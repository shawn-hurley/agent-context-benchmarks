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
