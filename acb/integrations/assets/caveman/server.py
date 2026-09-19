"""Trial-local Caveman engine gateway; only successful repetitive logs are eligible.

No provider credentials are stored here. Praxis owns upstream authentication and
provider usage. Responses, SSE events, tool IDs and cache metadata are opaque.
"""
from copy import deepcopy
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

ENGINE = "/usr/local/bin/caveman-engine"
EVIDENCE = Path("/data/evidence.jsonl")
LOCK = threading.Lock()
UPSTREAM_PORT = 18880
MAX_BODY = 64 * 1024 * 1024
LOG_LINE = re.compile(r"^(?:\d{4}-\d\d-\d\d[T ][\d:.+Z-]+\s+)?\[?INFO\]?\s")
ERROR = re.compile(r"(error|exception|fail(?:ed|ure)?|traceback|panic|fatal)", re.I)


def record(value):
    with LOCK:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        with EVIDENCE.open("a") as file:
            file.write(json.dumps(value) + "\n")


def eligible(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return len(text) >= 2048 and len(lines) >= 8 and not ERROR.search(text) and all(LOG_LINE.match(line) for line in lines)


def compress(text, runner=subprocess.run):
    mode_path = Path("/data/mode")
    mode = mode_path.read_text().strip() if mode_path.exists() else os.environ.get("ACB_CAVEMAN_MODE", "record")
    if not eligible(text) or mode != "compress":
        record({"status": "passthrough", "reason": "record_mode_or_ineligible", "basis": "policy"})
        return text
    original = text.encode()
    try:
        with LOCK:
            response = runner([ENGINE, "compress", "--type", "log"], input=original, capture_output=True, timeout=30, check=True)
            metadata = json.loads(response.stderr)
            handle = metadata.get("recovery_handle")
            if response.stdout == original:
                result = text
                status = "passthrough"
            elif handle and re.fullmatch(r"[A-Za-z0-9:_.-]+", handle):
                recovery = runner([ENGINE, "retrieve", handle], capture_output=True, timeout=10, check=True)
                if recovery.stdout != original:
                    raise ValueError("recovery bytes did not match")
                result = response.stdout.decode() + f"\n[Original available: acb-recall {handle}]\n"
                if len(result.encode()) >= len(original):
                    result = text
                status = "compressed" if result != text else "passthrough"
            else:
                raise ValueError("transform without a recovery handle")
        record({"status": status, "original_sha256": hashlib.sha256(original).hexdigest(),
                "engine": metadata, "basis": "engine estimates; provider usage recorded by Praxis"})
        return result
    except Exception as error:
        record({"status": "passthrough", "reason": type(error).__name__, "recovery_verified": False})
        return text


def transform(body: bytes):
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return body
    if not isinstance(payload, dict):
        return body
    if not isinstance(payload.get("messages", []), list):
        return body
    transformed = deepcopy(payload)
    for message in transformed.get("messages", []):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if message.get("role") == "tool" and isinstance(content, str):
            # Require explicit successful status, not an inference from missing errors.
            try:
                value = json.loads(content)
            except ValueError:
                continue
            if isinstance(value, dict) and type(value.get("exit_code")) is int and value["exit_code"] == 0 and isinstance(value.get("stdout"), str):
                value["stdout"] = compress(value["stdout"])
                if value["stdout"] != json.loads(content)["stdout"]:
                    message["content"] = json.dumps(value)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") is False:
                    if isinstance(block.get("content"), str):
                        block["content"] = compress(block["content"])
                    elif isinstance(block.get("content"), list):
                        for part in block["content"]:
                            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                                part["text"] = compress(part["text"])
    if transformed == payload:
        return body
    return json.dumps(transformed, ensure_ascii=False).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ready")
            return
        if url.path == "/retrieve":
            handle = parse_qs(url.query).get("handle", [""])[0]
            if not re.fullmatch(r"[A-Za-z0-9:_.-]{1,256}", handle):
                self.send_error(400)
                return
            result = subprocess.run([ENGINE, "retrieve", handle], capture_output=True, timeout=10)
            self.send_response(200 if result.returncode == 0 else 404)
            self.end_headers()
            self.wfile.write(result.stdout if result.returncode == 0 else b"unknown recovery handle")
            record({"status": "retrieved", "handle": handle, "success": result.returncode == 0})
            return
        self.forward(b"")

    def do_POST(self):
        if self.headers.get("Transfer-Encoding"):
            self.send_error(411, "Content-Length required")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(400, "Incomplete request body")
            return
        if self.headers.get("Content-Encoding", "identity") == "identity":
            body = transform(body)
        self.forward(body)

    def forward(self, body):
        upstream = HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=1800)
        omitted = {"host", "content-length", "connection", "transfer-encoding"}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in omitted}
        headers["Content-Length"] = str(len(body))
        done = threading.Event()
        watcher = None
        try:
            upstream.request(self.command, self.path, body, headers)
            upstream_socket = upstream.sock

            def watch_disconnect():
                # read1 may be waiting on a quiet SSE stream. Closing the client
                # must interrupt that read, even when no next event ever arrives.
                while not done.wait(0.1):
                    try:
                        ready, _, _ = select.select([self.connection], [], [], 0)
                        if ready and self.connection.recv(1, socket.MSG_PEEK) == b"":
                            upstream_socket.shutdown(socket.SHUT_RDWR)
                            return
                    except OSError:
                        return

            watcher = threading.Thread(target=watch_disconnect, daemon=True)
            watcher.start()
            response = upstream.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding"}:
                    self.send_header(key, value)
            self.end_headers()
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client cancellation closes the upstream connection below.
        finally:
            done.set()
            upstream.close()
            if watcher is not None:
                watcher.join(timeout=1)


if __name__ == "__main__":
    if os.environ.get("ACB_CAVEMAN_MODE", "record") not in ("record", "compress"):
        raise SystemExit("invalid Caveman mode")
    subprocess.run([ENGINE, "registry"], check=True, capture_output=True)
    ThreadingHTTPServer(("127.0.0.1", 18881), Handler).serve_forever()
