"""Aggregate the per-request usage log into per-instance and per-run metrics.

Output (in the run dir):
  metrics.jsonl  -- one InstanceMetrics row per instance
  report.json    -- run-level rollup + config

The report is the common evaluation output across harnesses/models/benchmarks:
every value is derived from usage.jsonl, so adding a new context metric later is
a re-aggregation, not a re-run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from acb.usage import InstanceMetrics, read_records


def build_report(usage_path: Path, resolved: dict[str, bool], out_dir: Path, cfg) -> Path:
    by_instance: dict[str, list] = defaultdict(list)
    if Path(usage_path).exists():
        for rec in read_records(usage_path):
            by_instance[rec.instance_id].append(rec)

    metrics: list[InstanceMetrics] = []
    for iid, recs in by_instance.items():
        m = InstanceMetrics.from_records(recs)
        m.resolved = resolved.get(iid)
        metrics.append(m)
    # instances that produced no LLM traffic (e.g. harness crash) still count
    for iid, is_resolved in resolved.items():
        if iid not in by_instance:
            metrics.append(InstanceMetrics(
                run_id=cfg.run_id, benchmark=cfg.benchmark, harness=cfg.harness,
                model=cfg.model, instance_id=iid, resolved=is_resolved,
            ))

    metrics_path = Path(out_dir) / "metrics.jsonl"
    with metrics_path.open("w") as f:
        for m in metrics:
            f.write(json.dumps(asdict(m), separators=(",", ":")) + "\n")

    n = len(metrics) or 1
    resolved_n = sum(1 for m in metrics if m.resolved)
    rollup = {
        "run_id": cfg.run_id,
        "benchmark": cfg.benchmark,
        "harness": cfg.harness,
        "model": cfg.model,
        "proxy": cfg.proxy,
        "instances": len(metrics),
        "resolved": resolved_n,
        "resolve_rate": resolved_n / n,
        "avg_total_tokens": sum(m.total_tokens for m in metrics) / n,
        "avg_turns": sum(m.turns for m in metrics) / n,
        "avg_peak_context": sum(m.peak_context for m in metrics) / n,
        "avg_cache_efficiency": sum(m.cache_efficiency for m in metrics) / n,
        # cost-of-success: tokens spent per resolved instance
        "tokens_per_resolved": (
            sum(m.total_tokens for m in metrics) / resolved_n if resolved_n else None
        ),
    }
    report_path = Path(out_dir) / "report.json"
    report_path.write_text(json.dumps(rollup, indent=2))
    return report_path
