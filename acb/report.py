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


def aggregate_per_instance_files(harness_out_dir: Path) -> None:
    """Aggregate per-instance usage files into combined usage.jsonl for backwards compatibility.
    
    Note: predictions.jsonl is aggregated by benchmark.evaluate()
    Note: metrics.jsonl is written by build_report() after updating with resolved status
    """
    instances_dir = harness_out_dir / "instances"
    if not instances_dir.exists():
        return
    
    # Aggregate usage
    usage_path = harness_out_dir / "usage.jsonl"
    with usage_path.open("w") as f:
        for instance_dir in sorted(instances_dir.iterdir()):
            if instance_dir.is_dir():
                instance_usage = instance_dir / "usage.jsonl"
                if instance_usage.exists():
                    f.write(instance_usage.read_text())


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
    
    # Also update per-instance metrics files with resolved status
    instances_dir = Path(out_dir) / "instances"
    if instances_dir.exists():
        for m in metrics:
            instance_metrics_path = instances_dir / m.instance_id / "metrics.json"
            if instance_metrics_path.exists():
                instance_metrics_path.write_text(json.dumps(asdict(m), indent=2))

    n = len(metrics) or 1
    resolved_n = sum(1 for m in metrics if m.resolved)
    
    # Handle both single harness (str) and multiple harnesses (list)
    harness_value = cfg.harness if isinstance(cfg.harness, str) else cfg.harness[0] if cfg.harness else "unknown"
    
    rollup = {
        "run_id": cfg.run_id,
        "benchmark": cfg.benchmark,
        "harness": harness_value,
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


def build_suite_report(out_dir: Path, cfg) -> Path:
    """Build a suite-level report aggregating all harness reports.
    
    Reads report.json from each harness subdirectory and produces a suite-level
    rollup that compares across harnesses.
    
    Returns the path to the suite-level report.json.
    """
    out_dir = Path(out_dir)
    harness_reports: dict[str, dict] = {}
    
    # Load reports from each harness subdirectory
    for harness_dir in sorted(out_dir.iterdir()):
        if not harness_dir.is_dir():
            continue
        report_path = harness_dir / "report.json"
        if report_path.exists():
            try:
                harness_reports[harness_dir.name] = json.loads(report_path.read_text())
            except Exception as e:
                print(f"[acb] warning: failed to load {report_path}: {e}")
    
    if not harness_reports:
        raise RuntimeError(f"No harness reports found in {out_dir}")
    
    # Build suite-level report: aggregate stats across harnesses
    suite_report = {
        "suite_id": cfg.run_id,
        "benchmark": cfg.benchmark,
        "model": cfg.model,
        "proxy": cfg.proxy,
        "instances": next(iter(harness_reports.values())).get("instances", 0),
        "harnesses": harness_reports,
    }
    
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(suite_report, indent=2))
    return report_path
