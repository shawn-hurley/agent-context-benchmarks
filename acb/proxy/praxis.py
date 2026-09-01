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

* Core praxis has no built-in Anthropic<->OpenAI translation filter (no
  ``anthropic_to_openai`` filter exists there). Cross-API runs (harness
  speaks one API, model backend speaks the other) are therefore rejected up
  front by ``build_config`` unless praxis-ai's translation chain applies --
  see "Anthropic<->OpenAI translation" below. The ``recording`` backend has
  no translation at all (same-API only, always).
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
  ``_TOKEN_USAGE_TARGET`` below).
* Getting ``token_count``'s DEBUG line to appear at all needs
  ``runtime.log_overrides`` in the config, not a ``RUST_LOG`` env var:
  ``RUST_LOG=<target>=debug`` *replaces* the whole filter (no implicit
  ``info`` fallback for unlisted targets), which silently suppressed
  ``access_log``'s own normal ``INFO`` output as a side effect (confirmed:
  0 access_log lines captured with that approach). ``runtime.log_overrides``
  merges with the default ``info`` base instead of replacing it.
* Real per-request correlation between ``access_log`` and ``token_count``
  *is* possible via ``request_id`` -- but only once the ``request_id``
  filter is actually in the chain (it always is in ``build_config()``'s
  ``observability`` chain here). Confirmed live, in JSON format:
  ``access_log``'s own ``fields.request_id`` and ``token_count``'s
  ``span.request_id`` carry the identical value for the same request.

Anthropic<->OpenAI translation (praxis-ai only, one direction):

