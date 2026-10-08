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
from acb.utils import normalize_instance_id_for_path
from acb.harbor.paths import trial_directory


def aggregate_per_instance_files(harness_out_dir: Path, trial_ids: list[str]) -> None:
    """Aggregate only the scheduled/imported direct trial directories."""
    trial_dirs = [trial_directory(harness_out_dir.parent, harness_out_dir.name, normalize_instance_id_for_path(iid)) for iid in trial_ids]
    # Aggregate usage
    usage_path = harness_out_dir / "usage.jsonl"
    with usage_path.open("w") as f:
        for instance_dir in sorted(trial_dirs):
            if instance_dir.is_dir():
                instance_usage = instance_dir / "usage.jsonl"
                if instance_usage.exists():
                    f.write(instance_usage.read_text())
    
    # Aggregate benchmark_metrics (per-request Praxis output with content classification)
    # Add instance_id to each record since benchmark_metrics are stored per-instance
    benchmark_metrics_path = harness_out_dir / "benchmark_metrics.jsonl"
    with benchmark_metrics_path.open("w") as f:
        for instance_dir in sorted(trial_dirs):
            if instance_dir.is_dir():
                instance_id = instance_dir.name
                instance_usage = instance_dir / "usage.jsonl"
                if instance_usage.exists():
                    first = next((line for line in instance_usage.read_text().splitlines() if line.strip()), None)
                    if first:
                        instance_id = json.loads(first).get("instance_id", instance_id)
                instance_bm = instance_dir / "benchmark_metrics.jsonl"
                if instance_bm.exists():
                    for line in instance_bm.read_text().strip().split('\n'):
                        if line.strip():
                            # Parse, add instance_id, and write back
                            rec = json.loads(line)
                            rec['instance_id'] = instance_id
                            f.write(json.dumps(rec, separators=(',', ':')) + '\n')


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
    instances_dir = Path(out_dir)
    if instances_dir.exists():
        for m in metrics:
            instance_metrics_path = trial_directory(instances_dir.parent, instances_dir.name, normalize_instance_id_for_path(m.instance_id)) / "metrics.json"
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
        "proxy": "praxis",
        "instances": len(metrics),
        "resolved": resolved_n,
        "resolve_rate": resolved_n / n,
        "avg_total_tokens": sum(m.total_tokens for m in metrics) / n,
        "avg_context_tokens": sum(m.context_tokens for m in metrics) / n,
        "avg_turns": sum(m.turns for m in metrics) / n,
        "avg_peak_context": sum(m.peak_context for m in metrics) / n,
        "avg_cache_efficiency": sum(m.cache_efficiency for m in metrics) / n,
        # cost-of-success: tokens spent per resolved instance
        "tokens_per_resolved": (
            sum(m.total_tokens for m in metrics) / resolved_n if resolved_n else None
        ),
    }
    integration_manifests = {}
    for iid in resolved:
        for manifest_path in sorted((trial_directory(Path(out_dir).parent, Path(out_dir).name, normalize_instance_id_for_path(iid)) / "integrations").glob("*/manifest.json")):
            integration_manifests.setdefault(iid, []).append(json.loads(manifest_path.read_text()))
    if integration_manifests:
        rollup["integrations"] = integration_manifests
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
        "proxy": "praxis",
        "instances": next(iter(harness_reports.values())).get("instances", 0),
        "harnesses": harness_reports,
    }
    
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(suite_report, indent=2))
    return report_path
