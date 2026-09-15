import json
from types import SimpleNamespace

from acb.usage import InstanceMetrics, UsageRecord, normalize_benchmark_metric, read_records
from acb.html_report import _aggregate_benchmark_metrics, _metric_total_tokens
from acb.proxy.record_server import _parse_openai_usage
from acb.proxy.praxis import PraxisContainerBackend


def test_cached_tokens_are_reported_as_context_not_usage():
    records = [
        UsageRecord(
            run_id="run",
            benchmark="bench",
            harness="goose",
            model="model",
            instance_id="instance",
            turn_index=0,
            input_tokens=100,
            cache_read_tokens=50,
            cache_creation_tokens=10,
            output_tokens=20,
        )
    ]

    metrics = InstanceMetrics.from_records(records)

    assert metrics.total_tokens == 130  # 100 fresh + 10 cache creation + 20 output
    assert metrics.context_tokens == 160  # includes the 50-token reused prefix
    assert metrics.cache_efficiency == 50 / 160


def test_mlx_cached_prefix_is_a_subset_of_prompt_tokens():
    raw = {
        "endpoint": "/v1/chat/completions",
        "input_tokens": 20995,
        "cache_read_input_tokens": 20975,
        "output_tokens": 299,
    }
    normalized = normalize_benchmark_metric(raw)
    assert normalized["input_tokens"] == 20
    assert normalize_benchmark_metric(normalized) == normalized
    assert raw["input_tokens"] == 20995
    aggregate = _aggregate_benchmark_metrics([raw])
    assert aggregate["context_tokens"] == 20995
    assert aggregate["peak_context"] == 20995
    assert aggregate["total_tokens"] == 319
    assert aggregate["cache_efficiency"] == 20975 / 20995
    assert _metric_total_tokens(raw) == 319


def test_anthropic_cache_buckets_remain_exclusive():
    metric = normalize_benchmark_metric({
        "endpoint": "/v1/messages", "input_tokens": 100,
        "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10,
        "output_tokens": 20,
    })
    aggregate = _aggregate_benchmark_metrics([metric])
    assert aggregate["total_input"] == 100
    assert aggregate["context_tokens"] == 160
    assert aggregate["total_tokens"] == 130


def test_openai_recording_handles_cached_usage_in_stream_and_json():
    response = {"usage": {
        "prompt_tokens": 100, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 90},
    }}
    for streamed in (False, True):
        text = json.dumps(response)
        if streamed:
            text = "data: " + text + "\n\ndata: [DONE]\n"
        usage = _parse_openai_usage(text.encode(), streamed)
        assert usage["input_tokens"] == 10
        assert usage["cache_read_tokens"] == 90
        assert usage["output_tokens"] == 20


def test_praxis_metrics_file_normalizes_before_writing_usage(tmp_path):
    metric_path = tmp_path / "benchmark_metrics.jsonl"
    metric_path.write_text(json.dumps({
        "endpoint": "/v1/chat/completions", "input_tokens": 100,
        "cache_read_input_tokens": 90, "output_tokens": 20,
    }) + "\n")
    backend = SimpleNamespace(
        _metrics_path=metric_path, usage_path=tmp_path / "usage.jsonl",
        tags=SimpleNamespace(run_id="run", benchmark="bench", harness="goose",
                             model="model", instance_id="instance"),
    )
    PraxisContainerBackend._read_metrics_file(backend)
    records = list(read_records(backend.usage_path))
    assert records[0].input_tokens == 10
    assert records[0].prompt_tokens == 100
    assert InstanceMetrics.from_records(records).cache_efficiency == 0.9
