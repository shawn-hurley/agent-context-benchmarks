"""Praxis gateway configuration for controller-owned model services.

Harbor owns service startup, readiness, logs and teardown. The benchmark_metrics
filter writes request-level usage; acb.proxy.metrics normalizes those records.
Provider credentials belong to the Praxis service, not the agent environment."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import replace


# Podman's gvproxy-based user-mode networking resolves this to the macOS
# host's own network stack, including loopback services -- verified against
# Podman 6.1.0 with `UserModeNetworking: true`. This is how a container
# reaches a model server bound to 127.0.0.1 on the Mac host.
CONTAINER_HOST_GATEWAY = "host.containers.internal"

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
                "env_var": "VERTEX_AUTH_TOKEN", "header_prefix": "Bearer ",
                "strip_client_credential": True
            }]},
        ]

        # --- Metrics filter ordering ---
        # Response-phase hooks run in *reverse* declared order, so
        # token_usage_to_metrics must be declared FIRST so it runs LAST
        # in response phase — after token_count and benchmark_metrics
        # have populated filter_metadata with extracted token counts.
        filters.append({"filter": "token_usage_to_metrics", "conditions": _messages_only()})

        # Extract tokens from Anthropic SSE responses.
        # Writes to filter_metadata (token.input, token.output, token.total).
        filters.append({"filter": "token_count", "provider": "anthropic", "conditions": _messages_only()})

        # Classify request content type during request phase.
        # Stores classification in ctx.extensions for later retrieval by token_usage_to_metrics.
        # Must come BEFORE benchmark_metrics so both filters work together.
        filters.append({"filter": "request_classifier", "conditions": _messages_only()})

        # Collect comprehensive metrics including all token types, timing, and sizes.
        # Also strips vertex_event from SSE streams to prevent SDK validation errors.
        # Writes to filter_metadata (token.input/output/total/cache_read/cache_creation),
        # only if not already set by token_count above.
        filters.append({"filter": "benchmark_metrics", "conditions": _messages_only()})

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

    # Forward the upstream authority, not the agent's loopback proxy address.
    # Besides virtual hosting, plain-HTTP hostname allowlists inspect this Host.
    ai_filters: list[dict] = [{"filter": "headers", "request_set": [
        {"name": "Host", "value": model_spec.endpoint},
    ]}]
    if translate:
        # Classifies the incoming Anthropic Messages request and promotes
        # its `stream` flag to metadata that anthropic_stream_events below
        # arms itself from later (see that filter's own docs: it activates
        # automatically off this metadata + a text/event-stream response,
        # no `response_conditions` needed).
        ai_filters.append({"filter": "anthropic_messages_format", "on_invalid": "continue",
                           "conditions": _messages_only()})
        # Classification must happen before the Anthropic body is rewritten
        # into OpenAI chat-completions format. The endpoint remains
        # /v1/messages while the request body is being translated.
        ai_filters.append({"filter": "request_classifier", "conditions": _messages_only()})

    ai_filters.append({"filter": "json_body_field", "field": "model", "header": "X-Model"})

    if translate:
        # Request-phase: rewrites the Anthropic Messages body into a Chat
        # Completions-shaped body. Response-phase: transforms a compatible
        # non-streaming JSON response back; streaming responses are instead
        # handled chunk-by-chunk by anthropic_messages_to_chat_completions_stream (declared next,
        # so it runs *before* this filter's own on_response in reverse
        # order -- matching praxis-ai's own reference example).
        ai_filters.append({"filter": "anthropic_messages_to_chat_completions", "conditions": _messages_only()})
        ai_filters.append({"filter": "anthropic_messages_to_chat_completions_stream", "conditions": _messages_only()})
        # Translate the endpoint and remove Anthropic's beta query parameter.
        # MLX-LM matches the full request URL, so retaining ?beta=true makes
        # an otherwise valid chat-completions request return 404.
        ai_filters.append({
            "filter": "url_rewrite",
            "operations": [
                {"regex_replace": {"pattern": "^/v1/messages$",
                                   "replacement": "/v1/chat/completions"}},
                {"strip_query_params": ["beta"]},
            ],
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
        # --- Metrics filter ordering ---
        # Response-phase hooks run in *reverse* declared order, so
        # token_usage_to_metrics must be declared FIRST so it runs LAST
        # in response phase — after token_count and benchmark_metrics
        # have populated filter_metadata with extracted token counts.
        trailing_filters.append({"filter": "token_usage_to_metrics"})

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

        # Classify request content type during request phase.
        # Stores classification in ctx.extensions for later retrieval by token_usage_to_metrics.
        # Translated Anthropic requests are classified above, before body
        # translation. Native requests are classified in this observability
        # chain, before benchmark_metrics reads the extension.
        if not translate:
            trailing_filters.append({"filter": "request_classifier"})

        # Comprehensive token tracking across all backends.
        # Handles both OpenAI and Anthropic response formats automatically,
        # captures all token types (input, output, cache_read, cache_creation),
        # and includes timing/size data.
        # Writes to filter_metadata (token.input/output/total/cache_read/cache_creation),
        # only if not already set by token_count above.
        trailing_filters.append({"filter": "benchmark_metrics"})
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
        config["insecure_options"] = {
            "allow_private_endpoints": True,
            "allow_private_upstreams": True,
        }
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
    # Endpoint validation and the connection-time resolved-IP guard have
    # separate switches. The host gateway requires both permissions.
    config["insecure_options"] = {
        "allow_private_endpoints": True,
        "allow_private_upstreams": True,
    }
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
