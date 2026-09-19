"""Individual benchmark charts and backwards-compatible in-memory rendering.

Comparison semantics live in acb.comparison; acb.comparison_html writes bundles
with standalone benchmark pages. Chart.js and optional patch rendering use CDN
assets; captured raw evidence and tables remain readable offline.
"""

from __future__ import annotations

import json
import html
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def _script_json(value, **kwargs):
    """JSON safe inside an HTML script element, including arbitrary tool output."""
    return (json.dumps(value, **kwargs).replace('&', '\\u0026')
            .replace('<', '\\u003c').replace('>', '\\u003e')
            .replace(chr(0x2028), '\\u2028').replace(chr(0x2029), '\\u2029'))


from acb.costs import ModelCost, estimate_cost, load_cost_table
from acb.usage import read_records, normalize_benchmark_metric, is_model_request

_CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4"

_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
]


def _color(i: int) -> str:
    return _COLORS[i % len(_COLORS)]


def _is_suite_directory(path: Path) -> bool:
    """Check if a directory is a suite (contains harness subdirectories with report.json)."""
    path = Path(path)
    if not path.is_dir():
        return False
    # A suite has subdirectories with report.json files and a harness name
    # that matches known harness names or appears to be a harness directory
    subdirs_with_reports = 0
    for item in path.iterdir():
        if item.is_dir() and (item / "report.json").exists():
            # Check if it looks like a harness directory (has metrics.jsonl or usage.jsonl)
            if (item / "metrics.jsonl").exists() or (item / "usage.jsonl").exists():
                subdirs_with_reports += 1
    return subdirs_with_reports > 0


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Per-run data container
# ---------------------------------------------------------------------------

@dataclass
class _RunData:
    run_dir: Path
    report: dict
    metrics: list[dict]
    usage_rows: list[dict]
    predictions: dict[str, str] = field(default_factory=dict)  # instance_id → model_patch
    cost: ModelCost | None = field(default=None)
    harness_name: str = ""  # for suite reports, the harness this data came from

    @property
    def run_id(self) -> str:
        # For suite reports, use harness_name; for single harnesses, use report.run_id or dir name
        if self.harness_name:
            return self.harness_name
        return self.report.get("run_id") or self.run_dir.name

    @classmethod
    def load(cls, run_dir: Path, harness_name: str = "") -> "_RunData":
        run_dir = Path(run_dir)
        report = json.loads(p.read_text()) if (p := run_dir / "report.json").exists() else {}
        
        # Try benchmark_metrics.jsonl first (new way - has content classification)
        # Fall back to metrics.jsonl (old way) if benchmark_metrics doesn't exist
        benchmark_metrics = _load_jsonl(run_dir / "benchmark_metrics.jsonl")
        
        if benchmark_metrics:
            # NEW PATH: Aggregate benchmark_metrics per-instance
            # Group by instance_id (from first 'instance_id' field in benchmark_metrics records, 
            # or fall back to using index if instance_id not present)
            instances: dict[str, list] = defaultdict(list)
            for rec in benchmark_metrics:
                if rec.get('endpoint') and not is_model_request(rec):
                    continue
                iid = rec.get('instance_id')
                if iid:
                    instances[iid].append(rec)
            
            # Aggregate each instance's per-request metrics
            metrics = []
            for iid, recs in instances.items():
                agg = _aggregate_benchmark_metrics(recs)
                agg['instance_id'] = iid
                # Preserve other fields from report if available
                for metric in _load_jsonl(run_dir / "metrics.jsonl"):
                    if metric.get('instance_id') == iid:
                        agg['resolved'] = metric.get('resolved')
                        break
                metrics.append(agg)
            
            # If no instance_id in benchmark_metrics, fall back to metrics.jsonl
            if not metrics:
                metrics = _load_jsonl(run_dir / "metrics.jsonl")
        else:
            # FALLBACK: Use metrics.jsonl (old way)
            metrics = _load_jsonl(run_dir / "metrics.jsonl")
        
        usage_rows = (
            [r.__dict__ for r in read_records(run_dir / "usage.jsonl")]
            if (run_dir / "usage.jsonl").exists()
            else []
        )
        
        # Load predictions from instances/*/prediction.json (source of truth)
        # This ensures we get ALL patches, not relying on predictions.jsonl which may be incomplete
        predictions: dict[str, str] = {}
        instances_dir = run_dir / "instances"
        if instances_dir.exists():
            for inst_dir in sorted(instances_dir.iterdir()):
                if inst_dir.is_dir():
                    pred_file = inst_dir / "prediction.json"
                    if pred_file.exists():
                        try:
                            pred = json.loads(pred_file.read_text())
                            iid = pred.get("instance_id")
                            if iid:
                                predictions[iid] = pred.get("model_patch", "")
                        except (json.JSONDecodeError, OSError):
                            # Skip malformed or unreadable files
                            pass
        model = report.get("model")
        cost = load_cost_table().get(model) if model else None
        return cls(run_dir=run_dir, report=report, metrics=metrics,
                   usage_rows=usage_rows, predictions=predictions, cost=cost,
                   harness_name=harness_name)


# ---------------------------------------------------------------------------
# Shared helpers (work the same in single and multi-run modes)
# ---------------------------------------------------------------------------

