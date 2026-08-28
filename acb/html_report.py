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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acb.costs import ModelCost, estimate_cost, load_cost_table
from acb.usage import read_records

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
        metrics = _load_jsonl(run_dir / "metrics.jsonl")
        usage_rows = (
            [r.__dict__ for r in read_records(run_dir / "usage.jsonl")]
            if (run_dir / "usage.jsonl").exists()
            else []
        )
        predictions: dict[str, str] = {}
        preds_path = run_dir / "predictions.jsonl"
        if preds_path.exists():
            for line in preds_path.read_text().splitlines():
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    iid = obj.get("instance_id", "")
                    if iid:
                        predictions[iid] = obj.get("model_patch") or ""
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


# ---------------------------------------------------------------------------
# Single-run HTML builders
# ---------------------------------------------------------------------------

def _summary_cards(report: dict, total_cost: float | None) -> str:
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

    patches_html, patches_data_js = _patches_section([rd])
    return _render_html(
        title=title,
        heading=rd.run_id,
        meta=f"{rd.report.get('benchmark', '?')} &middot; {rd.report.get('harness', '?')}"
             f" &middot; {rd.report.get('model', '?')} &middot; proxy: {rd.report.get('proxy', '?')}",
        summary_html=_summary_cards(rd.report, total_cost),
        context_datasets=context_datasets,
        turns=turns,
        avg_input=avg_input,
        avg_output=avg_output,
        duration_chart_html=duration_chart_html,
        duration_chart_js=duration_chart_js,
        cost_chart_html=cost_chart_html,
        cost_chart_js=cost_chart_js,
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
        # List of directories (explicit multi-run comparison)
        run_dirs = [Path(d) for d in run_dirs]
        runs = [_RunData.load(d) for d in run_dirs]
        
        if len(runs) == 1:
            return _build_single_run_html(runs[0])
        return _build_multi_run_html(runs, is_suite=False)
