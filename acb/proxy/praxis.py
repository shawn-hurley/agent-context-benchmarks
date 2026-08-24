"""PraxisBackend -- uses the Praxis (https://praxis.fast) Rust gateway.

Per instance run we generate a Praxis YAML with:

* a listener on an ephemeral port (the harness's ANTHROPIC_BASE_URL),
* an observability chain: ``request_id`` + ``access_log`` (structured JSON),
* an AI routing chain: ``json_body_field`` (model -> X-Model),
  ``credential_injection`` (real keys from env, client key stripped),
  ``router`` and ``load_balancer`` to the model backend.

Praxis writes access-log lines to stdout, which we capture and parse into
:class:`UsageRecord` rows.

NOTE: verified against praxis 0.5.3 (real binary, not just docs):

* Praxis has no built-in Anthropic<->OpenAI translation filter (no
  ``anthropic_to_openai`` filter exists). Cross-API runs (harness speaks one
  API, model backend speaks the other) are therefore rejected up front by
  ``build_config`` -- pick a harness/model pair with matching APIs, or use the
  ``recording`` backend (also same-API only).
* There is no ``token_usage_headers`` filter either; token counts are parsed
  straight out of whatever fields real ``access_log`` lines contain (see
  ``TOKEN_FIELD_CANDIDATES`` / ``_extract_tokens`` below -- returns None, i.e.
  no usage row, if none of the candidate keys are present). Cross-check totals
  against RecordingProxyBackend.
* Endpoints on loopback/private addresses (any local model server) fail
  config validation with "resolves to a sensitive address" unless
  ``insecure_options.allow_private_endpoints: true`` is set; ``build_config``
  sets this automatically when the model endpoint is private.

Container mode uses ``praxis-ai`` (https://github.com/praxis-proxy/ai)
instead of core praxis -- the ``token_count`` filter that actually computes
token usage only ships there. Two things verified live against the real
binary (not just docs), both real gaps in this alpha software:

* ``token_count`` + ``access_log`` in the wrong declared order **crashes
  every POST request** (``ConnectionClosed... Prematurely before response
  header is sent``). Response-phase hooks run in *reverse* declared order,
  so ``token_count`` must be declared *before* ``access_log`` for both to
  coexist safely -- confirmed by bisecting filter-by-filter against a live
  container.
* ``token_usage_headers`` (the documented way to expose token counts as
  ``Praxis-Token-*`` response headers) never actually fires for any
  non-streaming JSON response, from any backend -- confirmed with both vLLM
  and a synthetic backend returning a clean, correctly-shaped response.
  Root cause (via ``RUST_LOG=trace``): ``token_count`` only extracts counts
  in ``on_response_body``; ``token_usage_headers`` checks for them in
  ``on_response`` (the header phase, which necessarily runs first), so the
  data doesn't exist yet when it looks. The extraction itself works
  correctly (confirmed: ``extracted token usage from JSON response
  input=13 output=2 total=15``, matching vLLM's own ``usage`` block
  exactly) -- it's just never exposed via headers. Filed upstream; worked
  around here by parsing that DEBUG-level log line directly instead of
  relying on header injection (see ``PraxisContainerBackend`` and
  ``_TOKEN_USAGE_LOG_RE`` below).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import yaml

from acb.container import (
    container_cp_in,
    container_create,
    container_exec_capture,
    container_logs,
    container_start,
    container_stop_rm,
)
from acb.proxy.base import ProxyBackend
from acb.usage import UsageRecord, write_records

# Podman's gvproxy-based user-mode networking resolves this to the macOS
# host's own network stack, including loopback services -- verified against
# Podman 6.1.0 with `UserModeNetworking: true`. This is how a container
# reaches a model server bound to 127.0.0.1 on the Mac host.
CONTAINER_HOST_GATEWAY = "host.containers.internal"

# candidate access-log keys -> our field. First present key wins.
TOKEN_FIELD_CANDIDATES = {
    "input_tokens": ["input_tokens", "praxis_token_input", "prompt_tokens"],
    "output_tokens": ["output_tokens", "praxis_token_output", "completion_tokens"],
    "cache_read_tokens": ["cache_read_input_tokens", "praxis_token_cache_read"],
    "cache_creation_tokens": ["cache_creation_input_tokens", "praxis_token_cache_write"],
}

# The token_count filter's own DEBUG-level lines (only emitted with RUST_LOG
# including this target; see PraxisContainerBackend.start()) are the one
# place its extracted counts are actually observable, since
# token_usage_headers never fires for non-streaming responses (verified
# live -- see module docstring). PRAXIS_LOG_FORMAT=json (also set in
# start()) gets this as real JSON instead of the human-readable `tracing`
# console format (ANSI color codes) core praxis/praxis-ai use by default --
# confirmed live: `{"level":"DEBUG","fields":{"message":"extracted token
# usage from JSON response","input":13,"output":2,"total":15},
# "target":"praxis_ai_filters::token_usage::count"}`.
#
# No request_id correlation: checked the `http_request` tracing span's own
# recorded fields directly (otel.name, http.request.method, url.path, ...)
# -- request_id genuinely isn't one of them, so it can't propagate via span
# context onto token_count's log line the way it does onto access_log's
# (which must read it from somewhere else, e.g. filter_metadata, not the
# span). Correlation here is therefore sequential (turn_index = arrival
# order), same assumption the rest of this file already makes for CLI
# harnesses that can't inject correlation headers -- see acb/proxy/base.py.
_TOKEN_USAGE_TARGET = "praxis_ai_filters::token_usage::count"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_private_endpoint(endpoint: str) -> bool:
    """True if ``host:port`` resolves to a loopback/private address.

    Praxis rejects load_balancer clusters pointing at such addresses unless
    ``insecure_options.allow_private_endpoints`` is set (SSRF guard) -- this is
    always the case for locally-served models.
    """
    host = endpoint.rsplit(":", 1)[0].strip("[]")
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # hostname we can't classify (e.g. a real DNS name) -> assume public
    return ip.is_loopback or ip.is_private


# praxis-ai's token_count filter: openai | anthropic | google | bedrock |
# bedrock_invoke_model | azure. Our two supported harness/model APIs map
# directly onto its first two.
_TOKEN_COUNT_PROVIDERS = {"openai", "anthropic"}


def build_config(port: int, model_spec, harness_api: str, include_token_count: bool = False) -> dict:
    """Generate a Praxis config for one model backend the proxy owns.

    The proxy exposes the harness's API surface inbound, connects to the single
    model backend outbound, and injects the backend's key (if any).

    Praxis (verified against 0.5.3) has no Anthropic<->OpenAI translation
    filter, so the harness and backend must speak the same API -- raise early
    with a clear message instead of generating a config Praxis will reject.

    ``include_token_count`` only makes sense for a praxis-ai build (core
    praxis has no such filter and would fail to start on an unknown filter
    name) -- see ``build_container_config()``.
    """
    if harness_api != model_spec.api:
        raise ValueError(
            f"praxis backend cannot translate {harness_api!r} (harness) <-> "
            f"{model_spec.api!r} (model {model_spec.name!r}): this Praxis build has "
            "no anthropic<->openai translation filter. Pick a model whose `api` in "
            "proxy.yaml matches the harness, or use a harness that speaks the "
            "model's API."
        )
    ai_filters: list[dict] = [
        {"filter": "json_body_field", "field": "model", "header": "X-Model"},
    ]

    # credential injection only for backends that need a key (skip local/keyless)
    if model_spec.key_env:
        if model_spec.api == "anthropic":
            cred = {"name": "model", "header": "x-api-key",
                    "env_var": model_spec.key_env, "strip_client_credential": True}
        else:
            cred = {"name": "model", "header": "Authorization", "header_prefix": "Bearer ",
                    "env_var": model_spec.key_env, "strip_client_credential": True}
        ai_filters.append({"filter": "credential_injection", "clusters": [cred]})
    else:
        # still strip any client credential so a placeholder key never leaks upstream
        ai_filters.append({"filter": "credential_injection",
                           "clusters": [{"name": "model", "header": "Authorization",
                                         "value": "", "strip_client_credential": True}]})

    # Route the whole /v1/ surface to the single backend cluster, not just the
    # one chat/completions-style endpoint -- harnesses also hit e.g. GET
    # /v1/models (goose does this at startup for model metadata) which used to
    # 404 with a narrower single-path route.
    ai_filters.append({
        "filter": "router",
        "routes": [{"path_prefix": "/v1/", "cluster": "model"}],
    })
    cluster = {"name": "model", "endpoints": [model_spec.endpoint]}
    if model_spec.tls:
        cluster["tls"] = {}
    ai_filters.append({"filter": "load_balancer", "clusters": [cluster]})

    observability_filters: list[dict] = [{"filter": "request_id"}]
    if include_token_count:
        # Must be declared *before* access_log: response-phase hooks run in
        # reverse declared order, and token_count + access_log in the wrong
        # order crashes every POST request (verified live -- see module
        # docstring). Provider defaults to openai for anything we don't have
        # a direct mapping for (token_count doesn't support "not tracking
        # usage", only a fixed provider list).
        provider = model_spec.api if model_spec.api in _TOKEN_COUNT_PROVIDERS else "openai"
        observability_filters.append({"filter": "token_count", "provider": provider})
    observability_filters.append({"filter": "access_log", "sample_rate": 1.0})

    config: dict = {
        "listeners": [
            {"name": "acb", "address": f"127.0.0.1:{port}",
             "filter_chains": ["observability", "ai-routing"]}
        ],
        "filter_chains": [
            {"name": "observability", "filters": observability_filters},
            {"name": "ai-routing", "filters": ai_filters},
        ],
    }
    if _is_private_endpoint(model_spec.endpoint):
        # local model servers (vLLM/Ollama/LM Studio on 127.0.0.1 etc.) trip
        # Praxis's SSRF guard on load_balancer clusters; opt in explicitly.
        config["insecure_options"] = {"allow_private_endpoints": True}
    return config


def _container_endpoint(endpoint: str) -> str:
    """Rewrite a loopback/private endpoint to be reachable from a container.

    ``127.0.0.1``/``localhost`` inside a container refers to the container's
    own network namespace, not the Mac host -- a model server bound to the
    host's loopback needs to be addressed via the Podman machine's host
    gateway DNS name instead.
    """
    host, sep, port = endpoint.rpartition(":")
    if not sep:
        return endpoint
    if host in ("127.0.0.1", "localhost", "::1") or host.startswith("0."):
        return f"{CONTAINER_HOST_GATEWAY}:{port}"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return endpoint
    return f"{CONTAINER_HOST_GATEWAY}:{port}" if ip.is_loopback else endpoint


def build_container_config(port: int, model_spec, harness_api: str) -> dict:
    """Like :func:`build_config`, for a praxis-ai process running inside a container."""
    container_spec = replace(model_spec, endpoint=_container_endpoint(model_spec.endpoint))
    config = build_config(port, container_spec, harness_api, include_token_count=True)
    # The listener itself is fine on 127.0.0.1: the harness reaches it from a
    # sibling container in the same pod (shared network namespace), so the
    # pod's own loopback is the right address on both ends.
    #
    # build_config()'s own private-endpoint check is a literal string/IP
    # match (`_is_private_endpoint`), which doesn't catch this case: the
    # config carries the *hostname* `host.containers.internal`, but Praxis
    # resolves it at request time to gvproxy's gateway address (a private
    # 192.168.x.x address, verified: 192.168.127.254) and trips its SSRF
    # guard on the resolved IP regardless of what the config string looked
    # like. Container-mode always talks back to the host machine by design,
    # so this is always the intended target -- opt in unconditionally.
    config["insecure_options"] = {"allow_private_endpoints": True}
    return config


class PraxisBackend(ProxyBackend):
    name = "praxis"

    def start(self) -> str:
        binary = self.config.get("binary", "praxis")
        port = _free_port()
        workdir = self.usage_path.parent / "praxis" / self.tags.instance_id
        workdir.mkdir(parents=True, exist_ok=True)
        cfg_path = workdir / "praxis.yaml"
        self._log_path = workdir / "praxis.stdout.jsonl"
        with cfg_path.open("w") as f:
            yaml.safe_dump(build_config(port, self.model_spec, self.harness_api),
                           f, sort_keys=False)

        self._logf = self._log_path.open("w")
        self._proc = subprocess.Popen(
            [binary, "-c", str(cfg_path)],
            stdout=self._logf,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 15
        started = False
        while time.time() < deadline:
            if self._proc.poll() is not None:
                break  # process exited early -- definitely not started
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    started = True
                    break
            time.sleep(0.1)
        if not started:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._logf.close()
            log_tail = self._log_path.read_text(errors="replace").strip()
            detail = f"\n--- praxis log ({self._log_path}) ---\n{log_tail}" if log_tail else \
                " (no output captured; is the binary on PATH?)"
            raise RuntimeError(f"praxis failed to start.{detail}")
        self._base_url = f"http://127.0.0.1:{port}"
        return self._base_url

    def stop(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if getattr(self, "_logf", None):
            self._logf.close()
        self._parse_access_log()

    def _extract_tokens(self, entry: dict) -> dict | None:
        out = {}
        found = False
        for field, candidates in TOKEN_FIELD_CANDIDATES.items():
            out[field] = 0
            for key in candidates:
                if key in entry:
                    out[field] = int(entry[key] or 0)
                    found = True
                    break
        return out if found else None

    def _parse_access_log(self) -> None:
        if not getattr(self, "_log_path", None) or not self._log_path.exists():
            return
        records: list[UsageRecord] = []
        turn = 0
        for line in self._log_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = entry.get("path", entry.get("uri", ""))
            if "/messages" not in path and "/chat/completions" not in path:
                continue
            tokens = self._extract_tokens(entry)
            if tokens is None:
                continue
            records.append(
                UsageRecord(
                    run_id=self.tags.run_id,
                    benchmark=self.tags.benchmark,
                    harness=self.tags.harness,
                    model=self.tags.model,
                    instance_id=self.tags.instance_id,
                    turn_index=turn,
                    request_id=entry.get("request_id") or entry.get("x-request-id"),
                    endpoint=path,
                    status_code=entry.get("status"),
                    duration_ms=entry.get("duration_ms"),
                    ts=entry.get("ts"),
                    source="praxis",
                    **tokens,
                )
            )
            turn += 1
        write_records(self.usage_path, records)


class PraxisContainerBackend(PraxisBackend):
    """Runs Praxis as a sibling container in the same pod as the testbed.

    Config/log transfer uses `podman cp`, not bind mounts: this Podman
    machine (AppleHV backend on macOS) has no host directory shared into the
    VM by default, so `-v <hostpath>:...` fails inside the VM even for paths
    that exist on the Mac host.

    Not registered in the `make_backend()` factory (acb/proxy/__init__.py):
    it needs a `pod`/`image` that only exist in the runner's per-instance
    orchestration (acb/runner.py's `do_one`), unlike `RecordingProxyBackend`
    which is fully described by (tags, usage_path, config, model_spec,
    harness_api) alone -- and has no container-mode implementation at all.
    """

    name = "praxis-container"
    CONTAINER_PORT = 8080
    HEALTH_TIMEOUT = 20

    def __init__(self, *args, pod: str, image: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.pod = pod
        self.image = image
        self.container_name = f"{pod}-praxis"

    def start(self) -> str:
        workdir = self.usage_path.parent / "praxis" / self.tags.instance_id
        workdir.mkdir(parents=True, exist_ok=True)
        cfg_path = workdir / "praxis.container.yaml"
        self._log_path = workdir / "praxis.container.log"
        with cfg_path.open("w") as f:
            yaml.safe_dump(
                build_container_config(self.CONTAINER_PORT, self.model_spec, self.harness_api),
                f, sort_keys=False,
            )

        # token_count's own extraction only logs at DEBUG (see module
        # docstring for why this is the only way to actually observe
        # token counts -- token_usage_headers never fires). RUST_LOG is
        # scoped to this one target, not a blanket `debug`, since
        # praxis-ai's core-praxis dependency logs plenty of unrelated
        # per-connection DEBUG/TRACE noise (mio, pingora) that would
        # otherwise dominate this file. PRAXIS_LOG_FORMAT=json gets
        # structured, reliably-parseable lines instead of the human-
        # readable `tracing` console format (ANSI codes) used by default.
        # praxis-ai's own image has a bare `ENTRYPOINT ["praxis-ai"]` -- no
        # baked-in config path (unlike core praxis's image, which COPYs a
        # default config and needs no command args at all). Without this,
        # praxis-ai starts with no config specified at all -- verified this
        # is exactly what caused two earlier symptoms that looked unrelated
        # (a transient-looking 404 "not found" on every request, and later a
        # health-check timeout with empty logs): every one of my manual
        # debugging sessions happened to work because I always typed
        # `-c /etc/praxis/config.yaml` by hand, while this real code path
        # never did.
        container_create(
            self.pod, self.image, self.container_name,
            command=["-c", "/etc/praxis/config.yaml"],
            env={
                "RUST_LOG": "praxis_ai_filters::token_usage=debug",
                "PRAXIS_LOG_FORMAT": "json",
            },
        )
        container_cp_in(self.container_name, cfg_path, "/etc/praxis/config.yaml")
        container_start(self.container_name)

        # A bare TCP-port check isn't enough: praxis-ai loads its config via
        # a file watcher (`praxis_ai::watcher: config file watcher started`),
        # and the listener port opens before that load necessarily
        # completes. Verified live: a request landing in that gap gets a
        # bare 404 `{"error":"not found"}` from *some* default/empty
        # pipeline, not a connection failure -- indistinguishable from a
        # real routing bug unless the actual route is exercised. Polling a
        # real request through the configured route (not just the listener
        # socket) means "started" actually means "ready to route".
        deadline = time.time() + self.HEALTH_TIMEOUT
        started = False
        while time.time() < deadline:
            try:
                container_exec_capture(
                    self.container_name,
                    ["wget", "-qO-", "--timeout=2",
                     f"http://127.0.0.1:{self.CONTAINER_PORT}/v1/models"],
                )
                started = True
                break
            except RuntimeError:
                time.sleep(0.3)
        if not started:
            log_tail = container_logs(self.container_name)
            if os.environ.get("ACB_DEBUG_KEEP_CONTAINERS"):
                print(f"[debug] ACB_DEBUG_KEEP_CONTAINERS set -- leaving failed "
                      f"praxis container={self.container_name} running", flush=True)
            else:
                container_stop_rm(self.container_name)
            raise RuntimeError(
                f"praxis container failed to start.\n--- logs ---\n{log_tail}"
            )
        self._base_url = f"http://127.0.0.1:{self.CONTAINER_PORT}"
        return self._base_url

    def stop(self) -> None:
        self._log_path.write_text(container_logs(self.container_name))
        container_stop_rm(self.container_name)
        self._parse_token_usage_log()

    def _parse_token_usage_log(self) -> None:
        """Parse token_count's DEBUG log lines (JSON, via PRAXIS_LOG_FORMAT)
        into UsageRecords -- not access_log (it has no field for arbitrary
        filter metadata) and not token_usage_headers (never fires for
        non-streaming responses); see module docstring for both.
        """
        if not getattr(self, "_log_path", None) or not self._log_path.exists():
            return
        records: list[UsageRecord] = []
        turn = 0
        for line in self._log_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("target") != _TOKEN_USAGE_TARGET:
                continue
            fields = entry.get("fields", {})
            if "input" not in fields and "output" not in fields:
                continue
            input_tokens = int(fields.get("input", 0))
            output_tokens = int(fields.get("output", 0))
            if "total" in fields and int(fields["total"]) != input_tokens + output_tokens:
                print(f"[praxis-ai] warning: token_count total={fields['total']} != "
                      f"input+output={input_tokens + output_tokens}", flush=True)
            records.append(
                UsageRecord(
                    run_id=self.tags.run_id,
                    benchmark=self.tags.benchmark,
                    harness=self.tags.harness,
                    model=self.tags.model,
                    instance_id=self.tags.instance_id,
                    turn_index=turn,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    source="praxis-ai",
                )
            )
            turn += 1
        write_records(self.usage_path, records)