def _per_turn_averages(
    usage_rows: list[dict],
) -> tuple[list[int], list[float], list[float], list[float | None]]:
    """Average input/output tokens and duration at each turn index."""
    by_turn_input: dict[int, list[float]] = defaultdict(list)
    by_turn_output: dict[int, list[float]] = defaultdict(list)
    by_turn_duration: dict[int, list[float]] = defaultdict(list)
    for r in usage_rows:
        t = r["turn_index"]
        by_turn_input[t].append(r.get("input_tokens", 0))
        by_turn_output[t].append(r.get("output_tokens", 0))
        if r.get("duration_ms") is not None:
            by_turn_duration[t].append(r["duration_ms"])

    if not by_turn_input:
        return [], [], [], []
    max_turn = max(by_turn_input)
    turns = list(range(max_turn + 1))
    avg_input = [
        sum(by_turn_input.get(t, [0])) / max(len(by_turn_input.get(t, [])), 1)
        for t in turns
    ]
    avg_output = [
        sum(by_turn_output.get(t, [0])) / max(len(by_turn_output.get(t, [])), 1)
        for t in turns
    ]
    avg_duration: list[float | None] = [
        (sum(by_turn_duration[t]) / len(by_turn_duration[t]))
        if by_turn_duration.get(t)
        else None
        for t in turns
    ]
    return turns, avg_input, avg_output, avg_duration


def _total_cost(usage_rows: list[dict], cost: ModelCost) -> float:
    return sum(
        estimate_cost(
            cost,
            input_tokens=r.get("input_tokens", 0),
            output_tokens=r.get("output_tokens", 0),
            cache_read_tokens=r.get("cache_read_tokens", 0),
            cache_creation_tokens=r.get("cache_creation_tokens", 0),
        )
        for r in usage_rows
    )


def _aggregate_benchmark_metrics(records: list[dict]) -> dict:
    """Aggregate per-request benchmark_metrics into instance-level metrics.
    
    Computes totals, averages, and context growth from per-request data.
    Returns a dict compatible with InstanceMetrics structure.
    
    Note: total_tokens excludes reused cache reads; context_tokens includes them.
    """
    if not records:
        return {
            'turns': 0,
            'total_input': 0,
            'total_output': 0,
            'total_cache_read': 0,
            'total_cache_creation': 0,
            'total_tokens': 0,
            'context_tokens': 0,
            'peak_context': 0,
            'per_turn_prompt': [],
            'cache_efficiency': 0.0,
            'requests': [],
        }
    
    # Sort by timestamp to ensure turn order
    sorted_records = sorted(
        (normalize_benchmark_metric(r) for r in records),
        key=lambda r: r.get('timestamp_ms', 0),
    )
    
    for index, record in enumerate(sorted_records):
        record['turn_index'] = index

    # Compute aggregates
    total_input = sum(r.get('input_tokens', 0) for r in sorted_records)
    total_output = sum(r.get('output_tokens', 0) for r in sorted_records)
    total_cache_read = sum(r.get('cache_read_input_tokens', 0) for r in sorted_records)
    total_cache_creation = sum(r.get('cache_creation_input_tokens', 0) for r in sorted_records)
    
    # Usage-style total excludes reused cache reads. Context volume remains
    # available separately through total_prompt/context metrics.
    total_tokens = total_input + total_output + total_cache_creation
    
    # Per-turn prompt size (input + cache_read + cache_creation)
    per_turn_prompt = [
        r.get('input_tokens', 0) + r.get('cache_read_input_tokens', 0) + r.get('cache_creation_input_tokens', 0)
        for r in sorted_records
    ]
    peak_context = max(per_turn_prompt) if per_turn_prompt else 0
    
    # Cache efficiency: cache_read / total_prompt_tokens
    total_prompt = sum(per_turn_prompt)
    context_tokens = total_prompt
    cache_efficiency = total_cache_read / total_prompt if total_prompt > 0 else 0.0
    
    return {
        'turns': len(sorted_records),
        'total_input': total_input,
        'total_output': total_output,
        'total_cache_read': total_cache_read,
        'total_cache_creation': total_cache_creation,
        'total_tokens': total_tokens,
        'context_tokens': context_tokens,
        'peak_context': peak_context,
        'per_turn_prompt': per_turn_prompt,
        'cache_efficiency': cache_efficiency,
        'requests': sorted_records,  # Keep per-request data for content analysis
    }


def _metric_total_tokens(metric: dict) -> float:
    """Return fresh input/creation and output tokens for a classified request."""
    metric = normalize_benchmark_metric(metric)
    return sum(
        metric.get(field, 0) or 0
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
        )
    )


def _tool_identities(metric: dict) -> list[dict]:
    """Return normalized tool identities, including legacy records."""
    tools = metric.get("tools") or []
    if tools:
        return tools
    name = metric.get("tool_name")
    if not name:
        return []
    return [{
        "name": name,
        "detail": metric.get("tool_detail"),
        "call_id": metric.get("tool_call_id"),
    }]


def _normalize_tool_interactions(metrics: list[dict]) -> list[dict]:
    """Combine tool-call and tool-result records into logical interactions.

    The raw metrics remain request-level records. This derived stream is used
    only for tool reporting, so a matching call/result pair is counted once
    while retaining the summed token cost of both model requests.
    """
    interactions: dict[str, dict] = {}
    result_call_ids = {
        (metric.get("instance_id"), metric.get("step_name"), tool.get("call_id"))
        for metric in metrics
        for tool in (metric.get("tool_results") or [])
        if tool.get("call_id")
    }
    anonymous_index = 0
    for metric in metrics:
        if metric.get("content_type") not in {"tool_call", "tool_result"}:
            continue
        tools = _tool_identities(metric)
        if not tools:
            tools = [{"name": "unknown", "detail": None, "call_id": None}]
        token_share = _metric_total_tokens(metric) / len(tools)
        for tool_index, tool in enumerate(tools):
            call_id = tool.get("call_id")
            if call_id:
                key = str((metric.get("instance_id"), metric.get("step_name"), call_id))
            else:
                key = f"record:{metric.get('request_id', anonymous_index)}:{tool_index}"
                anonymous_index += 1
            interaction = interactions.setdefault(key, {
                "name": tool.get("name") or "unknown",
                "detail": tool.get("detail"),
                "call_id": call_id,
                "scope": (metric.get("instance_id"), metric.get("step_name")),
                "request_ids": [],
                "source_types": set(),
                "tokens": 0.0,
                "duration_ms": 0.0,
            })
            # Prefer the most informative detail if the paired records differ.
            if not interaction["detail"] and tool.get("detail"):
                interaction["detail"] = tool["detail"]
            request_id = metric.get("request_id")
            if request_id and request_id not in interaction["request_ids"]:
                interaction["request_ids"].append(request_id)
            interaction["source_types"].add(metric["content_type"])
            interaction["tokens"] += token_share
            interaction["duration_ms"] += (metric.get("duration_ms") or 0) / len(tools)

    normalized = []
    for interaction in interactions.values():
        source_types = interaction.pop("source_types")
        if (*interaction["scope"], interaction.get("call_id")) in result_call_ids:
            source_types.add("tool_result")
        interaction["source_types"] = sorted(source_types)
        interaction["complete_pair"] = source_types == {"tool_call", "tool_result"}
        interaction["call_only"] = source_types == {"tool_call"}
        interaction["result_only"] = source_types == {"tool_result"}
        normalized.append(interaction)
    return normalized


