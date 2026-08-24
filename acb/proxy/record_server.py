"""Zero-dependency recording proxy (stdlib only).

Runnable as a module:

    python -m acb.proxy.record_server --port 0 --usage-path runs/<id>/usage.jsonl \
        --run-id R --benchmark swebench --harness claude-code \
        --model claude-opus-4-8 --instance-id astropy__astropy-12345 \
        --upstream https://api.anthropic.com

It forwards ``/v1/messages`` (and any other path) to the upstream provider,
streaming the response back to the client while tee-ing the bytes to extract
Anthropic token usage from the SSE ``message_start`` / ``message_delta`` events
(or from the JSON body for non-streaming responses). One UsageRecord is appended
per request. The real provider key is read from ``ACB_UPSTREAM_API_KEY`` and
injected; the client's placeholder key is dropped.

This backend is provider-specific (Anthropic Messages API) on purpose: it is the
guaranteed-correct reference used to validate the Praxis backend's numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from acb.usage import UsageRecord

_STATE = {
    "turn": 0,
    "lock": Lock(),
}
_TAGS: dict = {}
_UPSTREAM = "https://api.anthropic.com"
_USAGE_PATH = "usage.jsonl"
_API = "anthropic"  # api the backend speaks: anthropic | openai
_KEY_ENV = "ANTHROPIC_API_KEY"


def _next_turn() -> int:
    with _STATE["lock"]:
        t = _STATE["turn"]
        _STATE["turn"] += 1
        return t


def _parse_sse_usage(text: str) -> dict:
    """Accumulate usage across Anthropic SSE events."""
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "request_id": None,
    }
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "message_start":
            msg = evt.get("message", {})
            usage["request_id"] = msg.get("id")
            u = msg.get("usage", {})
            usage["input_tokens"] = u.get("input_tokens", 0)
            usage["cache_read_tokens"] = u.get("cache_read_input_tokens", 0)
            usage["cache_creation_tokens"] = u.get("cache_creation_input_tokens", 0)
            usage["output_tokens"] = u.get("output_tokens", 0)
        elif etype == "message_delta":
            u = evt.get("usage", {})
            if "output_tokens" in u:
                usage["output_tokens"] = u["output_tokens"]  # cumulative
    return usage


def _parse_json_usage(body: bytes) -> dict:
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "request_id": None,
    }
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return usage
    usage["request_id"] = obj.get("id")
    u = obj.get("usage", {})
    usage["input_tokens"] = u.get("input_tokens", 0)
    usage["output_tokens"] = u.get("output_tokens", 0)
    usage["cache_read_tokens"] = u.get("cache_read_input_tokens", 0)
    usage["cache_creation_tokens"] = u.get("cache_creation_input_tokens", 0)
    return usage


def _parse_openai_usage(body: bytes, streamed: bool) -> dict:
    """OpenAI chat.completions usage. Cache fields are cloud-only -> left at 0.

    Non-streaming: `usage` on the response object.
    Streaming: usage arrives on the final chunk only when the client requested
    `stream_options.include_usage`; otherwise it is absent (0s recorded).
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,  # local OpenAI-compatible servers don't report cache
        "cache_creation_tokens": 0,
        "request_id": None,
    }
    if streamed:
        for line in body.decode("utf-8", "replace").splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage["request_id"] = evt.get("id") or usage["request_id"]
            u = evt.get("usage")
            if u:
                usage["input_tokens"] = u.get("prompt_tokens", 0)
                usage["output_tokens"] = u.get("completion_tokens", 0)
        return usage
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return usage
    usage["request_id"] = obj.get("id")
    u = obj.get("usage", {})
    usage["input_tokens"] = u.get("prompt_tokens", 0)
    usage["output_tokens"] = u.get("completion_tokens", 0)
    return usage


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence default logging
        pass

    def _record(self, usage: dict, endpoint: str, status: int, duration_ms: float):
        rec = UsageRecord(
            run_id=_TAGS["run_id"],
            benchmark=_TAGS["benchmark"],
            harness=_TAGS["harness"],
            model=_TAGS["model"],
            instance_id=_TAGS["instance_id"],
            turn_index=_next_turn(),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            request_id=usage.get("request_id"),
            endpoint=endpoint,
            status_code=status,
            duration_ms=duration_ms,
            ts=time.time(),
            source="recording",
        )
        with _STATE["lock"], open(_USAGE_PATH, "a", encoding="utf-8") as f:
            f.write(rec.to_json() + "\n")

    def do_GET(self):
        self._proxy(b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self._proxy(self.rfile.read(length) if length else b"")

    def _proxy(self, body: bytes):
        endpoint = self.path
        url = _UPSTREAM.rstrip("/") + endpoint
        # strip client (placeholder) credentials; the proxy injects the real key
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "x-api-key", "authorization")}
        upstream_key = os.environ.get(_KEY_ENV, "") if _KEY_ENV else ""
        if upstream_key:
            if _API == "anthropic":
                headers["x-api-key"] = upstream_key
            else:
                headers["Authorization"] = f"Bearer {upstream_key}"
        if _API == "anthropic":
            headers.setdefault("anthropic-version", "2023-06-01")

        req = urllib.request.Request(url, data=body or None, headers=headers,
                                     method=self.command)
        start = time.time()
        collected = bytearray()
        is_stream = False
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                status = resp.status
                ctype = resp.headers.get("Content-Type", "")
                is_stream = "text/event-stream" in ctype
                self.send_response(status)
                for k, v in resp.headers.items():
                    if k.lower() in ("transfer-encoding", "content-length", "connection"):
                        continue
                    self.send_header(k, v)
                if is_stream:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    collected.extend(chunk)
                    if is_stream:
                        self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                    else:
                        self.wfile.write(chunk)
                if is_stream:
                    self.wfile.write(b"0\r\n\r\n")
        except urllib.error.HTTPError as e:
            status = e.code
            err_body = e.read()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:  # noqa: BLE001
            self.send_response(502)
            msg = json.dumps({"error": str(e)}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        duration_ms = (time.time() - start) * 1000
        if _API == "anthropic" and endpoint.endswith("/messages"):
            usage = _parse_sse_usage(collected.decode("utf-8", "replace")) if is_stream \
                else _parse_json_usage(bytes(collected))
            self._record(usage, endpoint, status, duration_ms)
        elif _API == "openai" and ("/chat/completions" in endpoint or endpoint.endswith("/completions")):
            usage = _parse_openai_usage(bytes(collected), is_stream)
            self._record(usage, endpoint, status, duration_ms)


def main():
    global _UPSTREAM, _USAGE_PATH, _TAGS, _API, _KEY_ENV
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--usage-path", required=True)
    ap.add_argument("--upstream", default="https://api.anthropic.com")
    ap.add_argument("--api", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--key-env", default="ANTHROPIC_API_KEY")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--benchmark", required=True)
    ap.add_argument("--harness", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--port-file", help="write the bound port here once listening")
    args = ap.parse_args()

    _UPSTREAM = args.upstream
    _USAGE_PATH = args.usage_path
    _API = args.api
    _KEY_ENV = args.key_env
    _TAGS = {
        "run_id": args.run_id,
        "benchmark": args.benchmark,
        "harness": args.harness,
        "model": args.model,
        "instance_id": args.instance_id,
    }
    os.makedirs(os.path.dirname(_USAGE_PATH) or ".", exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    bound_port = server.server_address[1]
    if args.port_file:
        with open(args.port_file, "w") as f:
            f.write(str(bound_port))
    print(f"listening on 127.0.0.1:{bound_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