praxis-ai (https://github.com/praxis-proxy/ai) ships
``anthropic_messages_format`` + ``anthropic_to_openai`` + a path rewrite to
translate an Anthropic Messages-speaking harness onto an OpenAI Chat
Completions-speaking backend (e.g. claude-code against a local vLLM/Ollama
server) -- see its own ``examples/configs/anthropic/messages-to-openai.yaml``.
Only that one direction (``harness_api="anthropic"``, ``model_spec.api=
"openai"``) is wired up here; the reverse (an OpenAI-speaking harness like
goose against a real Anthropic backend) isn't implemented since no current
harness needs it -- ``build_config`` still raises for every other mismatch.

Getting ``token_count`` to see the *untranslated* bytes took care: it must
run, in response order, *before* ``anthropic_to_openai``/
``anthropic_stream_events`` rewrite the body into the client's wire shape --
otherwise it parses the wrong format (configured ``provider`` matches the
backend's native shape, i.e. ``model_spec.api``) and silently extracts
nothing. Response-phase hooks run in reverse *declared* order, so this means
declaring ``token_count`` *after* the translation filters. The non-translated
path's ``token_count``/``access_log`` pair normally lives in a separate
``observability`` filter chain that's always declared (and therefore always
*runs*, response-wise) before ``ai-routing`` -- which would put them on the
wrong side of the translation filters. For the translated case only,
``build_config`` instead appends them to the end of ``ai_filters`` itself
(after the translation filters, before ``load_balancer``), keeping their
relative order (``token_count`` before ``access_log``, declared-order) that
avoids the crash bug documented above.

Verified live against the real ``acb-praxis-ai:latest`` image and a real
local vLLM backend (not just docs): a real ``POST /v1/messages`` (both
``stream: false`` and ``stream: true``) round-tripped correctly -- real
Anthropic Messages request in, correctly-shaped Anthropic response
(non-streaming JSON and ``message_start``/``content_block_delta``/
``message_stop`` SSE events for streaming) out, with the actual completion
text intact end to end. ``token_count`` extracted the correct, matching
counts in both cases (confirmed via its own DEBUG line, correlated to
``access_log`` by ``request_id``), and ``acb``'s real
``PraxisContainerBackend._parse_usage_log()`` turned that into correct
``UsageRecord`` rows.

One real bug found and fixed by this manual test: without scoping the
translation filters to ``/v1/messages`` via ``conditions``,
``anthropic_to_openai`` unconditionally wrapped *every* response --
including a plain ``GET /v1/models`` model-list, which the router also
sends to the same cluster -- into a bogus, empty Anthropic "message" shape,
corrupting a response it was never meant to touch. All four translation
filters (``anthropic_messages_format``, ``anthropic_to_openai``,
``anthropic_stream_events``, ``path_rewrite``) are scoped to
``path_prefix: /v1/messages`` in ``build_config`` accordingly; ``GET
/v1/models`` now passes through untouched, confirmed live.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import yaml

from acb.container import (
    container_cp_in,
    container_cp_out,
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

# The token_count filter's own DEBUG-level lines (only emitted with
# `runtime.log_overrides` including this target at debug; see
# build_container_config()) are the one place its extracted counts are
# actually observable, since token_usage_headers never fires for
# non-streaming responses (verified live -- see module docstring).
# PRAXIS_LOG_FORMAT=json (set in PraxisContainerBackend.start()) gets this
# as real JSON instead of the human-readable `tracing` console format (ANSI
# color codes) core praxis/praxis-ai use by default -- confirmed live:
# `{"level":"DEBUG","fields":{"message":"extracted token usage from JSON
# response","input":13,"output":2,"total":15},
# "target":"praxis_ai_filters::token_usage::count","span":{...,
# "request_id":"..."}}`.
#
# request_id correlation with access_log's own `fields.request_id` *is*
# possible via this line's `span.request_id` (confirmed live, identical
# value for the same request) -- but only because `request_id` is always in
# build_config()'s `observability` chain here.


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


def build_config(port: int, model_spec, harness_api: str, include_token_count: bool = False) -> dict:
    """Generate a Praxis config for one model backend the proxy owns.

    The proxy exposes the harness's API surface inbound, connects to the single
    model backend outbound, and injects the backend's key (if any).

    Vertex AI Anthropic backends (``model_spec.is_vertex``) use a single
    filter chain with Vertex-specific filters: ``vertex_anthropic_prepare``
    (body rewrite), ``headers`` (Host header), path rewrites for rawPredict,
    and GCP OAuth2 credential injection.

    For all other backends, only one cross-API direction is translatable: an
    Anthropic-speaking harness (claude-code) against an OpenAI-speaking backend
    (a local vLLM/Ollama server), via praxis-ai's ``anthropic_messages_format``
    / ``anthropic_to_openai`` / ``anthropic_stream_events`` filters -- see the
    module docstring's "Anthropic<->OpenAI translation" section. Every other
    mismatch (including the reverse direction) still raises early with a
    clear message instead of generating a config Praxis will reject.

    ``include_token_count`` enables the ``benchmark_metrics`` filter which only
    makes sense for a praxis-ai build (core praxis has no such filter and would
    fail to start on an unknown filter name) -- see ``build_container_config()``.
    """
    # Fresh list per use -- sharing the same object causes yaml.safe_dump to
    # emit YAML anchors/aliases (&id001/*id001) which praxis rejects at parse time.
    def _messages_only() -> list[dict]:
        return [{"when": {"path_prefix": "/v1/messages"}}]

    if model_spec.is_vertex:
        # ===== Vertex AI path: single combined filter chain =====
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        region = os.environ.get("CLOUD_ML_REGION", "us-central1")
        vertex_path = (
            f"/v1/projects/{project}/locations/{region}"
            f"/publishers/anthropic/models/{model_spec.vertex_model}:rawPredict"
        )
        vertex_models_path = f"/v1/projects/{project}/locations/{region}/models"
        upstream_host = model_spec.endpoint.rsplit(":", 1)[0]  # aiplatform.googleapis.com

        filters: list[dict] = [
            {"filter": "access_log", "sample_rate": 1.0},
            {"filter": "request_id"},
            # Classify request format and promote routing facts to headers.
            {"filter": "anthropic_messages_format", "on_invalid": "continue"},
            # Promote model name to X-Model header for logging/metrics.
            # Must come BEFORE vertex_anthropic_prepare strips the model field.
            {"filter": "json_body_field", "field": "model", "header": "X-Model"},
            {"filter": "model_to_header"},
            # Body rewrite: remove `model` field and inject anthropic_version.
            {"filter": "vertex_anthropic_prepare", "conditions": _messages_only()},
            # Explicitly set the Host header to the upstream hostname.
            # Without this praxis forwards the client's original Host
            # (e.g. "127.0.0.1:8080") and Google returns a generic 404.
            {"filter": "headers", "request_set": [
                {"name": "Host", "value": upstream_host},
                # Prevent gzip compression so our SSE filter can parse and
                # strip vertex_event events from the plain-text response body.
                # Without this Vertex sends Content-Encoding: gzip and the
                # filter receives binary bytes it cannot process as UTF-8 SSE.
                {"name": "Accept-Encoding", "value": "identity"},
            ]},
            # Rewrite /v1/messages to the Vertex rawPredict path.
            {"filter": "path_rewrite", "replace": {
                "pattern": "^/v1/messages$", "replacement": vertex_path
            }},
            {"filter": "path_rewrite", "replace": {
                "pattern": "^/v1/models$", "replacement": vertex_models_path
            }, "allow_rewrite_override": True},
            {"filter": "router", "routes": [
                {"path_prefix": "/v1/", "cluster": "vertex_ai_global"},
            ]},
            # Inject the GCP Bearer token AFTER router selects the cluster.
            {"filter": "credential_injection", "clusters": [{
                "name": "vertex_ai_global", "header": "Authorization",
                "env_var": "GCP_ACCESS_TOKEN", "header_prefix": "Bearer ",
                "strip_client_credential": True
            }]},
        ]

        # Extract tokens from Anthropic SSE responses.
        # Must come BEFORE benchmark_metrics so benchmark_metrics can read from metadata.
        filters.append({"filter": "token_count", "provider": "anthropic", "conditions": _messages_only()})

        # Collect comprehensive metrics including all token types, timing, and sizes.
        # Writes to /tmp/benchmark_metrics.jsonl for direct consumption (no log parsing).
        # Also strips vertex_event from SSE streams to prevent SDK validation errors.
        # Stores extracted tokens to metadata for downstream token_usage_to_metrics filter.
        filters.append({"filter": "benchmark_metrics", "conditions": _messages_only()})

        # Write metrics to file with deduplication by request_id.
        # Reads token counts from filter_metadata (written by token_count filter above or benchmark_metrics).
        filters.append({"filter": "token_usage_to_metrics", "conditions": _messages_only()})

        # Create separate cluster dicts to avoid YAML aliases (&id001/*id001)
        # when yaml.safe_dump sees the same object reused multiple times.
        filters.append({"filter": "load_balancer", "clusters": [{
            "name": "vertex_ai_global",
            "endpoints": ["aiplatform.googleapis.com:443"],
            "tls": {"sni": "aiplatform.googleapis.com"}
        }]})

        return {
            "listeners": [
                {"name": "acb", "address": f"127.0.0.1:{port}",
                 "filter_chains": ["vertex_pipeline"]}
            ],
            "clusters": [{
                "name": "vertex_ai_global",
                "endpoints": ["aiplatform.googleapis.com:443"],
                "tls": {"sni": "aiplatform.googleapis.com"}
            }],
            "filter_chains": [
                {"name": "vertex_pipeline", "filters": filters}
            ],
            "insecure_options": {"allow_private_endpoints": True},
        }

    # ===== Local model path: separate observability + ai-routing chains =====
    translate = harness_api != model_spec.api
    if translate and not (harness_api == "anthropic" and model_spec.api == "openai"):
        raise ValueError(
            f"praxis backend cannot translate {harness_api!r} (harness) <-> "
            f"{model_spec.api!r} (model {model_spec.name!r}): only "
            "anthropic-harness -> openai-backend translation is wired up "
            "(see build_config()'s docstring / the module docstring's "
            "'Anthropic<->OpenAI translation' section). Pick a model whose "
            "`api` in proxy.yaml matches the harness, use a harness that "
            "speaks the model's API, or add the reverse direction."
        )

    ai_filters: list[dict] = []
    if translate:
        # Classifies the incoming Anthropic Messages request and promotes
        # its `stream` flag to metadata that anthropic_stream_events below
        # arms itself from later (see that filter's own docs: it activates
        # automatically off this metadata + a text/event-stream response,
        # no `response_conditions` needed).
        ai_filters.append({"filter": "anthropic_messages_format", "on_invalid": "continue",
                            "conditions": _messages_only()})

    ai_filters.append({"filter": "json_body_field", "field": "model", "header": "X-Model"})

    if translate:
        # Request-phase: rewrites the Anthropic Messages body into a Chat
        # Completions-shaped body. Response-phase: transforms a compatible
        # non-streaming JSON response back; streaming responses are instead
        # handled chunk-by-chunk by anthropic_stream_events (declared next,
        # so it runs *before* this filter's own on_response in reverse
        # order -- matching praxis-ai's own reference example).
        ai_filters.append({"filter": "anthropic_to_openai", "conditions": _messages_only()})
        ai_filters.append({"filter": "anthropic_stream_events", "conditions": _messages_only()})
        # Only rewrite the Anthropic-shaped endpoint -- leave anything else
        # under /v1/ (there shouldn't be anything else claude-code calls,
        # but no reason to rewrite a path anthropic_to_openai didn't touch).
        ai_filters.append({
            "filter": "path_rewrite",
            "replace": {"pattern": "^/v1/messages$", "replacement": "/v1/chat/completions"},
            "conditions": _messages_only(),
        })

    # Route the whole /v1/ surface to the single backend cluster, not just the
    # one chat/completions-style endpoint -- harnesses also hit e.g. GET
    # /v1/models (goose does this at startup for model metadata) which used to
    # 404 with a narrower single-path route. Router checks the rewritten path
    # first when path_rewrite set one, which still matches this prefix.
    ai_filters.append({
        "filter": "router",
        "routes": [{"path_prefix": "/v1/", "cluster": "local_model"}],
    })

    # credential injection only for backends that need a key (skip local/keyless)
    # MUST come AFTER router selects the cluster.
    if model_spec.key_env:
        if model_spec.api == "anthropic":
            cred = {"name": "local_model", "header": "x-api-key",
                    "env_var": model_spec.key_env, "strip_client_credential": True}
        else:
            cred = {"name": "local_model", "header": "Authorization", "header_prefix": "Bearer ",
                    "env_var": model_spec.key_env, "strip_client_credential": True}
        ai_filters.append({"filter": "credential_injection", "clusters": [cred]})
    else:
        # still strip any client credential so a placeholder key never leaks upstream
        ai_filters.append({"filter": "credential_injection",
                           "clusters": [{"name": "local_model", "header": "Authorization",
                                         "value": "", "strip_client_credential": True}]})

    # benchmark_metrics/access_log: for the non-translated case these live in the
    # separate `observability` chain below (always declared -- and so always
    # *running*, response-wise -- before `ai-routing`, which is fine since
    # nothing there mutates the body). For the translated case that ordering
    # is wrong: benchmark_metrics needs to see the untranslated (backend-native)
    # bytes, which means it must run, in response order, *before*
    # anthropic_to_openai/anthropic_stream_events rewrite them -- i.e.
    # declared *after* those filters (response hooks run in reverse declared
    # order). So for `translate`, append them here instead, keeping their
    # relative order (benchmark_metrics before access_log).
    trailing_filters: list[dict] = []
    if include_token_count:
        # Extract tokens from OpenAI-compatible (vLLM/Ollama) responses.
        # For non-translated Anthropic->OpenAI requests, the backend is native Anthropic,
        # so token_count: anthropic is used. For translated requests, the backend is OpenAI,
        # so token_count: openai is used. The router has already selected the cluster by
        # this point, so we have no way to conditionally apply the right provider.
        # Instead, we add both and let token_count be silent (graceful fallback) if the
        # response format doesn't match the provider.
        if model_spec.api == "openai":
            trailing_filters.append({"filter": "token_count", "provider": "openai"})
        elif model_spec.api == "anthropic":
            trailing_filters.append({"filter": "token_count", "provider": "anthropic"})

        # Use benchmark_metrics for comprehensive token tracking across all backends.
        # Writes to /tmp/benchmark_metrics.jsonl for reliable file-based collection.
        # Handles both OpenAI and Anthropic response formats automatically,
        # captures all token types (input, output, cache_read, cache_creation),
        # and includes timing/size data. Much more reliable than parsing logs.
        # Stores extracted tokens to metadata for downstream token_usage_to_metrics filter.
        trailing_filters.append({"filter": "benchmark_metrics"})

        # Write metrics to file with deduplication by request_id.
        # Reads token counts from filter_metadata (written by token_count filter above or benchmark_metrics).
        trailing_filters.append({"filter": "token_usage_to_metrics"})
    trailing_filters.append({"filter": "access_log", "sample_rate": 1.0})

    if translate:
        ai_filters.extend(trailing_filters)
        observability_filters: list[dict] = [{"filter": "request_id"}]
    else:
        observability_filters = [{"filter": "request_id"}, *trailing_filters]

    cluster = {"name": "local_model", "endpoints": [model_spec.endpoint]}
    if model_spec.tls:
        # Always set SNI explicitly to the upstream hostname so praxis uses
        # the correct server name regardless of the incoming Host header.
        upstream_host = model_spec.endpoint.rsplit(":", 1)[0]
        cluster["tls"] = {"sni": upstream_host}
    ai_filters.append({"filter": "load_balancer", "clusters": [cluster]})

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
    # Not a RUST_LOG env var: RUST_LOG=<target>=debug *replaces* the whole
    # filter (no implicit `info` fallback for unlisted targets), which
    # silently suppressed access_log's own normal INFO output as a side
    # effect (confirmed live: 0 access_log lines captured that way).
    # runtime.log_overrides merges with the default `info` base instead.
    config["runtime"] = {
        "log_overrides": {
            "praxis_filter": "debug",  # All praxis filter activity
            "praxis_vertex_anthropic::metrics_collector": "debug",  # Metrics collection filter
        }
    }
    return config


class PraxisBackend(ProxyBackend):
    name = "praxis"

    def start(self) -> str:
        binary = self.config.get("binary", "praxis")
        port = _free_port()
        # usage_path is already instances/{test_id}/usage.jsonl
        # So usage_path.parent is the per-instance directory
        workdir = self.usage_path.parent
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

    def __init__(self, *args, pod: str, image: str,
                 extra_env: dict[str, str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.pod = pod
        self.image = image
        self.container_name = f"{pod}-praxis"
        # Extra env vars merged into the praxis container's environment at
        # start() time.  Used to pass Vertex auth tokens (VERTEX_AUTH_TOKEN)
        # without touching os.environ, which would be a race condition under
        # max_workers > 1 (multiple do_one() threads share the same process
        # environment).
        self._extra_env: dict[str, str] = extra_env or {}

    def start(self) -> str:
        # usage_path is already instances/{test_id}/usage.jsonl
        # So usage_path.parent is the per-instance directory - use it directly
        workdir = self.usage_path.parent
        workdir.mkdir(parents=True, exist_ok=True)
        cfg_path = workdir / "praxis.container.yaml"
        self._log_path = workdir / "praxis.container.log"
        self._metrics_path = workdir / "benchmark_metrics.jsonl"
        with cfg_path.open("w") as f:
            yaml.safe_dump(
                build_container_config(self.CONTAINER_PORT, self.model_spec, self.harness_api),
                f, sort_keys=False,
            )

        # benchmark_metrics and other debug logging is enabled via
        # `runtime.log_overrides` in the config itself (see
        # build_container_config()) rather than a RUST_LOG env var -- the
        # latter replaces the whole log filter (no implicit `info` fallback
        # for unlisted targets), which would silently suppress access_log's
        # own normal INFO output as a side effect (confirmed live). This
        # env only needs the JSON format switch, for structured,
        # reliably-parseable lines instead of the human-readable `tracing`
        # console format (ANSI codes) used by default.
        #
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
            env={"PRAXIS_LOG_FORMAT": "json", **self._extra_env},
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
        #
        # Health check accepts any HTTP response body (including 4xx) as
        # "ready": a response body confirms praxis is up, config is loaded,
        # and the route is active. Only connection-level failures (refused,
        # timeout) trigger a retry.
        #
        # This is necessary for Vertex AI backends: aiplatform.googleapis.com
        # has no /v1/models endpoint and always returns HTTP 404, which is a
        # legitimate "ready" signal (praxis connected, TLS succeeded, upstream
        # responded). Busybox wget exits non-zero on HTTP 4xx, so the old
        # plain-wget check would retry until timeout for every Vertex run.
        #
        # `wget -qO- ... 2>/dev/null | grep -q .` exits 0 when the response
        # contains any bytes (direct Anthropic: 200 body; Vertex: 404 body;
        # TLS error body from praxis itself) and non-zero only when there is
        # no response at all (connection refused, TCP timeout) -- i.e. praxis
        # isn't up yet.
        deadline = time.time() + self.HEALTH_TIMEOUT
        started = False
        while time.time() < deadline:
            try:
                container_exec_capture(
                    self.container_name,
                    ["sh", "-c",
                     f"wget -qO- --timeout=2 "
                     f"http://127.0.0.1:{self.CONTAINER_PORT}/v1/models "
                     f"2>/dev/null | grep -q ."],
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
        
        # Copy metrics file from container
        try:
            container_cp_out(self.container_name, "/tmp/benchmark_metrics.jsonl", self._metrics_path)
        except Exception as e:
            print(f"[metrics] warning: failed to copy metrics file: {e}", flush=True)
        
        container_stop_rm(self.container_name)
        self._read_metrics_file()

    def _read_metrics_file(self) -> None:
        """Read metrics JSONL file written by benchmark_metrics filter.

        Each line is a serialized BenchmarkMetric JSON object with all token
        types normalized and populated by the Rust filter. No log parsing needed.
        All token types (including cache_read_tokens and cache_creation_tokens)
        are now properly captured from Vertex responses.
        """
        if not getattr(self, "_metrics_path", None) or not self._metrics_path.exists():
            print(f"[metrics] warning: metrics file not found at {self._metrics_path}", flush=True)
            return

        records: list[UsageRecord] = []
        for turn, line in enumerate(self._metrics_path.read_text(errors="replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                metric = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[metrics] warning: failed to parse metrics line: {e}", flush=True)
                continue

            records.append(
                UsageRecord(
                    run_id=self.tags.run_id,
                    benchmark=self.tags.benchmark,
                    harness=self.tags.harness,
                    model=self.tags.model,
                    instance_id=self.tags.instance_id,
                    turn_index=turn,
                    input_tokens=metric.get("input_tokens", 0),
                    output_tokens=metric.get("output_tokens", 0),
                    cache_read_tokens=metric.get("cache_read_input_tokens", 0),
                    cache_creation_tokens=metric.get("cache_creation_input_tokens", 0),
                    request_id=metric.get("request_id"),
                    endpoint=metric.get("endpoint"),
                    status_code=metric.get("status_code"),
                    duration_ms=metric.get("duration_ms"),
                    ts=metric.get("timestamp_ms", 0) / 1000.0 if metric.get("timestamp_ms") else None,
                    source="praxis-ai",
                )
            )

        write_records(self.usage_path, records)