def _content_type_breakdown(metrics: list[dict]) -> dict:
    """Generate data for content type visualization."""
    by_type = {}
    tool_usage = {}

    for m in metrics:
        content_type = m.get('content_type', 'unknown')
        if content_type in ['tool_call', 'tool_result']:
            continue
        tokens = _metric_total_tokens(m)

        # Aggregate by content type
        if content_type not in by_type:
            by_type[content_type] = {
                'count': 0,
                'total_tokens': 0,
                'turns': []
            }
        by_type[content_type]['count'] += 1
        by_type[content_type]['total_tokens'] += tokens
        by_type[content_type]['turns'].append(m.get('turn_index', 0))
        
    interactions = _normalize_tool_interactions(metrics)
    if interactions:
        by_type['tool_call'] = {
            'count': len(interactions),
            'total_tokens': sum(i['tokens'] for i in interactions),
            'turns': [],
        }
        for interaction in interactions:
            tool_name = interaction['name']
            tool_detail = interaction['detail']
            tool_key = f"{tool_name}:{tool_detail}" if tool_detail else tool_name
            data = tool_usage.setdefault(tool_key, {
                'tool_name': tool_name,
                'tool_detail': tool_detail,
                'interactions': 0,
                'complete_pairs': 0,
                'call_only': 0,
                'result_only': 0,
                'total_tokens': 0.0,
            })
            data['interactions'] += 1
            data['complete_pairs'] += int(interaction['complete_pair'])
            data['call_only'] += int(interaction['call_only'])
            data['result_only'] += int(interaction['result_only'])
            data['total_tokens'] += interaction['tokens']

    return {
        'by_type': by_type,
        'tool_usage': tool_usage
    }


def _prepare_timeline_data(metrics: list[dict]) -> dict:
    """Prepare stacked bar chart data showing content types per turn."""
    # Get all turns in order
    turn_set = set()
    for m in metrics:
        turn_idx = m.get('turn_index', 0)
        turn_set.add(turn_idx)
    
    turns = sorted(turn_set)
    if not turns:
        return {'labels': [], 'datasets': []}
    
    # Group by content type
    by_type = {}
    for m in metrics:
        content_type = m.get('content_type', 'unknown')
        if content_type not in by_type:
            by_type[content_type] = [0] * len(turns)
        
        turn_idx = m.get('turn_index', 0)
        if turn_idx in turns:
            data_idx = turns.index(turn_idx)
            by_type[content_type][data_idx] = m.get('input_tokens', 0)
    
    # Build Chart.js dataset
    colors = {
        'system_prompt': '#4e79a7',
        'user_message': '#59a14f',
        'tool_call': '#f28e2b',
        'tool_result': '#e15759',
        'assistant_continuation': '#76b7b2',
        'mixed': '#edc948',
        'unknown': '#999999'
    }
    
    datasets = []
    for content_type, data in sorted(by_type.items()):
        datasets.append({
            'label': content_type.replace('_', ' ').title(),
            'data': data,
            'backgroundColor': colors.get(content_type, '#cccccc')
        })
    
    return {
        'labels': [f"Request {t}" for t in turns],
        'datasets': datasets
    }


def _shell_command_breakdown(metrics: list[dict]) -> dict:
    """Extract and aggregate shell command usage from bash tool calls.
    
    Returns:
        {
            'rgctl': {'calls': 15, 'results': 15, 'total_tokens': 40000, 'turns': [...]},
            'git': {'calls': 20, 'results': 20, 'total_tokens': 35000, 'turns': [...]},
            ...
        }
        Sorted by total_tokens (descending)
    """
    commands = {}
    
    for m in metrics:
        # Only process bash-executed commands
        if m.get('tool_detail') == 'bash':
            cmd = m.get('tool_name', 'unknown')
            content_type = m.get('content_type')
            tokens = _metric_total_tokens(m)
            turn = m.get('turn_index', 0)
            
            if cmd not in commands:
                commands[cmd] = {
                    'calls': 0,
                    'results': 0,
                    'total_tokens': 0,
                    'turns': []
                }
            
            if content_type == 'tool_call':
                commands[cmd]['calls'] += 1
            elif content_type == 'tool_result':
                commands[cmd]['results'] += 1
            
            commands[cmd]['total_tokens'] += tokens
            commands[cmd]['turns'].append(turn)
    
    # Sort by total tokens (descending)
    return dict(sorted(commands.items(), key=lambda x: x[1]['total_tokens'], reverse=True))


