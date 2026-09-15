"""Self-contained HTML visualization of one or more run reports + usage data.

Single-run mode (`build_html_report(run_dir)`):
  Reads `report.json`, `metrics.jsonl`, `usage.jsonl` from that directory and
  renders per-instance charts (context growth = one line per instance, etc.).

Multi-run mode (`build_html_report([run_dir_a, run_dir_b, ...])`):
  Loads the same files from each directory and renders a combined comparison
  view.  Context-growth and cost charts overlay *all* per-instance lines from
  all runs (labelled "{run_id}: {instance_id}").  Token-per-turn and duration
  charts show one averaged series per run.  A summary comparison table
  replaces the single-run metric cards, and per-instance tables are grouped
  under a heading for each run.

Charts render via Chart.js loaded from a CDN -- viewing the report needs
internet access for the charts; everything else is inline.
"""

from __future__ import annotations

import json
import html
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acb.costs import ModelCost, estimate_cost, load_cost_table
from acb.usage import read_records, normalize_benchmark_metric

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
        tool.get("call_id")
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
                key = f"call:{call_id}"
            else:
                key = f"record:{metric.get('request_id', anonymous_index)}:{tool_index}"
                anonymous_index += 1
            interaction = interactions.setdefault(key, {
                "name": tool.get("name") or "unknown",
                "detail": tool.get("detail"),
                "call_id": call_id,
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
        if interaction.get("call_id") in result_call_ids:
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
        'labels': [f"Turn {t}" for t in turns],
        'datasets': datasets
    }


def _content_type_suite_breakdown(runs: list["_RunData"]) -> dict:
    """Aggregate content type data across all harnesses in a suite.
    
    Returns:
        {
            'by_harness': { harness_name: content_breakdown },
            'aggregate': aggregate_breakdown,
        }
    """
    by_harness = {}
    all_requests_aggregate = []
    
    for rd in runs:
        # Flatten all per-request records from this harness
        harness_requests = []
        for m in rd.metrics:
            harness_requests.extend(m.get('requests', []))
        
        all_requests_aggregate.extend(harness_requests)
        
        # Aggregate for this harness
        if harness_requests:
            harness_breakdown = _content_type_breakdown(harness_requests)
            by_harness[rd.run_id] = harness_breakdown
    
    # Aggregate across all harnesses
    aggregate_breakdown = _content_type_breakdown(all_requests_aggregate) if all_requests_aggregate else {'by_type': {}, 'tool_usage': {}}
    
    return {
        'by_harness': by_harness,
        'aggregate': aggregate_breakdown,
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


def _shell_command_suite_breakdown(runs: list["_RunData"]) -> dict:
    """Aggregate shell command data across all harnesses in a suite.
    
    Returns:
        {
            'rgctl': {
                'total_calls': 47,
                'total_tokens': 125000,
                'by_harness': {
                    'harness_a': {'calls': 15, 'tokens': 40000},
                    'harness_b': {'calls': 20, 'tokens': 55000},
                    'harness_c': {'calls': 12, 'tokens': 30000}
                }
            },
            ...
        }
        Sorted by total_tokens (descending)
    """
    all_shell_commands = {}
    
    for rd in runs:
        harness_name = rd.harness_name or rd.run_id
        
        # Get all requests for this harness
        harness_requests = []
        for m in rd.metrics:
            harness_requests.extend(m.get('requests', []))
        
        # Extract shell commands for this harness
        shell_cmds = _shell_command_breakdown(harness_requests)
        
        # Aggregate into all_shell_commands
        for cmd, data in shell_cmds.items():
            if cmd not in all_shell_commands:
                all_shell_commands[cmd] = {
                    'total_calls': 0,
                    'total_tokens': 0,
                    'by_harness': {}
                }
            
            all_shell_commands[cmd]['total_calls'] += data['calls']
            all_shell_commands[cmd]['total_tokens'] += data['total_tokens']
            all_shell_commands[cmd]['by_harness'][harness_name] = {
                'calls': data['calls'],
                'tokens': data['total_tokens']
            }
    
    # Sort by total tokens (descending)
    return dict(sorted(all_shell_commands.items(), key=lambda x: x[1]['total_tokens'], reverse=True))


# ---------------------------------------------------------------------------
# Single-run HTML builders
# ---------------------------------------------------------------------------

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
        f"<tr><td>{m['instance_id']}</td>"
        f"<td>{'✓' if m.get('resolved') else '✗' if m.get('resolved') is False else '-'}</td>"
        f"<td>{m['turns']}</td><td>{m['total_tokens']:,}</td>"
        f"<td>{m['peak_context']:,}</td><td>{m['cache_efficiency'] * 100:.1f}%</td></tr>"
        for m in metrics
    )
    return f"""
    <table>
      <thead><tr><th>Instance</th><th>Resolved</th><th>Turns</th>
        <th>Total tokens</th><th>Peak context</th><th>Cache eff.</th></tr></thead>
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
        f"window.ACB_PATCHES = {json.dumps(patches_data, ensure_ascii=False)};\n"
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
                f'<span class="badge {cls}">{rd.run_id} {sym}</span>'
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
                    f'data-run="{rd.run_id}" data-iid="{iid}"></div>'
                )
            else:
                inner = '<div class="no-patch">no patch produced</div>'
            cols.append(
                f'<div class="patch-col"><h4>{rd.run_id}</h4>{inner}</div>'
            )
        grid = (
            f'<div class="patch-grid" '
            f'style="grid-template-columns:repeat({col_count},minmax(0,1fr))">'
            + "".join(cols)
            + "</div>"
        )

        safe_id = iid.replace("/", "-").replace("_", "-")
        blocks.append(
            f'<details class="patch-block" id="patch-{safe_id}">'
            f"<summary>{iid} {badges_html}</summary>"
            f"{grid}"
            f"</details>"
        )

    return "\n".join(blocks), patches_data_js


def _build_single_run_html(rd: _RunData) -> str:
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
    <h2>Request duration per turn (avg across instances)</h2>
    <div class="chart-wrap"><canvas id="durationChart"></canvas></div>"""
        duration_chart_js = f"""
    new Chart(document.getElementById('durationChart'), {{
      type: 'line',
      data: {{
        labels: {json.dumps(turns)},
        datasets: [{{
          label: 'avg duration (ms)',
          data: {json.dumps(avg_duration)},
          borderColor: '{_color(2)}',
          backgroundColor: '{_color(2)}',
          spanGaps: true,
          tension: 0.15,
        }}],
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'turn index' }} }},
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
        labels: {json.dumps(list(range(max((len(d["data"]) for d in cost_datasets), default=0))))},
        datasets: {json.dumps(cost_datasets)},
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'turn index' }} }},
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
          <td>{data['tool_name']}</td>
          <td>{data['tool_detail'] or '—'}</td>
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
        
        content_type_data = json.dumps(content_breakdown['by_type'])
        timeline_data_json = json.dumps(timeline_data)
        
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
        labels: {json.dumps(shell_cmd_labels)},
        datasets: [{{
          data: {json.dumps(shell_cmd_tokens)},
          backgroundColor: {json.dumps(_COLORS[:len(shell_cmd_labels)])},
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
        labels: {json.dumps(list(content_breakdown['by_type'].keys()))},
        datasets: [{{
          data: {json.dumps([v['total_tokens'] for v in content_breakdown['by_type'].values()])},
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
              title: function(context) {{ return 'Turn ' + context[0].label; }}
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
        meta=f"{rd.report.get('benchmark', '?')} &middot; {rd.report.get('harness', '?')}"
             f" &middot; {rd.report.get('model', '?')} &middot; proxy: {rd.report.get('proxy', '?')}",
        summary_html=_summary_cards(rd.report, total_cost, tool_usage_pct),
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

def _comparison_table(runs: list[_RunData], is_suite: bool = False) -> str:
    """Summary table with one row per run (or harness in suite mode)."""
    if is_suite:
        # Suite mode: focus on harness name instead of generic "Run"
        cols = [
            ("Harness", lambda r: r.run_id),
            ("Instances", lambda r: str(r.report.get("instances", "-"))),
            ("Resolved", lambda r: str(r.report.get("resolved", "-"))),
            ("Resolve rate", lambda r: f"{r.report.get('resolve_rate', 0) * 100:.0f}%"),
            ("Avg total tokens", lambda r: f"{r.report.get('avg_total_tokens', 0):,.0f}"),
            ("Avg turns", lambda r: f"{r.report.get('avg_turns', 0):.1f}"),
            ("Avg peak context", lambda r: f"{r.report.get('avg_peak_context', 0):,.0f}"),
            ("Avg cache eff.", lambda r: f"{r.report.get('avg_cache_efficiency', 0) * 100:.1f}%"),
            ("Tokens/resolved", lambda r: (
                f"{r.report['tokens_per_resolved']:,.0f}"
                if r.report.get("tokens_per_resolved") is not None else "n/a"
            )),
        ]
    else:
        # Multi-run mode: include run-specific columns
        cols = [
            ("Run", lambda r: r.run_id),
            ("Harness", lambda r: r.report.get("harness", "-")),
            ("Model", lambda r: r.report.get("model", "-")),
            ("Benchmark", lambda r: r.report.get("benchmark", "-")),
            ("Instances", lambda r: str(r.report.get("instances", "-"))),
            ("Resolved", lambda r: str(r.report.get("resolved", "-"))),
            ("Resolve rate", lambda r: f"{r.report.get('resolve_rate', 0) * 100:.0f}%"),
            ("Avg total tokens", lambda r: f"{r.report.get('avg_total_tokens', 0):,.0f}"),
            ("Avg turns", lambda r: f"{r.report.get('avg_turns', 0):.1f}"),
            ("Avg peak context", lambda r: f"{r.report.get('avg_peak_context', 0):,.0f}"),
            ("Avg cache eff.", lambda r: f"{r.report.get('avg_cache_efficiency', 0) * 100:.1f}%"),
            ("Tokens/resolved", lambda r: (
                f"{r.report['tokens_per_resolved']:,.0f}"
                if r.report.get("tokens_per_resolved") is not None else "n/a"
            )),
        ]
    headers = "".join(f"<th>{h}</th>" for h, _ in cols)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{fn(rd)}</td>" for _, fn in cols) + "</tr>"
        for rd in runs
    )
    return f"""
    <table style="width:100%;max-width:none">
      <thead><tr>{headers}</tr></thead>
      <tbody>{body_rows}</tbody>
    </table>"""


def _resolve_rate_bar_chart(runs: list[_RunData]) -> str:
    """Build HTML for a resolve rate bar chart (suite view)."""
    harnesses = [r.run_id for r in runs]
    rates = [r.report.get("resolve_rate", 0) * 100 for r in runs]
    
    chart_html = """
    <h2>Resolve Rate by Harness</h2>
    <div class="chart-wrap"><canvas id="resolveRateChart"></canvas></div>"""
    
    chart_js = f"""
    new Chart(document.getElementById('resolveRateChart'), {{
      type: 'bar',
      data: {{
        labels: {json.dumps(harnesses)},
        datasets: [{{
          label: 'resolve rate',
          data: {json.dumps(rates)},
          backgroundColor: {json.dumps([_color(i) for i in range(len(runs))])},
        }}],
      }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        scales: {{
          x: {{
            title: {{ display: true, text: 'resolve rate (%)' }},
            min: 0,
            max: 100,
            ticks: {{ callback: (v) => v + '%' }},
          }},
        }},
      }},
    }});"""
    
    return chart_html, chart_js


def _unified_instance_table(runs: list[_RunData]) -> str:
    """Build a unified table comparing all instances across all harnesses.
    
    Rows are instances, columns are harnesses. Each cell shows resolve status
    and token count.
    """
    # Collect all instance IDs across all harnesses
    all_instances: dict[str, None] = {}
    for rd in runs:
        for m in rd.metrics:
            all_instances[m["instance_id"]] = None
    instance_ids = list(all_instances.keys())
    
    if not instance_ids:
        return "<p class='muted'>No instance metrics found.</p>"
    
    # Build harness columns
    harness_headers = "".join(f"<th>{r.run_id}</th>" for r in runs)
    
    # Build rows
    rows = []
    for iid in instance_ids:
        cells = [f"<td>{iid}</td>"]
        for rd in runs:
            # Find metric for this instance in this harness
            metric = next((m for m in rd.metrics if m["instance_id"] == iid), None)
            if metric:
                status = "✓" if metric.get("resolved") else "✗" if metric.get("resolved") is False else "?"
                tokens = f"{metric.get('total_tokens', 0):,}"
                cells.append(f"<td>{status} {tokens}</td>")
            else:
                cells.append("<td>-</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    
    return f"""
    <table style="width:100%;max-width:none">
      <thead><tr><th>Instance</th>{harness_headers}</tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _build_multi_run_html(runs: list[_RunData], is_suite: bool = False) -> str:
    """Combined report with all runs overlaid on the same charts.
    
    If is_suite=True, treats runs as harnesses in a suite and adjusts labels
    and structure accordingly.
    """
    # --- context growth: all per-instance lines from all runs ---
    context_datasets: list[dict] = []
    color_idx = 0
    for rd in runs:
        for m in rd.metrics:
            per_turn = m.get("per_turn_prompt") or []
            if is_suite:
                label = f"{rd.run_id}: {m['instance_id']}"
            else:
                label = f"{rd.run_id}: {m['instance_id']}"
            context_datasets.append({
                "label": label,
                "data": per_turn,
                "borderColor": _color(color_idx),
                "backgroundColor": _color(color_idx),
                "fill": False,
                "tension": 0.15,
            })
            color_idx += 1

    # --- tokens per turn: one avg pair (input/output) per run ---
    tokens_datasets: list[dict] = []
    all_turns: set[int] = set()
    run_turn_data: list[tuple[str, list[int], list[float], list[float]]] = []
    for i, rd in enumerate(runs):
        t_turns, avg_input, avg_output, _ = _per_turn_averages(rd.usage_rows)
        all_turns.update(t_turns)
        run_turn_data.append((rd.run_id, t_turns, avg_input, avg_output))
    turns = sorted(all_turns)

    for i, (run_id, t_turns, avg_input, avg_output) in enumerate(run_turn_data):
        # pad to common length; missing turns default to 0
        turn_to_input = dict(zip(t_turns, avg_input))
        turn_to_output = dict(zip(t_turns, avg_output))
        padded_input = [turn_to_input.get(t, 0) for t in turns]
        padded_output = [turn_to_output.get(t, 0) for t in turns]
        tokens_datasets.append({
            "label": f"{run_id} input",
            "data": padded_input,
            "backgroundColor": _color(i * 2),
        })
        tokens_datasets.append({
            "label": f"{run_id} output",
            "data": padded_output,
            "backgroundColor": _color(i * 2 + 1),
        })

    # --- duration: one averaged line per run ---
    duration_datasets: list[dict] = []
    has_duration = False
    for i, rd in enumerate(runs):
        t_turns, _, _, avg_duration = _per_turn_averages(rd.usage_rows)
        if any(d is not None for d in avg_duration):
            has_duration = True
            turn_to_dur = dict(zip(t_turns, avg_duration))
            padded_dur = [turn_to_dur.get(t) for t in turns]
            duration_datasets.append({
                "label": rd.run_id,
                "data": padded_dur,
                "borderColor": _color(i),
                "backgroundColor": _color(i),
                "spanGaps": True,
                "tension": 0.15,
            })

    # --- cost: all per-instance lines from all runs ---
    cost_datasets: list[dict] = []
    any_cost = False
    color_idx = 0
    for rd in runs:
        if rd.cost and rd.usage_rows:
            any_cost = True
            ds = _cost_datasets(rd.usage_rows, rd.cost,
                                 color_offset=color_idx,
                                 label_prefix=f"{rd.run_id}: ")
            cost_datasets.extend(ds)
            color_idx += len(ds)

    # --- assemble HTML sections ---
    if is_suite:
        # Suite mode: different heading/meta
        benchmark = runs[0].report.get("benchmark", "?")
        model = runs[0].report.get("model", "?")
        title = f"acb suite: {benchmark} × {model}"
        heading = "Suite Comparison"
        meta = f"{benchmark} &middot; {model}"
        summary_html_title = "Summary by Harness"
    else:
        run_names = " vs ".join(rd.run_id for rd in runs)
        title = f"acb comparison: {run_names}"
        heading = "Comparison"
        meta = run_names
        summary_html_title = "Summary"

    # summary
    summary_html = f"<h2>{summary_html_title}</h2>{_comparison_table(runs, is_suite=is_suite)}"
    
    # resolve rate bar chart (suite view only)
    resolve_chart_html = ""
    resolve_chart_js = ""
    if is_suite:
        resolve_chart_html, resolve_chart_js = _resolve_rate_bar_chart(runs)

    # duration chart
    duration_chart_html = ""
    duration_chart_js = ""
    if has_duration:
        duration_chart_html = """
    <h2>Request duration per turn (avg per harness)</h2>
    <div class="chart-wrap"><canvas id="durationChart"></canvas></div>"""
        duration_chart_js = f"""
    new Chart(document.getElementById('durationChart'), {{
      type: 'line',
      data: {{
        labels: {json.dumps(turns)},
        datasets: {json.dumps(duration_datasets)},
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'turn index' }} }},
          y: {{ title: {{ display: true, text: 'duration (ms)' }}, beginAtZero: true }},
        }},
      }},
    }});"""

    # cost chart
    if any_cost:
        max_cost_len = max((len(d["data"]) for d in cost_datasets), default=0)
        cost_chart_html = """
    <h2>Cost over time (cumulative, per instance)</h2>
    <div class="chart-wrap"><canvas id="costChart"></canvas></div>"""
        cost_chart_js = f"""
    new Chart(document.getElementById('costChart'), {{
      type: 'line',
      data: {{
        labels: {json.dumps(list(range(max_cost_len)))},
        datasets: {json.dumps(cost_datasets)},
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'turn index' }} }},
          y: {{
            title: {{ display: true, text: 'cumulative cost (USD)' }},
            beginAtZero: true,
            ticks: {{ callback: (v) => '$' + v }},
          }},
        }},
      }},
    }});"""
    else:
        cost_chart_html = '<h2>Cost over time</h2><p class="muted">No cost data configured for any harness.</p>'
        cost_chart_js = ""

    # instance tables
    if is_suite:
        # Suite mode: unified table across harnesses
        instances_html = f"<h2>Instances</h2>{_unified_instance_table(runs)}"
    else:
        # Multi-run mode: separate tables per run
        instances_html_parts = ["<h2>Instances</h2>"]
        for rd in runs:
            instances_html_parts.append(f"<h3>{rd.run_id}</h3>")
            instances_html_parts.append(_instance_table(rd.metrics))
        instances_html = "\n".join(instances_html_parts)

    # Content type visualization for suite/multi-run
    content_type_html = ""
    content_type_js = ""
    
    # Check if any run has request data with content classification
    has_any_requests = any(
        any(m.get('requests') for m in rd.metrics)
        for rd in runs
    )
    
    if has_any_requests:
        suite_breakdown = _content_type_suite_breakdown(runs)
        
        # Build aggregate pie chart
        if suite_breakdown['aggregate']['by_type']:
            content_type_html = f"""
    <h2>Content Type Analysis</h2>
    <div class="chart-wrap"><canvas id="contentTypeChart"></canvas></div>"""
            
            # Per-harness comparison if we have multiple harnesses
            if len(runs) > 1 and suite_breakdown['by_harness']:
                content_type_html += f"""
    <h3>Content Type Distribution by Harness</h3>
    <div class="chart-wrap"><canvas id="harnessContentTypeChart"></canvas></div>"""
            
            # Tool usage table if available
            all_tool_usage = {}
            for harness_data in suite_breakdown['by_harness'].values():
                for tool_key, tool_data in harness_data['tool_usage'].items():
                    if tool_key not in all_tool_usage:
                        all_tool_usage[tool_key] = {
                            'tool_name': tool_data['tool_name'],
                            'tool_detail': tool_data['tool_detail'],
                            'interactions': 0,
                            'complete_pairs': 0,
                            'call_only': 0,
                            'result_only': 0,
                            'total_tokens': 0,
                        }
                    all_tool_usage[tool_key]['interactions'] += tool_data['interactions']
                    all_tool_usage[tool_key]['complete_pairs'] += tool_data['complete_pairs']
                    all_tool_usage[tool_key]['call_only'] += tool_data['call_only']
                    all_tool_usage[tool_key]['result_only'] += tool_data['result_only']
                    all_tool_usage[tool_key]['total_tokens'] += tool_data['total_tokens']
            
            if all_tool_usage:
                tool_rows = ""
                # Sort by total tokens (descending)
                sorted_tool_usage = sorted(
                    all_tool_usage.items(),
                    key=lambda x: x[1]['total_tokens'],
                    reverse=True
                )
                for tool_key, data in sorted_tool_usage:
                    avg_tokens = data['total_tokens'] / data['interactions'] if data['interactions'] else 0
                    tool_rows += f"""
        <tr>
          <td>{data['tool_name']}</td>
          <td>{data['tool_detail'] or '—'}</td>
          <td>{data['interactions']}</td>
          <td>{data['complete_pairs']}</td>
          <td>{data['call_only']}</td>
          <td>{data['result_only']}</td>
          <td>{data['total_tokens']:,.0f}</td>
          <td>{avg_tokens:,.0f}</td>
        </tr>"""
                content_type_html += f"""
    <h3>Tool Usage Statistics (Suite Total)</h3>
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
            
            # Shell command suite breakdown if available
            shell_cmd_suite = _shell_command_suite_breakdown(runs)
            if shell_cmd_suite:
                shell_rows = ""
                
                for cmd, data in shell_cmd_suite.items():
                    # Build popover data for Chart.js tooltip
                    harness_breakdown = []
                    for harness, stats in data['by_harness'].items():
                        harness_breakdown.append({
                            'harness': harness,
                            'calls': stats['calls'],
                            'tokens': stats['tokens']
                        })
                    
                    # Store in data attribute for Chart.js tooltip
                    breakdown_json = json.dumps(harness_breakdown).replace('"', '&quot;')
                    
                    shell_rows += f"""
        <tr data-breakdown='{breakdown_json}'>
          <td>{cmd}</td>
          <td>{data['total_calls']}</td>
          <td>{data['total_tokens']:,}</td>
          <td class="info-cell"><span class="info-icon">ℹ️</span></td>
        </tr>"""
                
                content_type_html += f"""
    <h3>Shell Command Usage (Suite Total)</h3>
    <div class="chart-wrap">
      <table id="shellCommandSuiteTable">
        <thead>
          <tr>
            <th>Command</th>
            <th>Total Calls</th>
            <th>Total Tokens</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>{shell_rows}
        </tbody>
      </table>
    </div>"""
            
            # Generate JavaScript for charts
            content_type_js = f"""
    // Aggregate Content Type Distribution (Pie Chart)
    new Chart(document.getElementById('contentTypeChart'), {{
      type: 'pie',
      data: {{
        labels: {json.dumps(list(suite_breakdown['aggregate']['by_type'].keys()))},
        datasets: [{{
          data: {json.dumps([v['total_tokens'] for v in suite_breakdown['aggregate']['by_type'].values()])},
          backgroundColor: ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#999999'],
        }}],
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'right' }},
          title: {{ display: true, text: 'Total Tokens by Content Type (Suite)' }}
        }}
      }}
    }});"""
            
            # Per-harness comparison chart if multiple runs
            if len(runs) > 1 and suite_breakdown['by_harness']:
                harness_names = list(suite_breakdown['by_harness'].keys())
                all_types = set()
                for bd in suite_breakdown['by_harness'].values():
                    all_types.update(bd['by_type'].keys())
                all_types = sorted(all_types)
                
                colors_map = {
                    'system_prompt': '#4e79a7',
                    'user_message': '#59a14f',
                    'tool_call': '#f28e2b',
                    'tool_result': '#e15759',
                    'assistant_continuation': '#76b7b2',
                    'mixed': '#edc948',
                    'unknown': '#999999'
                }
                
                datasets = []
                for content_type in all_types:
                    data = []
                    for harness_name in harness_names:
                        harness_data = suite_breakdown['by_harness'][harness_name]
                        data.append(harness_data['by_type'].get(content_type, {}).get('total_tokens', 0))
                    datasets.append({
                        'label': content_type.replace('_', ' ').title(),
                        'data': data,
                        'backgroundColor': colors_map.get(content_type, '#cccccc')
                    })
                
                content_type_js += f"""

    // Per-Harness Content Type Comparison (Stacked Bar)
    new Chart(document.getElementById('harnessContentTypeChart'), {{
      type: 'bar',
      data: {{
        labels: {json.dumps(harness_names)},
        datasets: {json.dumps(datasets)},
      }},
      options: {{
        responsive: true,
        scales: {{
          x: {{ title: {{ display: true, text: 'Harness' }} }},
          y: {{ stacked: true, title: {{ display: true, text: 'Tokens' }} }}
        }},
        plugins: {{
          title: {{ display: true, text: 'Content Type Distribution by Harness' }}
        }}
      }}
     }});"""
            
            # Add JavaScript for shell command suite popover
            shell_suite_js = """
    // Custom tooltip for shell command breakdown
    document.querySelectorAll('#shellCommandSuiteTable tbody tr').forEach(row => {
      const infoCell = row.querySelector('.info-icon');
      if (!infoCell) return;
      
      const breakdown = JSON.parse(row.getAttribute('data-breakdown'));
      
      // Create Chart.js style tooltip on hover
      infoCell.addEventListener('mouseenter', function(e) {
        const tooltip = document.createElement('div');
        tooltip.className = 'chartjs-tooltip';
        tooltip.style.cssText = 'position: absolute; background: rgba(0,0,0,0.8); color: white; padding: 8px 12px; border-radius: 4px; font-size: 12px; pointer-events: none; z-index: 1000; white-space: nowrap;';
        
        let content = '<div style="font-weight: bold; margin-bottom: 4px;">Breakdown by Harness</div>';
        breakdown.forEach(item => {
          content += `<div>${item.harness}: ${item.calls} calls, ${item.tokens.toLocaleString()} tokens</div>`;
        });
        tooltip.innerHTML = content;
        
        document.body.appendChild(tooltip);
        
        // Position tooltip
        const rect = e.target.getBoundingClientRect();
        tooltip.style.left = (rect.left + window.scrollX - tooltip.offsetWidth - 10) + 'px';
        tooltip.style.top = (rect.top + window.scrollY) + 'px';
        
        infoCell._tooltip = tooltip;
      });
      
      infoCell.addEventListener('mouseleave', function() {
        if (infoCell._tooltip) {
          infoCell._tooltip.remove();
          infoCell._tooltip = null;
        }
      });
    });
    """
            content_type_js += shell_suite_js
    
    patches_html, patches_data_js = _patches_section(runs)

    return _render_html(
        title=title,
        heading=heading,
        meta=meta,
        summary_html=summary_html,
        resolve_chart_html=resolve_chart_html,
        resolve_chart_js=resolve_chart_js,
        context_datasets=context_datasets,
        turns=turns,
        avg_input=[],          # not used — tokens_datasets handles it
        avg_output=[],
        duration_chart_html=duration_chart_html,
        duration_chart_js=duration_chart_js,
        cost_chart_html=cost_chart_html,
        cost_chart_js=cost_chart_js,
        instances_html=instances_html,
        patches_html=patches_html,
        patches_data_js=patches_data_js,
        tokens_stacked=False,
        tokens_datasets_override=tokens_datasets,
        content_type_html=content_type_html,
        content_type_js=content_type_js,
    )


# ---------------------------------------------------------------------------
# Shared HTML template renderer
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
        tokens_datasets_js = json.dumps(tokens_datasets_override)
    else:
        tokens_datasets_js = json.dumps([
            {"label": "input tokens", "data": avg_input, "backgroundColor": _color(0)},
            {"label": "output tokens", "data": avg_output, "backgroundColor": _color(1)},
        ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
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
  <h1>{heading}</h1>
  <div class="meta">{meta}</div>

  {summary_html}

  {resolve_chart_html}

  <h2>Context growth over turns (prompt size = input + cache_read + cache_creation)</h2>
  <div class="chart-wrap"><canvas id="contextGrowthChart"></canvas></div>

   <h2>Tokens per turn (avg across instances)</h2>
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
      labels: {json.dumps(context_labels)},
      datasets: {json.dumps(context_datasets)},
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ title: {{ display: true, text: 'turn index' }} }},
        y: {{ title: {{ display: true, text: 'prompt tokens' }}, beginAtZero: true }},
      }},
    }},
  }});

  new Chart(document.getElementById('tokensPerTurnChart'), {{
    type: 'bar',
    data: {{
      labels: {json.dumps(turns)},
      datasets: {tokens_datasets_js},
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ title: {{ display: true, text: 'turn index' }}, stacked: false }},
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

def _load_suite_runs(suite_dir: Path) -> list[_RunData]:
    """Load harness reports from a suite directory in stable order."""
    return [
        _RunData.load(item, harness_name=item.name)
        for item in sorted(Path(suite_dir).iterdir())
        if item.is_dir() and (item / "report.json").exists()
    ]


def _comparison_value(value: float | int | None) -> dict[str, float | int | None]:
    return {"value": value}


def _comparison_payload(baseline_dir: Path, candidate_dir: Path) -> dict:
    baseline_report = json.loads((baseline_dir / "report.json").read_text())
    candidate_report = json.loads((candidate_dir / "report.json").read_text())
    if baseline_report.get("benchmark") != candidate_report.get("benchmark"):
        raise ValueError("comparison requires matching benchmarks")

    baseline_runs = {r.run_id: r for r in _load_suite_runs(baseline_dir)}
    candidate_runs = {r.run_id: r for r in _load_suite_runs(candidate_dir)}
    baseline_instances = {
        m["instance_id"] for rd in baseline_runs.values() for m in rd.metrics
    }
    candidate_instances = {
        m["instance_id"] for rd in candidate_runs.values() for m in rd.metrics
    }
    if baseline_instances != candidate_instances:
        only_baseline = sorted(baseline_instances - candidate_instances)
        only_candidate = sorted(candidate_instances - baseline_instances)
        raise ValueError(
            "comparison requires matching instance sets; "
            f"only in baseline={only_baseline}, only in candidate={only_candidate}"
        )

    def report_value(rd: _RunData | None, key: str):
        return rd.report.get(key) if rd else None

    rows = []
    for harness in sorted(set(baseline_runs) | set(candidate_runs)):
        before = baseline_runs.get(harness)
        after = candidate_runs.get(harness)
        metrics_before = {m["instance_id"]: m for m in before.metrics} if before else {}
        metrics_after = {m["instance_id"]: m for m in after.metrics} if after else {}
        instances = []
        for instance_id in sorted(baseline_instances):
            left = metrics_before.get(instance_id)
            right = metrics_after.get(instance_id)
            instances.append({
                "instance_id": instance_id,
                "baseline_resolved": left.get("resolved") if left else None,
                "candidate_resolved": right.get("resolved") if right else None,
                "baseline_tokens": left.get("total_tokens") if left else None,
                "candidate_tokens": right.get("total_tokens") if right else None,
            })

        def detail(rd: _RunData | None) -> dict | None:
            if not rd:
                return None
            requests = [r for m in rd.metrics for r in m.get("requests", [])]
            breakdown = _content_type_breakdown(requests)
            return {
                "content": breakdown["by_type"],
                "tools": breakdown["tool_usage"],
                "tool_percentage": _calculate_tool_usage_percentage(requests),
            }

        rows.append({
            "harness": harness,
            "baseline": {
                "model": report_value(before, "model"),
                "resolved": report_value(before, "resolved"),
                "resolve_rate": report_value(before, "resolve_rate"),
                "avg_total_tokens": report_value(before, "avg_total_tokens"),
                "tokens_per_resolved": report_value(before, "tokens_per_resolved"),
                "detail": detail(before),
            },
            "candidate": {
                "model": report_value(after, "model"),
                "resolved": report_value(after, "resolved"),
                "resolve_rate": report_value(after, "resolve_rate"),
                "avg_total_tokens": report_value(after, "avg_total_tokens"),
                "tokens_per_resolved": report_value(after, "tokens_per_resolved"),
                "detail": detail(after),
            },
            "instances": instances,
        })
    return {
        "benchmark": baseline_report.get("benchmark"),
        "baseline_run_id": baseline_report.get("suite_id") or baseline_report.get("run_id") or baseline_dir.name,
        "candidate_run_id": candidate_report.get("suite_id") or candidate_report.get("run_id") or candidate_dir.name,
        "baseline_model": baseline_report.get("model"),
        "candidate_model": candidate_report.get("model"),
        "rows": rows,
    }


def _build_comparison_html(baseline_dir: Path, candidate_dir: Path) -> str:
    payload = _comparison_payload(baseline_dir, candidate_dir)
    payload_json = json.dumps(payload, separators=(",", ":"))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>acb comparison</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; background: #f7f7f9; color: #1a1a1a; }}
h1 {{ margin-bottom: .25rem; }} .meta {{ color: #666; margin-bottom: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px #0001; }}
th, td {{ padding: .65rem .8rem; border-bottom: 1px solid #eee; text-align: left; }}
th {{ background: #fafafa; color: #666; font-size: .78rem; text-transform: uppercase; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.better {{ color: #187a37; }} .worse {{ color: #b42318; }} .muted {{ color: #888; }}
button {{ border: 0; background: none; color: #1769aa; cursor: pointer; text-decoration: underline; font: inherit; }}
.detail {{ display: none; background: white; margin: 1rem 0 2rem; padding: 1rem; box-shadow: 0 1px 3px #0001; }}
.detail.open {{ display: block; }} .detail h3 {{ margin-top: 0; }} .detail table {{ box-shadow: none; }}
.status {{ font-weight: 600; }}
.run-labels {{ display: flex; gap: 1rem; margin: 1rem 0 1.25rem; flex-wrap: wrap; }}
.run-labels div {{ background: white; border-left: 4px solid #1769aa; padding: .7rem 1rem; min-width: 220px; box-shadow: 0 1px 3px #0001; }}
.run-labels div:first-child {{ border-left-color: #187a37; }}
.run-labels span {{ display: block; color: #666; font-size: .75rem; text-transform: uppercase; }}
.run-labels strong {{ display: block; margin-top: .2rem; font-size: 1.05rem; }}
small {{ font-weight: normal; text-transform: none; }}
</style></head><body>
<h1>Benchmark Comparison</h1>
<div class="run-labels"><div><span>Candidate</span><strong>{html.escape(str(payload['candidate_run_id']))}</strong></div>
<div><span>Baseline</span><strong>{html.escape(str(payload['baseline_run_id']))}</strong></div></div>
<div class="meta">Benchmark: <strong>{html.escape(str(payload['benchmark']))}</strong> &middot;
Candidate model: {html.escape(str(payload['candidate_model']))} &middot;
Baseline model: {html.escape(str(payload['baseline_model']))}</div>
<table><thead><tr><th>Harness</th><th>Correctness<br><small>Candidate / Baseline</small></th><th>Resolve delta<br><small>Candidate - Baseline</small></th>
<th>Avg tokens<br><small>Candidate / Baseline</small></th><th>Token delta<br><small>Candidate - Baseline</small></th><th>Instances</th><th>Details</th></tr></thead>
<tbody id="summary"></tbody></table>
<div id="details"></div>
<script>
const comparison = {payload_json};
const fmt = value => value == null ? 'n/a' : Number(value).toLocaleString(undefined, {{maximumFractionDigits: 1}});
const delta = (a, b) => a == null || b == null ? null : b - a;
const deltaCell = (a, b, digits = 1) => {{ const d = delta(a,b); if (d == null) return '<span class="muted">n/a</span>'; const cls = d < 0 ? 'better' : d > 0 ? 'worse' : 'muted'; return `<span class="${{cls}}">${{d > 0 ? '+' : ''}}${{Number(d).toLocaleString(undefined, {{maximumFractionDigits: digits}})}}</span>`; }};
const correctness = (row) => {{
  if (!row.baseline.detail && !row.candidate.detail) return '<span class="muted">harness missing</span>';
  return `${{fmt(row.candidate.resolved)}} / ${{fmt(row.baseline.resolved)}}`;
}};
const summary = document.getElementById('summary');
comparison.rows.forEach((row, index) => {{
  const missing = !row.baseline.detail ? ' <span class="muted">(only in candidate)</span>' : !row.candidate.detail ? ' <span class="muted">(only in baseline)</span>' : '';
  summary.insertAdjacentHTML('beforeend', `<tr><td><strong>${{row.harness}}</strong>${{missing}}</td><td>${{correctness(row)}}</td><td class="num">${{deltaCell(row.baseline.resolve_rate, row.candidate.resolve_rate, 1)}}</td><td class="num"><button data-index="${{index}}">${{fmt(row.candidate.avg_total_tokens)}} / ${{fmt(row.baseline.avg_total_tokens)}}</button></td><td class="num">${{deltaCell(row.baseline.avg_total_tokens, row.candidate.avg_total_tokens, 0)}}</td><td class="num">${{row.instances.length}}</td><td><button data-index="${{index}}">Open breakdown</button></td></tr>`);
}});
const details = document.getElementById('details');
const renderBreakdown = (row, side) => {{
  const data = row[side].detail; if (!data) return '<p class="muted">No data for this side.</p>';
  const types = Object.entries(data.content).map(([key, value]) => `<tr><td>${{key}}</td><td class="num">${{fmt(value.total_tokens)}}</td><td class="num">${{value.count}}</td></tr>`).join('');
  const tools = Object.entries(data.tools).map(([key, value]) => `<tr><td>${{value.tool_name}}</td><td>${{value.tool_detail || ''}}</td><td class="num">${{value.interactions}}</td><td class="num">${{value.complete_pairs}}</td><td class="num">${{value.call_only}}</td><td class="num">${{value.result_only}}</td><td class="num">${{fmt(value.total_tokens)}}</td></tr>`).join('');
  const runName = side === 'candidate' ? comparison.candidate_run_id : comparison.baseline_run_id;
  return `<h3>${{side[0].toUpperCase() + side.slice(1)}}: ${{runName}} (${{row[side].model || ''}})</h3><h4>Content classification</h4><table><tr><th>Type</th><th>Tokens</th><th>Records</th></tr>${{types}}</table><h4>Tools</h4><table><tr><th>Name</th><th>Detail</th><th>Interactions</th><th>Complete Pairs</th><th>Call Only</th><th>Result Only</th><th>Tokens</th></tr>${{tools || '<tr><td colspan="7">No tool data</td></tr>'}}</table>`;
}};
document.querySelectorAll('button[data-index]').forEach(button => button.addEventListener('click', () => {{
  const index = Number(button.dataset.index); const row = comparison.rows[index]; const id = `detail-${{index}}`; let panel = document.getElementById(id);
  if (!panel) {{ panel = document.createElement('section'); panel.id = id; panel.className = 'detail'; panel.innerHTML = `<h2>${{row.harness}} token breakdown</h2>${{renderBreakdown(row, 'candidate')}}${{renderBreakdown(row, 'baseline')}}<h3>Correctness by instance</h3><table><tr><th>Instance</th><th>Candidate</th><th>Baseline</th><th>Token delta<br><small>Candidate - Baseline</small></th></tr>${{row.instances.map(i => `<tr><td>${{i.instance_id}}</td><td>${{i.candidate_resolved == null ? 'n/a' : i.candidate_resolved ? 'resolved' : 'unresolved'}}</td><td>${{i.baseline_resolved == null ? 'n/a' : i.baseline_resolved ? 'resolved' : 'unresolved'}}</td><td class="num">${{deltaCell(i.baseline_tokens, i.candidate_tokens, 0)}}</td></tr>`).join('')}}</table>`; details.appendChild(panel); }}
  panel.classList.toggle('open'); panel.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
}}));
</script></body></html>"""

