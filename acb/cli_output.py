"""Concise terminal views of the same plans and records emitted as JSON."""
from __future__ import annotations

import json
import sys


def text_output(args) -> bool:
    return args.output_format == "text" or (args.output_format is None and sys.stdout.isatty())


def emit(args, document, lines):
    if text_output(args):
        print("\n".join(lines() if callable(lines) else lines))
    else:
        print(json.dumps(document, indent=2))


def value(item):
    if item is None:
        return "unavailable"
    if isinstance(item, float):
        return f"{item:,.4g}"
    if isinstance(item, int):
        return f"{item:,}"
    return str(item)


def components(items):
    def describe(item):
        if isinstance(item, str):
            return item
        name = item.get("name", "unnamed") + (f" {item['version']}" if item.get("version") else "")
        return name + (" " + json.dumps(item["options"], sort_keys=True) if item.get("options") else "")
    return ", ".join(describe(item) for item in items or []) or "none"


def plan_lines(plan):
    model = plan["model"]
    workflow = plan.get("workflow")
    benchmark = plan["benchmark_config"]
    lines = [
        f"Run: {plan['run_id']}", f"Benchmark: {plan['benchmark']}",
        f"Task source: {benchmark.get('path') or benchmark.get('dataset') or benchmark.get('benchmark_cache_dir') or 'not configured'}",
        f"Dataset revision: {benchmark.get('revision') or benchmark.get('version') or 'not pinned in configuration'}",
        f"Environment: {plan['environment']} ({plan['execution_backend']})",
        f"Model alias: {plan['model_alias']}", f"Provider model ID: {model['name']}",
        f"Provider endpoint: {'https' if model['tls'] else 'http'}://{model['endpoint']} ({model['api']})",
        f"Credential environment variable: {model.get('key_env') or 'none configured'}",
        f"Measurement proxy: {plan['proxy']}",
        f"Workflow: {workflow['name'] if workflow else 'none'}",
    ]
    if workflow:
        lines.append(f"Workflow directory: {workflow['source_dir']}")
        lines.append("Stages: " + ", ".join(f"{s['name']} ({s['timeout_sec']}s)" for s in workflow['steps']))
    lines.extend([
        f"Task IDs: {', '.join(plan['subset']) if plan.get('subset') else 'all eligible tasks'}",
        f"Task limit per harness: {plan.get('limit') or 'none'}",
        f"Concurrent trials (global): {plan['max_workers']}",
        f"Attempts per task/harness: {plan['attempts']}",
        f"Output directory: {plan['output_dir']}", f"Cache directory: {plan['cache_dir']}",
        f"Offline: {plan['offline']}", f"Cache policy: {plan['cache_policy']}",
    ])
    for name, config in plan["harnesses"].items():
        lines.extend([
            f"Harness: {name} {config.get('version', '')} — timeout {config.get('timeout', 'default')}s",
            f"  Skills: {components(config.get('skills'))}",
            f"  Extensions: {components(config.get('extensions'))}",
            f"  MCP servers: {components(config.get('mcp_servers'))}",
        ])
        advanced = {key: item for key, item in config.items() if key not in {
            "version", "timeout", "skills", "extensions", "mcp_servers", "execution_integrations", "model_middleware"}}
        if advanced:
            lines.append("  Settings: " + json.dumps(advanced, sort_keys=True))
    manifest = plan.get("manifest")
    if manifest:
        count = len(manifest["tasks"])
        lines.append(f"Selected tasks: {count}; scheduled trials: {count * len(plan['harnesses']) * plan['attempts']}")
    else:
        lines.append("Task count: pending discovery/preparation (acb tasks lists eligible IDs)")
    lines.extend(["Configuration sources:", *[f"  {source}" for source in plan.get("sources", [])]])
    if plan.get("pending_checks"):
        lines.append("Pending checks: " + "; ".join(plan["pending_checks"]))
    if plan.get("prepared_file"):
        lines.append(f"Prepared plan: {plan['prepared_file']}")
    return lines


def report_lines(roots):
    from acb.report_data import ReportSource
    lines = []
    for root in roots:
        source = ReportSource(root)
        lines.append(f"Results: {root}")
        for harness in source.harnesses.values():
            report = harness.report
            rows = list(ReportSource(harness.directory).benchmarks.values())
            graded = [row for row in rows if row["grade"] is not None]
            measured = [row for row in rows if row["measurement_complete"]]
            evaluations = report.get("evaluations", [])
            errors = sum(item.get("status") != "completed" for item in evaluations)
            tokens = sum(row["tokens"] for row in measured) if measured else None
            lines.extend([
                f"  Harness: {report['harness']} | Model: {report.get('model', 'unavailable')}",
                f"  Graded tasks: {len(graded)}/{len(rows)} | Complete measurements: {len(measured)}/{len(rows)}",
                f"  Trials with errors/unavailable grades: {errors} | Missing trials: {report.get('missing_trials', 'unavailable')}",
                f"  Tokens for complete measurements: {value(tokens)} (coverage {len(measured)}/{len(rows)})",
                "  Task | Grade | Measurements | Tokens",
            ])
            for row in rows:
                lines.append(f"  {row['benchmark']} | {value(row['grade'])} | "
                             f"{'complete' if row['measurement_complete'] else 'incomplete'} | {value(row['tokens'])}")
        if not source.harnesses:
            lines.append("  Harness details unavailable; use --json to inspect the saved overview.")
    lines.append("Grades and complete measurements determine results; process exit alone does not.")
    return lines


def comparison_lines(payloads):
    lines = []
    for payload in payloads:
        coverage, tokens = payload["coverage"], payload["matched_tokens"]
        lines.extend([
            f"Baseline: {payload['baseline']}", f"Candidate: {payload['candidate']}",
            f"Grade change: {payload['quality'] or 'unavailable'} | Compared grades: {coverage['graded']}/{coverage['total']}",
            f"Comparable measurements: {coverage['measured']}/{coverage['total']}",
            f"Matched tokens: {value(tokens['baseline'])} → {value(tokens['candidate'])}; "
            f"change {value(tokens['absolute'])} ({value(tokens['percent'])}{'%' if tokens['percent'] is not None else ''})",
            "Task | Baseline grade | Candidate grade | Result | Token change",
        ])
        for row in payload["rows"]:
            left, right = row["baseline"] or {}, row["candidate"] or {}
            lines.append(f"{' / '.join(row['identity'])} | {value(left.get('grade'))} | "
                         f"{value(right.get('grade'))} | {row['quality']} | {value(row['tokens']['absolute'])}")
            lines.extend(f"  {reason}" for reason in row["reasons"])
            if not row["measurement_comparable"]:
                lines.append("  Token change unavailable: inputs or measurements are not comparable.")
            lines.extend(f"  Setting {change['setting']}: {value(change['baseline'])} → {value(change['candidate'])}"
                         for change in row["setup_differences"])
            lines.extend(f"  {note}" for note in row["telemetry_notes"])
    return lines


def task_lines(document):
    lines = [f"Tasks: {document['benchmark']} ({document['source']})",
             f"Listing: {'complete' if document['complete'] else 'partial cached coverage'}",
             "Selected | Task ID"]
    lines.extend(f"{'yes' if task['selected'] is True else 'no ' if task['selected'] is False else '?  '} | {task['id']}"
                 for task in document["tasks"])
    lines.extend(document["notes"])
    lines.append("Use these IDs in run YAML subset, or browse Task IDs in acb run.")
    return lines