def _calculate_tool_usage_percentage(metrics: list[dict]) -> dict:
    """Calculate what percentage of tokens are used by tools, averaged per instance.
    
    For each instance:
      - Calculate: (tool_tokens / total_tokens) * 100
    Then average across all instances.
    
    Returns:
        {
            'average_percentage': 42.5,
            'min_percentage': 20.0,
            'max_percentage': 65.0,
            'tool_call_tokens': 50000,
            'tool_result_tokens': 45000,
            'total_tokens': 225000
        }
    """
    # Group by instance_id
    by_instance = {}
    for m in metrics:
        instance_id = m.get('instance_id', 'unknown')
        if instance_id not in by_instance:
            by_instance[instance_id] = {
                'tool_tokens': 0,
                'total_tokens': 0,
                'tool_call_tokens': 0,
                'tool_result_tokens': 0
            }
        
        tokens = _metric_total_tokens(m)
        content_type = m.get('content_type')
        
        by_instance[instance_id]['total_tokens'] += tokens
        
        if content_type in ['tool_call', 'tool_result']:
            by_instance[instance_id]['tool_tokens'] += tokens
            if content_type == 'tool_call':
                by_instance[instance_id]['tool_call_tokens'] += tokens
            else:
                by_instance[instance_id]['tool_result_tokens'] += tokens
    
    # Calculate percentages per instance
    percentages = []
    for data in by_instance.values():
        if data['total_tokens'] > 0:
            pct = (data['tool_tokens'] / data['total_tokens']) * 100
            percentages.append(pct)
    
    # Aggregate stats across all instances
    total_tool_call = sum(d['tool_call_tokens'] for d in by_instance.values())
    total_tool_result = sum(d['tool_result_tokens'] for d in by_instance.values())
    total_all = sum(d['total_tokens'] for d in by_instance.values())
    
    return {
        'average_percentage': sum(percentages) / len(percentages) if percentages else 0.0,
        'min_percentage': min(percentages) if percentages else 0.0,
        'max_percentage': max(percentages) if percentages else 0.0,
        'tool_call_tokens': total_tool_call,
        'tool_result_tokens': total_tool_result,
        'total_tokens': total_all
    }


def _summary_cards(report: dict, total_cost: float | None, tool_usage_pct: dict | None = None) -> str:
    if not report:
        return '<p class="muted">No report.json found for this run.</p>'
    fields: list[tuple[str, Any]] = [
        ("Resolve rate", f"{report.get('resolve_rate', 0) * 100:.0f}%"),
        ("Instances", report.get("instances", "-")),
        ("Resolved", report.get("resolved", "-")),
        ("Avg total tokens", f"{report.get('avg_total_tokens', 0):,.0f}"),
        ("Avg turns", f"{report.get('avg_turns', 0):.1f}"),
        ("Avg peak context", f"{report.get('avg_peak_context', 0):,.0f}"),
        ("Avg cache efficiency", f"{report.get('avg_cache_efficiency', 0) * 100:.1f}%"),
        ("Tokens / resolved", (
            f"{report['tokens_per_resolved']:,.0f}"
            if report.get("tokens_per_resolved") is not None else "n/a"
        )),
    ]
    
    # Add tool usage percentage if available
    if tool_usage_pct and tool_usage_pct['average_percentage'] > 0:
        avg_pct = tool_usage_pct['average_percentage']
        min_pct = tool_usage_pct['min_percentage']
        max_pct = tool_usage_pct['max_percentage']
        tc = tool_usage_pct['tool_call_tokens']
        tr = tool_usage_pct['tool_result_tokens']
        
        # Create tooltip text
        tooltip = f"Tool calls: {tc:,} tokens | Tool results: {tr:,} tokens | Range: {min_pct:.1f}% - {max_pct:.1f}%"
        
        fields.append(("Tool usage", f"<span class='tooltip-trigger'>{avg_pct:.1f}%<span class='tooltip-text'>{tooltip}</span></span>"))
    
    if total_cost is not None:
        fields.append(("Total est. cost", f"${total_cost:,.4f}"))
    cards = "".join(
        f'<div class="card"><div class="card-label">{label}</div>'
        f'<div class="card-value">{value}</div></div>'
        for label, value in fields
    )
    return f'<div class="cards">{cards}</div>'


