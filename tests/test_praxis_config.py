"""Regression tests for Praxis filter ordering."""

from acb.config import ModelSpec
from acb.proxy.praxis import build_config, build_container_config


def test_private_model_allows_endpoint_and_upstream_connection():
    spec = ModelSpec(name="local", api="openai", endpoint="127.0.0.1:8000", tls=False)
    for config in (build_config(18880, spec, "openai"),
                   build_container_config(18880, spec, "openai")):
        assert config["insecure_options"]["allow_private_endpoints"] is True
        assert config["insecure_options"]["allow_private_upstreams"] is True
    container = build_container_config(18880, spec, "openai")
    balancer = next(f for chain in container["filter_chains"] for f in chain["filters"]
                    if f["filter"] == "load_balancer")
    assert balancer["clusters"][0]["endpoints"] == ["host.containers.internal:8000"]


def test_public_model_does_not_enable_private_upstreams():
    spec = ModelSpec(name="public", api="openai", endpoint="api.example.com:443", tls=True)
    config = build_config(18880, spec, "openai")
    assert not config.get("insecure_options", {}).get("allow_private_upstreams", False)


def test_translated_anthropic_classification_precedes_body_translation():
    spec = ModelSpec(
        name="local-model",
        api="openai",
        endpoint="127.0.0.1:8000",
        tls=False,
    )
    config = build_config(8080, spec, "anthropic", include_token_count=True)
    filters = config["filter_chains"][1]["filters"]
    names = [item["filter"] for item in filters]

    assert names.count("request_classifier") == 1
    assert names.index("request_classifier") < names.index(
        "anthropic_messages_to_chat_completions"
    )
    assert names.index("request_classifier") < names.index(
        "anthropic_messages_to_chat_completions_stream"
    )


def test_translated_endpoint_strips_anthropic_beta_query():
    spec = ModelSpec(name="local-model", api="openai", endpoint="127.0.0.1:8000", tls=False)
    config = build_config(8080, spec, "anthropic", include_token_count=True)
    filters = config["filter_chains"][1]["filters"]
    names = [item["filter"] for item in filters]
    rewrite = filters[names.index("url_rewrite")]

    assert "path_rewrite" not in names
    assert names.index("anthropic_messages_to_chat_completions") < names.index("url_rewrite")
    assert names.index("url_rewrite") < names.index("router")
    assert rewrite["operations"] == [
        {"regex_replace": {"pattern": "^/v1/messages$", "replacement": "/v1/chat/completions"}},
        {"strip_query_params": ["beta"]},
    ]
    assert rewrite["conditions"] == [{"when": {"path_prefix": "/v1/messages"}}]


def test_same_api_route_does_not_strip_query_parameters():
    spec = ModelSpec(name="local-model", api="openai", endpoint="127.0.0.1:8000", tls=False)
    config = build_config(8080, spec, "openai", include_token_count=True)
    names = [item["filter"] for item in config["filter_chains"][1]["filters"]]
    assert "url_rewrite" not in names


def test_upstream_authority_replaces_loopback_host_for_both_harness_apis():
    spec = ModelSpec(name="fixture", api="openai", endpoint="fixture-model:18080", tls=False)
    for api in ("openai", "anthropic"):
        filters = build_config(18880, spec, api)["filter_chains"][1]["filters"]
        host_values = [header['value'] for item in filters for header in item.get('request_set', [])
                       if header['name'].lower() == 'host']
        assert host_values == ['fixture-model:18080']


def test_container_usage_excludes_discovery_and_token_probes(tmp_path):
    import json
    from acb.proxy.metrics import PraxisMetricsReader
    from acb.proxy.base import ProxyTags
    from acb.usage import read_records
    backend = PraxisMetricsReader(
        tags=ProxyTags('r', 'b', 'h', 'm', 'i'), usage_path=tmp_path / 'usage.jsonl',
        )
    backend.metrics_path = tmp_path / 'raw.jsonl'
    endpoints = ['/v1/models', '/v1/messages/count_tokens', '/v1/chat/completions', '/v1/messages?beta=true']
    backend.metrics_path.write_text(''.join(json.dumps({'endpoint': e, 'input_tokens': 100,
         'output_tokens': 20, 'request_id': str(i)}) + '\n' for i, e in enumerate(endpoints)))
    backend.read_metrics_file()
    rows = list(read_records(backend.usage_path))
    assert [r.endpoint for r in rows] == endpoints[2:]
    assert [r.turn_index for r in rows] == [0, 1]


def test_vertex_credential_reads_the_token_the_worker_exports():
    # acb/harbor/worker.py exports VERTEX_AUTH_TOKEN and passes only that name
    # into the Praxis sidecar, so credential_injection must read the same one.
    spec = ModelSpec(name="vertex", api="anthropic", endpoint="aiplatform.googleapis.com:443",
                     key_env="VERTEX_AUTH_TOKEN", vertex_model="claude-haiku-4-5@20251001")
    config = build_container_config(18880, spec, "anthropic")
    injection = next(f for chain in config["filter_chains"] for f in chain["filters"]
                     if f["filter"] == "credential_injection")
    assert injection["clusters"][0]["env_var"] == "VERTEX_AUTH_TOKEN"
