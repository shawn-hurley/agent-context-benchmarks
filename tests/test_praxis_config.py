"""Regression tests for Praxis filter ordering."""

from acb.config import ModelSpec
from acb.proxy.praxis import build_config


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