def _instance_table(metrics: list[dict]) -> str:
    if not metrics:
        return '<p class="muted">No metrics.jsonl found for this run.</p>'
    rows = "".join(
        f"<tr><td>{html.escape(m['instance_id'])}</td>"
        f"<td>{'✓' if m.get('resolved') else '✗' if m.get('resolved') is False else '-'}</td>"
        f"<td>{m['turns']}</td><td>{m['total_tokens']:,}</td>"
        f"<td>{m['peak_context']:,}</td><td>{m['cache_efficiency'] * 100:.1f}%</td></tr>"
        for m in metrics
    )
    return f"""
    <table>
      <thead><tr><th>Instance</th><th>Resolved</th><th>Model requests</th>
        <th>Fresh input + output + cache creation</th><th>Peak context</th><th>Cache eff.</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _context_growth_datasets(metrics: list[dict], color_offset: int = 0) -> list[dict]:
    datasets = []
    for i, m in enumerate(metrics):
        per_turn = m.get("per_turn_prompt") or []
        datasets.append({
            "label": m["instance_id"],
            "data": per_turn,
            "borderColor": _color(color_offset + i),
            "backgroundColor": _color(color_offset + i),
            "fill": False,
            "tension": 0.15,
        })
    return datasets


def _cost_datasets(
    usage_rows: list[dict], cost: ModelCost, color_offset: int = 0, label_prefix: str = ""
) -> list[dict]:
    """Cumulative (running-total) USD cost per turn, one line per instance."""
    by_instance: dict[str, list[dict]] = defaultdict(list)
    for r in usage_rows:
        by_instance[r["instance_id"]].append(r)

    datasets = []
    for i, (instance_id, rows) in enumerate(sorted(by_instance.items())):
        rows = sorted(rows, key=lambda r: r["turn_index"])
        cumulative = []
        total = 0.0
        for r in rows:
            total += estimate_cost(
                cost,
                input_tokens=r.get("input_tokens", 0),
                output_tokens=r.get("output_tokens", 0),
                cache_read_tokens=r.get("cache_read_tokens", 0),
                cache_creation_tokens=r.get("cache_creation_tokens", 0),
            )
            cumulative.append(round(total, 6))
        label = f"{label_prefix}{instance_id}" if label_prefix else instance_id
        datasets.append({
            "label": label,
            "data": cumulative,
            "borderColor": _color(color_offset + i),
            "backgroundColor": _color(color_offset + i),
            "fill": False,
            "tension": 0.15,
        })
    return datasets


def _patches_section(runs: list["_RunData"]) -> tuple[str, str]:
    """Build the collapsible patch viewer and its companion JS data blob.

    Returns (html, patches_data_js) where patches_data_js is a self-contained
    ``<script>`` block that assigns ``window.ACB_PATCHES`` -- a nested object
    keyed by instance_id then run_id, holding the raw unified-diff string for
    each (instance, run) pair. The HTML block is inserted into the page body;
    the JS block is injected before the toggle-listener script in _render_html.

    Patches are rendered lazily via diff2html on first expand of each
    ``<details>`` element so large diffs (thousands of lines) do not slow down
    the initial page load.
    """
    # Collect all instance_ids in the order they appear across runs (stable).
    seen: dict[str, None] = {}
    for rd in runs:
        for m in rd.metrics:
            seen[m["instance_id"]] = None
        for iid in rd.predictions:
            seen[iid] = None
    instance_ids = list(seen)
    if not instance_ids:
        return "", ""

    # resolved status: instance_id → run_id → bool|None
    resolved_by: dict[str, dict[str, bool | None]] = {iid: {} for iid in instance_ids}
    for rd in runs:
        for m in rd.metrics:
            resolved_by.setdefault(m["instance_id"], {})[rd.run_id] = m.get("resolved")

    # Build the patches data object for JS (all patches, all runs, all instances)
    patches_data: dict[str, dict[str, str]] = {}
    for iid in instance_ids:
        patches_data[iid] = {}
        for rd in runs:
            patch = rd.predictions.get(iid, "")
            if patch:
                patches_data[iid][rd.run_id] = patch

    patches_data_js = (
        "<script>\n"
        f"window.ACB_PATCHES = {_script_json(patches_data, ensure_ascii=False)};\n"
        "</script>"
    )

    # HTML: one <details> per instance
    col_count = len(runs)
    blocks: list[str] = ["<h2>Patches</h2>"]
    for iid in instance_ids:
        # Summary badges: one per run showing resolved/unresolved
        badges: list[str] = []
        for rd in runs:
            r = resolved_by.get(iid, {}).get(rd.run_id)
            if r is True:
                cls, sym = "badge-resolved", "✓"
            elif r is False:
                cls, sym = "badge-unresolved", "✗"
            else:
                cls, sym = "badge-unknown", "?"
            badges.append(
                f'<span class="badge {cls}">{html.escape(rd.run_id)} {sym}</span>'
            )
        badges_html = f'<span class="run-badges">{"".join(badges)}</span>'

        # Grid columns: one per run
        cols: list[str] = []
        for rd in runs:
            patch = rd.predictions.get(iid, "")
            if patch.strip():
                # Placeholder div -- diff2html fills it in on first expand
                inner = (
                    f'<div class="diff-target" '
                    f'data-run="{html.escape(rd.run_id, quote=True)}" data-iid="{html.escape(iid, quote=True)}"></div>'
                )
            else:
                inner = '<div class="no-patch">no patch produced</div>'
            cols.append(
                f'<div class="patch-col"><h4>{html.escape(rd.run_id)}</h4>{inner}</div>'
            )
        grid = (
            f'<div class="patch-grid" '
            f'style="grid-template-columns:repeat({col_count},minmax(0,1fr))">'
            + "".join(cols)
            + "</div>"
        )

        safe_id = iid.replace("/", "-").replace("_", "-")
        blocks.append(
            f'<details class="patch-block" id="patch-{html.escape(safe_id, quote=True)}">'
            f"<summary>{html.escape(iid)} {badges_html}</summary>"
            f"{grid}"
            f"</details>"
        )

    return "\n".join(blocks), patches_data_js


def _build_single_run_html(rd: _RunData, *, include_summary: bool = True) -> str:
    """Identical to the original single-run HTML report."""
    context_datasets = _context_growth_datasets(rd.metrics)
    turns, avg_input, avg_output, avg_duration = _per_turn_averages(rd.usage_rows)
    has_duration = any(d is not None for d in avg_duration)

    cost_datasets = _cost_datasets(rd.usage_rows, rd.cost) if rd.cost and rd.usage_rows else []
    total_cost = _total_cost(rd.usage_rows, rd.cost) if rd.cost and rd.usage_rows else None

    title = f"acb report: {rd.run_id}"

    duration_chart_html = ""
    duration_chart_js = ""
    if has_duration:
        duration_chart_html = """
    <h2>Duration per model request (avg across instances)</h2>
    <div class="chart-wrap"><canvas id="durationChart"></canvas></div>"""
        duration_chart_js = f"""
    new Chart(document.getElementById('durationChart'), {{
      type: 'line',
      data: {{
        labels: {_script_json(turns)},
        datasets: [{{
          label: 'avg duration (ms)',
          data: {_script_json(avg_duration)},
          borderColor: '{_color(2)}',
          backgroundColor: '{_color(2)}',
          spanGaps: true,
          tension: 0.15,
        }}],
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'model request index' }} }},
          y: {{ title: {{ display: true, text: 'duration (ms)' }}, beginAtZero: true }},
        }},
      }},
    }});"""

    if rd.cost:
        cost_chart_html = """
    <h2>Cost over time (cumulative, per instance)</h2>
    <div class="chart-wrap"><canvas id="costChart"></canvas></div>"""
        cost_chart_js = f"""
    new Chart(document.getElementById('costChart'), {{
      type: 'line',
      data: {{
        labels: {_script_json(list(range(max((len(d["data"]) for d in cost_datasets), default=0))))},
        datasets: {_script_json(cost_datasets)},
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'model request index' }} }},
          y: {{
            title: {{ display: true, text: 'cumulative cost (USD)' }},
            beginAtZero: true,
            ticks: {{ callback: (v) => '$' + v }},
          }},
        }},
      }},
    }});"""
    else:
        model = rd.report.get("model")
        cost_chart_html = (
            f'<h2>Cost over time</h2><p class="muted">No cost data configured for '
            f'model "{model}" in config/costs.yaml -- add input_per_1m/output_per_1m '
            f"rates there to see this chart.</p>"
        )
        cost_chart_js = ""

    # Generate content type visualization if metrics have classification data
    content_type_html = ""
    content_type_js = ""
    tool_usage_pct = None
    
    # Flatten all per-request records from all instances (from benchmark_metrics.jsonl)
    all_requests = []
    for m in rd.metrics:
        all_requests.extend(m.get('requests', []))
    
    metrics_with_classification = [r for r in all_requests if 'content_type' in r]
    
    # Calculate tool usage percentage if we have classification data
    if metrics_with_classification:
        tool_usage_pct = _calculate_tool_usage_percentage(metrics_with_classification)
    if metrics_with_classification:
        content_breakdown = _content_type_breakdown(metrics_with_classification)
        timeline_data = _prepare_timeline_data(metrics_with_classification)
        
        content_type_html = """
    <h2>Content Type Analysis</h2>
    <div class="chart-wrap"><canvas id="contentTypeChart"></canvas></div>
    <div class="chart-wrap"><canvas id="contentTimelineChart"></canvas></div>"""
        
        if content_breakdown['tool_usage']:
            # Build tool usage table - sorted by total tokens (descending)
            tool_rows = ""
            sorted_tools = sorted(
                content_breakdown['tool_usage'].items(),
                key=lambda x: x[1]['total_tokens'],
                reverse=True
            )
            for tool_key, data in sorted_tools:
                interactions = data['interactions']
                avg_tokens = data['total_tokens'] / interactions if interactions else 0
                tool_rows += f"""
        <tr>
          <td>{html.escape(str(data['tool_name']))}</td>
          <td>{html.escape(str(data['tool_detail'] or '—'))}</td>
          <td>{interactions}</td>
          <td>{data['complete_pairs']}</td>
          <td>{data['call_only']}</td>
          <td>{data['result_only']}</td>
          <td>{data['total_tokens']:,.0f}</td>
          <td>{avg_tokens:,.0f}</td>
        </tr>"""
            content_type_html += f"""
    <h3>Tool Usage Statistics</h3>
    <div class="chart-wrap">
      <table>
        <thead>
          <tr>
            <th>Tool Name</th>
            <th>Detail</th>
            <th>Interactions</th>
            <th>Complete Pairs</th>
            <th>Call Only</th>
            <th>Result Only</th>
            <th>Total Tokens</th>
            <th>Avg Tokens/Interaction</th>
          </tr>
        </thead>
        <tbody>{tool_rows}
        </tbody>
      </table>
    </div>"""
        
        # Add shell command pie chart if shell commands exist
        shell_commands = _shell_command_breakdown(metrics_with_classification)
        if shell_commands:
            shell_cmd_labels = list(shell_commands.keys())
            shell_cmd_tokens = [cmd['total_tokens'] for cmd in shell_commands.values()]
            
            content_type_html += """
    <h3>Shell Command Distribution</h3>
    <div class="chart-wrap"><canvas id="shellCommandChart"></canvas></div>"""
        
        content_type_data = _script_json(content_breakdown['by_type'])
        timeline_data_json = _script_json(timeline_data)
        
        # Build shell command chart JS if shell commands exist
        shell_cmd_js = ""
        if shell_commands:
            shell_cmd_labels = list(shell_commands.keys())
            shell_cmd_tokens = [cmd['total_tokens'] for cmd in shell_commands.values()]
            shell_cmd_js = f"""
    // Shell Command Distribution (Pie Chart)
    new Chart(document.getElementById('shellCommandChart'), {{
      type: 'pie',
      data: {{
        labels: {_script_json(shell_cmd_labels)},
        datasets: [{{
          data: {_script_json(shell_cmd_tokens)},
          backgroundColor: {_script_json(_COLORS[:len(shell_cmd_labels)])},
        }}],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: true,
        plugins: {{
          title: {{
            display: true,
            text: 'Shell Command Token Distribution',
            font: {{ size: 16 }}
          }},
          legend: {{
            position: 'right',
            labels: {{
              font: {{ size: 12 }}
            }}
          }},
          tooltip: {{
            callbacks: {{
              label: function(context) {{
                let label = context.label || '';
                let value = context.parsed || 0;
                let total = context.dataset.data.reduce((a, b) => a + b, 0);
                let percentage = ((value / total) * 100).toFixed(1);
                return label + ': ' + value.toLocaleString() + ' tokens (' + percentage + '%)';
              }}
            }}
          }}
        }}
      }}
    }});
