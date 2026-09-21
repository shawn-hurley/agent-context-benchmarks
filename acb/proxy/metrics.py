"""Normalize recorded Praxis metrics independently of service orchestration."""
from dataclasses import dataclass
from pathlib import Path

from acb.proxy.base import ProxyTags
from acb.usage import is_model_request, parse_measurements, UsageRecord, write_records, normalize_benchmark_metric


@dataclass
class PraxisMetricsReader:
    tags: ProxyTags
    usage_path: Path
    metrics_path: Path | None = None

    def read_metrics_file(self, measurements: list[dict] | None = None) -> list[str]:
        """Read metrics JSONL file written by benchmark_metrics filter.

        Each line is a serialized BenchmarkMetric JSON object with all token
        types normalized and populated by the Rust filter. No log parsing needed.
        All token types (including cache_read_tokens and cache_creation_tokens)
         are now properly captured from Vertex responses.
         """
        errors = []
        if measurements is None and (not getattr(self, "metrics_path", None) or not self.metrics_path.exists()):
            # Non-critical: metrics file may not be present
            return ["metrics file missing"]

        if measurements is None:
            measurements, errors = parse_measurements(self.metrics_path.read_text(errors="replace"))

        records: list[UsageRecord] = []
        for metric in measurements:
            if not is_model_request(metric):
                continue
            records.append(
                UsageRecord(
                    run_id=self.tags.run_id,
                    benchmark=self.tags.benchmark,
                    harness=self.tags.harness,
                    model=self.tags.model,
                    instance_id=self.tags.instance_id,
                    turn_index=len(records),
                    input_tokens=normalize_benchmark_metric(metric).get("input_tokens", 0),
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
        return errors