def build_html_report(run_dirs: str | Path | list[str | Path]) -> str:
    """Build a self-contained HTML report for one or more runs or a suite.

    Args:
        run_dirs: A single run directory path, or a list of paths.
                  - Single suite directory (contains harness subdirs): suite comparison view
                  - Single harness directory: single-run detail view
                  - List of directories: multi-run comparison view
                  
    Returns:
        HTML string ready to be written to a file.
    """
    if isinstance(run_dirs, (str, Path)):
        run_dir = Path(run_dirs)
        
        # Check if this is a suite directory (contains harness subdirs with reports)
        if _is_suite_directory(run_dir):
            # Load each harness subdirectory as a separate run/harness
            runs = []
            for harness_dir in sorted(run_dir.iterdir()):
                if harness_dir.is_dir() and (harness_dir / "report.json").exists():
                    # Use the subdirectory name as the harness name
                    runs.append(_RunData.load(harness_dir, harness_name=harness_dir.name))
            
            if len(runs) > 1:
                return _build_multi_run_html(runs, is_suite=True)
            elif len(runs) == 1:
                return _build_single_run_html(runs[0])
            else:
                return "<p>No harness data found in suite directory.</p>"
        else:
            # Single harness directory
            runs = [_RunData.load(run_dir)]
            return _build_single_run_html(runs[0])
    else:
        # List of directories (explicit multi-run comparison). Two suite roots
        # are treated as baseline/candidate comparison inputs.
        run_dirs = [Path(d) for d in run_dirs]
        if len(run_dirs) == 2 and all(_is_suite_directory(d) for d in run_dirs):
            return _build_comparison_html(run_dirs[0], run_dirs[1])
        runs = [_RunData.load(d) for d in run_dirs]
        
        if len(runs) == 1:
            return _build_single_run_html(runs[0])
        return _build_multi_run_html(runs, is_suite=False)