"""
        
        content_type_js = f"""
    // Content Type Distribution (Pie Chart)
    new Chart(document.getElementById('contentTypeChart'), {{
      type: 'pie',
      data: {{
        labels: {_script_json(list(content_breakdown['by_type'].keys()))},
        datasets: [{{
          data: {_script_json([v['total_tokens'] for v in content_breakdown['by_type'].values()])},
          backgroundColor: ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#999999'],
        }}],
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'right' }},
          title: {{ display: true, text: 'Total Tokens by Content Type' }}
        }}
      }}
    }});

    // Content Type Timeline (Stacked Bar)
    const timelineData = {timeline_data_json};
    new Chart(document.getElementById('contentTimelineChart'), {{
      type: 'bar',
      data: timelineData,
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'Turn' }} }},
          y: {{ stacked: true, title: {{ display: true, text: 'Tokens' }} }}
        }},
        plugins: {{
          tooltip: {{
            callbacks: {{
              title: function(context) {{ return 'Request ' + context[0].label; }}
            }}
          }}
        }}
      }}
    }});"""
        
        # Append shell command chart JS if it exists
        content_type_js += shell_cmd_js

    patches_html, patches_data_js = _patches_section([rd])
    return _render_html(
        title=title,
        heading=rd.run_id,
        meta=f"{html.escape(str(rd.report.get('benchmark', '?')))} &middot; {html.escape(str(rd.report.get('harness', '?')))}"
             f" &middot; {html.escape(str(rd.report.get('model', '?')))} &middot; proxy: {html.escape(str(rd.report.get('proxy', '?')))}",
        summary_html=_summary_cards(rd.report, total_cost, tool_usage_pct) if include_summary else "",
        context_datasets=context_datasets,
        turns=turns,
        avg_input=avg_input,
        avg_output=avg_output,
        duration_chart_html=duration_chart_html,
        duration_chart_js=duration_chart_js,
        cost_chart_html=cost_chart_html,
        cost_chart_js=cost_chart_js,
        content_type_html=content_type_html,
        content_type_js=content_type_js,
        instances_html=f"<h2>Instances</h2>{_instance_table(rd.metrics)}",
        patches_html=patches_html,
        patches_data_js=patches_data_js,
        tokens_stacked=False,
        resolve_chart_html="",
        resolve_chart_js="",
    )


# ---------------------------------------------------------------------------
# Multi-run HTML builders
# ---------------------------------------------------------------------------

def _render_html(
    *,
    title: str,
    heading: str,
    meta: str,
    summary_html: str,
    context_datasets: list[dict],
    turns: list[int],
    avg_input: list[float],
    avg_output: list[float],
    duration_chart_html: str,
    duration_chart_js: str,
    cost_chart_html: str,
    cost_chart_js: str,
    instances_html: str,
    patches_html: str = "",
    patches_data_js: str = "",
    tokens_stacked: bool,
    tokens_datasets_override: list[dict] | None = None,
    resolve_chart_html: str = "",
    resolve_chart_js: str = "",
    content_type_html: str = "",
    content_type_js: str = "",
) -> str:
    max_context_len = max((len(d["data"]) for d in context_datasets), default=0)
    context_labels = list(range(max_context_len))

    if tokens_datasets_override is not None:
        tokens_datasets_js = _script_json(tokens_datasets_override)
    else:
        tokens_datasets_js = _script_json([
            {"label": "input tokens", "data": avg_input, "backgroundColor": _color(0)},
            {"label": "output tokens", "data": avg_output, "backgroundColor": _color(1)},
        ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/diff2html/bundles/css/diff2html.min.css">
<script src="https://cdn.jsdelivr.net/npm/diff2html/bundles/js/diff2html-ui.min.js"></script>
<script src="{_CHART_JS_CDN}"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          margin: 0; padding: 2rem; background: #f7f7f9; color: #1a1a1a; }}
  h1 {{ margin-top: 0; }}
  h2 {{ margin-top: 2.5rem; font-size: 1.1rem; color: #333; }}
  h3 {{ margin-top: 1.5rem; font-size: 0.95rem; color: #555; }}
  .muted {{ color: #888; }}
  .meta {{ color: #666; margin-bottom: 1.5rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: white; border-radius: 8px; padding: 1rem 1.25rem;
           box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .card-label {{ font-size: 0.8rem; color: #888; text-transform: uppercase;
                 letter-spacing: 0.03em; }}
  .card-value {{ font-size: 1.6rem; font-weight: 600; margin-top: 0.25rem; }}
  .chart-wrap {{ background: white; border-radius: 8px; padding: 1.5rem;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.08); max-width: 900px; }}
  table {{ border-collapse: collapse; background: white; border-radius: 8px;
           overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  th, td {{ padding: 0.5rem 1rem; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ background: #fafafa; font-size: 0.8rem; text-transform: uppercase;
        color: #888; letter-spacing: 0.03em; }}
  tr:last-child td {{ border-bottom: none; }}
  /* --- patch viewer --- */
  details.patch-block {{
    background: white; border-radius: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    margin-bottom: 0.75rem; overflow: hidden;
  }}
  details.patch-block > summary {{
    cursor: pointer; padding: 0.75rem 1rem;
    font-weight: 500; list-style: none;
    display: flex; align-items: center; gap: 1rem;
    user-select: none;
  }}
  details.patch-block > summary::-webkit-details-marker {{ display: none; }}
  details.patch-block > summary::before {{
    content: '▶'; font-size: 0.7rem; color: #888;
    transition: transform 0.15s; flex-shrink: 0;
  }}
  details.patch-block[open] > summary::before {{ transform: rotate(90deg); }}
  .run-badges {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .badge {{ font-size: 0.78rem; padding: 0.15rem 0.5rem;
            border-radius: 4px; font-weight: 500; white-space: nowrap; }}
  .badge-resolved   {{ background: #e6f4ea; color: #2a7a2a; }}
  .badge-unresolved {{ background: #fce8e6; color: #c0392b; }}
  .badge-unknown    {{ background: #f1f1f1; color: #888; }}
  .patch-grid {{ display: grid; gap: 1rem; padding: 0 1rem 1rem; }}
   .patch-col > h4 {{
     font-size: 0.82rem; color: #555; margin: 0.75rem 0 0.4rem;
     text-transform: uppercase; letter-spacing: 0.04em;
   }}
   .no-patch {{
     color: #aaa; font-style: italic; font-size: 0.85rem;
     padding: 1rem; border: 1px dashed #e0e0e0; border-radius: 4px;
   }}
   .patch-col .d2h-wrapper {{ overflow-x: auto; }}
   .patch-col .d2h-file-header {{ font-size: 0.8rem; }}
    /* --- content type analysis --- */
    .tool-usage-table {{ width: 100%; border-collapse: collapse; }}
    .tool-usage-table th,
    .tool-usage-table td {{ padding: 0.5rem 1rem; text-align: left; border-bottom: 1px solid #eee; }}
    .tool-usage-table th {{ background: #fafafa; font-size: 0.8rem; text-transform: uppercase; color: #888; }}
    .tool-usage-table tr:hover {{ background: #f9f9f9; }}
    /* Tool type row styling */
    tr.shell-tool {{ background-color: #f8f9fa; }}
    tr.agent-tool {{ background-color: #ffffff; }}
    /* Info icon for shell command breakdown */
    .info-icon {{
      cursor: help;
      font-size: 16px;
      color: #4e79a7;
      user-select: none;
    }}
    .info-icon:hover {{
      color: #2b5a8a;
    }}
    .info-cell {{
      text-align: center;
    }}
    /* Tooltip styling for summary cards */
    .tooltip-trigger {{
      position: relative;
      cursor: help;
      border-bottom: 1px dotted #666;
    }}
    .tooltip-trigger .tooltip-text {{
      visibility: hidden;
      background-color: rgba(0, 0, 0, 0.8);
      color: #fff;
      text-align: left;
      padding: 8px 12px;
      border-radius: 4px;
      position: absolute;
      z-index: 1000;
      bottom: 125%;
      left: 50%;
      transform: translateX(-50%);
      white-space: nowrap;
      font-size: 12px;
      font-weight: normal;
    }}
    .tooltip-trigger:hover .tooltip-text {{
      visibility: visible;
    }}
    .tooltip-trigger .tooltip-text::after {{
      content: "";
      position: absolute;
      top: 100%;
      left: 50%;
      margin-left: -5px;
      border-width: 5px;
      border-style: solid;
      border-color: rgba(0, 0, 0, 0.8) transparent transparent transparent;
    }}
  </style>
</head>
<body>
  <h1>{html.escape(heading)}</h1>
  <div class="meta">{meta}</div>

  {summary_html}

  {resolve_chart_html}

  <h2>Context growth over model requests (prompt size = input + cache_read + cache_creation)</h2>
  <div class="chart-wrap"><canvas id="contextGrowthChart"></canvas></div>

   <h2>Tokens per model request (avg across instances)</h2>
   <div class="chart-wrap"><canvas id="tokensPerTurnChart"></canvas></div>
   {duration_chart_html}
   {cost_chart_html}
   {content_type_html}

   {instances_html}

   {patches_html}

{patches_data_js}
<script>
  {resolve_chart_js}
  new Chart(document.getElementById('contextGrowthChart'), {{
    type: 'line',
    data: {{
      labels: {_script_json(context_labels)},
      datasets: {_script_json(context_datasets)},
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ title: {{ display: true, text: 'model request index' }} }},
        y: {{ title: {{ display: true, text: 'prompt tokens' }}, beginAtZero: true }},
      }},
    }},
  }});

  new Chart(document.getElementById('tokensPerTurnChart'), {{
    type: 'bar',
    data: {{
      labels: {_script_json(turns)},
      datasets: {tokens_datasets_js},
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ title: {{ display: true, text: 'model request index' }}, stacked: false }},
        y: {{ title: {{ display: true, text: 'tokens' }}, beginAtZero: true }},
      }},
    }},
   }});
   {duration_chart_js}
   {cost_chart_js}
   {content_type_js}

   // Lazy-render diff2html on first expand of each patch block
  document.querySelectorAll('details.patch-block').forEach(function(el) {{
    el.addEventListener('toggle', function() {{
      if (!this.open || this.dataset.rendered) return;
      this.dataset.rendered = '1';
      var patches = window.ACB_PATCHES || {{}};
      this.querySelectorAll('.diff-target').forEach(function(target) {{
        var iid = target.dataset.iid;
        var run = target.dataset.run;
        var patch = (patches[iid] || {{}})[run] || '';
        if (!patch.trim()) return;
        var ui = new Diff2HtmlUI(target, patch, {{
          drawFileList: false,
          outputFormat: 'line-by-line',
          matching: 'lines',
          highlight: false,
        }});
        ui.draw();
      }});
    }});
  }});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _comparison_payload(baseline_dir: Path, candidate_dir: Path) -> dict:
    from acb.comparison import compare
    return compare(baseline_dir, candidate_dir)


def build_html_report(run_dirs: str | Path | list[str | Path]) -> str:
    """Render shared comparison semantics in memory.

    Use comparison_html.write_reports for a bundle with standalone benchmark
    pages and navigation links. This API has no filesystem side effects.
    """
    from acb.comparison import benchmarks, compare
    from acb.comparison_html import detail_html, page, render_comparison
    roots = [Path(run_dirs)] if isinstance(run_dirs, (str, Path)) else list(map(Path, run_dirs))
    if not roots:
        raise ValueError('at least one run is required')
    if len(roots) > 1:
        bodies = [render_comparison(compare(roots[0], root)).split('<body>', 1)[1].rsplit('</body>', 1)[0]
                  for root in roots[1:]]
        return page('Benchmark comparisons', ''.join(bodies))
    records = benchmarks(roots[0])
    # Embed full individual documents without merging their chart element IDs.
    frames = ''.join('<iframe title="'+html.escape(record['benchmark'], quote=True)+
                     '" style="width:100%;height:1000px;border:0" srcdoc="'+
                     html.escape(detail_html(record), quote=True)+'"></iframe>'
                     for record in records.values())
    return page('Individual benchmark reports', '<h1>Individual benchmark reports</h1>'+frames)
