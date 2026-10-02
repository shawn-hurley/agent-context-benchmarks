"""Shared HTML document, section, chart and evidence components.

Components accept text or structured chart data. Only Section.body contains
trusted HTML assembled by renderers; user/run values must pass through escape.
"""
from dataclasses import dataclass, field
import html
import json
from pathlib import Path

PREVIEW_BYTES = 32 * 1024
MAX_ARTIFACTS = 40
ARTIFACT_BUDGET = 128 * 1024


def escape(value):
    return html.escape(str(value), quote=True)


def value(item):
    return 'unavailable' if item is None else escape(round(item, 3) if isinstance(item, float) else item)


def grade_definition(definition):
    direction = {'higher': 'higher is better', 'lower': 'lower is better'}.get(
        definition.get('direction'), 'direction unspecified')
    return escape(f"{definition.get('metric', 'unspecified')} ({direction}; tolerance {definition.get('tolerance', 0)})")


def table(headers, rows):
    return ('<div class="table-wrap"><table><thead><tr>' +
            ''.join('<th>' + escape(header) + '</th>' for header in headers) +
            '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + value(cell) + '</td>' for cell in row) +
                                           '</tr>' for row in rows) + '</tbody></table></div>')


def stat_cards(items):
    """Render a compact, readable summary without changing measurement semantics."""
    return ('<div class="stat-grid">' + ''.join(
        '<div class="stat' + (' stat-' + escape(item[2]) if len(item) > 2 else '') +
        '"><span class="stat-label">' + escape(item[0]) +
        '</span><strong class="stat-value">' + value(item[1]) + '</strong></div>'
        for item in items
    ) + '</div>')


def pass_outcome(records):
    """Classify explicit trial pass results without assuming a score range."""
    evaluations = [trial['evaluation'] for record in records for trial in record['trials']]
    outcomes = [evaluation.get('resolved') if evaluation.get('status') == 'completed' else None
                for evaluation in evaluations]
    passed = sum(outcome is True for outcome in outcomes)
    if not outcomes or any(type(outcome) is not bool for outcome in outcomes):
        return 'unknown', passed, len(outcomes)
    if passed == len(outcomes):
        return 'full pass', passed, len(outcomes)
    return ('partial pass' if passed else 'failure'), passed, len(outcomes)


def outcome_badge(outcome):
    return f'<span class="badge badge-{outcome.replace(" ", "-")}">{escape(outcome)}</span>'


def run_title(info):
    """A concise identity shared by visible headings and browser tabs."""
    parts = [str(info.get('benchmark') or 'Benchmark')]
    if info.get('source') and info.get('target'):
        parts.append(f"{info['source']} → {info['target']}")
    parts.append(str(info.get('model') or 'Model unrecorded'))
    if info.get('workflow'):
        parts.append(str(info['workflow']))
    return ' · '.join(parts)


def run_facts(info):
    """Show selected configuration fields; keep unknown distinct from none."""
    known = info.get('configuration_recorded', False)

    def listed(items):
        return ', '.join(items) if items else ('None' if known else 'Not recorded')

    facts = [
        ('Run ID', info.get('run_id')),
        ('Benchmark', info.get('benchmark')),
        ('Model', info.get('model')),
        ('Harness', listed(info.get('harnesses'))),
        ('Workflow', info.get('workflow') or ('None' if known else 'Not recorded')),
        ('Skills', listed(info.get('skills'))),
        ('MCP servers', listed(info.get('mcp_servers'))),
        ('Extensions', listed(info.get('extensions'))),
        ('Integrations', listed(info.get('integrations'))),
        ('Proxy', info.get('proxy')),
    ]
    if info.get('source') and info.get('target'):
        facts.insert(4, ('Migration', f"{info['source']} → {info['target']}"))
    return ('<dl class="run-facts">' + ''.join(
        '<div><dt>' + escape(label) + '</dt><dd>' + escape(fact if fact is not None else 'Not recorded') + '</dd></div>'
        for label, fact in facts
    ) + '</dl>')


def script_json(value):
    return (json.dumps(value, ensure_ascii=False).replace('&', '\\u0026')
            .replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


@dataclass
class Section:
    body: str
    charts: dict = field(default_factory=dict)

    @classmethod
    def join(cls, sections):
        bodies, charts = [], {}
        for section in sections:
            if charts.keys() & section.charts.keys():
                raise ValueError('duplicate chart identity')
            bodies.append(section.body)
            charts.update(section.charts)
        return cls(''.join(bodies), charts)


@dataclass
class RenderContext:
    """Allocate unique DOM identities within one document."""
    counter: int = 0

    def identifier(self, kind):
        self.counter += 1
        return f'{kind}-{self.counter}'

    def chart(self, title, kind, data, *, x=None, y=None, stacked=False):
        identity = self.identifier('chart')
        options = {'responsive': True, 'maintainAspectRatio': False}
        if x or y:
            options['scales'] = {
                'x': {'title': {'display': bool(x), 'text': x}, 'stacked': stacked},
                'y': {'title': {'display': bool(y), 'text': y}, 'beginAtZero': True, 'stacked': stacked},
            }
        body = (f'<h3>{escape(title)}</h3><div class="chart-wrap"><canvas id="{identity}" '
                f'aria-label="{escape(title)}" role="img"></canvas></div>')
        return Section(body, {identity: {'type': kind, 'data': data, 'options': options}})


def preview(text, *, limit=PREVIEW_BYTES):
    truncated = len(text) > limit
    return ('<pre>' + escape(text[:limit]) + '</pre>' +
            ('<p class="muted">Preview truncated; open the original artifact for full evidence.</p>' if truncated else ''))


def diff_preview(patch, *, limit=PREVIEW_BYTES):
    """Highlight a bounded patch preview while escaping every diff line."""
    lines = []
    for line in patch[:limit].splitlines():
        if line.startswith(('diff --git ', '+++ ', '--- ')):
            kind = 'diff-file'
        elif line.startswith('@@'):
            kind = 'diff-hunk'
        elif line.startswith('+'):
            kind = 'diff-add'
        elif line.startswith('-'):
            kind = 'diff-del'
        else:
            kind = 'diff-context'
        lines.append(f'<span class="{kind}">{escape(line) or "&nbsp;"}</span>')
    result = '<pre class="diff-preview">' + ''.join(lines) + '</pre>'
    if len(patch) > limit:
        result += '<p class="muted">Preview truncated; open the original artifact for full evidence.</p>'
    return result


def json_details(title, value):
    return f'<details><summary>{escape(title)}</summary>{preview(json.dumps(value, indent=2, ensure_ascii=False))}</details>'


def artifact_sections(directory: Path | None):
    """Bound embedded evidence by count and bytes; link to untouched originals."""
    if directory is None or not directory.is_dir():
        return ''
    body = f'<p><a href="{escape(directory.as_uri())}">Original trial artifacts</a></p>'
    remaining, count = ARTIFACT_BUDGET, 0
    for artifact in sorted(directory.rglob('*')):
        if not artifact.is_file() or artifact.suffix not in ('.json', '.jsonl', '.txt', '.log', '.patch'):
            continue
        if count >= MAX_ARTIFACTS or remaining <= 0:
            body += '<p class="muted">Additional artifact previews omitted; use the original trial directory.</p>'
            break
        # Do not follow evidence symlinks outside this trial directory.
        if not artifact.resolve().is_relative_to(directory.resolve()):
            continue
        limit = min(PREVIEW_BYTES, remaining)
        try:
            with artifact.open('rb') as stream:
                content = stream.read(limit + 1)
        except OSError:
            continue
        remaining -= min(len(content), limit)
        count += 1
        body += (f'<details><summary>{escape(artifact.relative_to(directory))}</summary>'
                 f'<a href="{escape(artifact.as_uri())}">Open full artifact</a>'
                 f'{preview(content.decode("utf-8", errors="replace"), limit=limit)}</details>')
    return body


STYLES = '''
:root {color-scheme:light;font:16px/1.5 system-ui,sans-serif;color:#1a2b34;background:#f4f5f2}
* {box-sizing:border-box}
body {max-width:1380px;margin:0 auto;padding:38px clamp(16px,4vw,52px) 80px}
h1,h2,h3 {line-height:1.2;letter-spacing:-.025em}
h1 {font-size:clamp(2rem,4vw,3rem);margin:0 0 12px}
h2 {font-size:1.45rem;margin:42px 0 12px}
h3 {font-size:1.12rem;margin:28px 0 10px}
p {max-width:90ch} a {color:#096b71;text-underline-offset:3px}
a:hover {color:#06484d} small,.muted {color:#596b73} small {display:block}
section {margin:28px 0}.benchmark {border-top:2px solid #cfddda;padding-top:28px}
.eyebrow {color:#08747a;font-size:.78rem;font-weight:750;text-transform:uppercase;letter-spacing:.12em;margin:0 0 8px}
.subtitle {color:#53666f;margin:0 0 26px}
.stat-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:24px 0}
.stat {background:#fff;border:1px solid #dce6e2;border-radius:12px;padding:16px 18px;box-shadow:0 2px 9px #183b3910}
.stat-label {display:block;color:#587078;font-size:.76rem;font-weight:700;text-transform:uppercase;letter-spacing:.075em}
.stat-value {display:block;font-size:1.5rem;line-height:1.2;margin-top:8px;overflow-wrap:anywhere}
.stat-full-pass {background:#edf8f1;border-color:#a9dcc0}.stat-full-pass .stat-value {color:#166342}
.stat-partial-pass {background:#fff7e8;border-color:#f1d59b}.stat-partial-pass .stat-value {color:#79520f}
.stat-failure {background:#fff0eb;border-color:#efc0b3}.stat-failure .stat-value {color:#9a452f}
.stat-unknown {background:#f0f3f4;border-color:#cbd6d9}.stat-unknown .stat-value {color:#485c65}
.run-facts {display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;margin:20px 0 28px;
            border:1px solid #dce6e2;border-radius:12px;overflow:hidden;background:#dce6e2}
.run-facts > div {background:#fff;padding:12px 16px;min-width:0}
.run-facts dt {color:#587078;font-size:.74rem;font-weight:700;text-transform:uppercase;letter-spacing:.075em}
.run-facts dd {margin:5px 0 0;font-weight:600;overflow-wrap:anywhere}
.comparison-runs {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.comparison-runs > div {min-width:0}.comparison-runs h3 {overflow-wrap:anywhere}
@media (max-width:900px) {.comparison-runs {grid-template-columns:1fr}}
.badge {display:inline-block;padding:3px 9px;border-radius:999px;background:#e4ecec;color:#28545a;font-size:.85rem;font-weight:700}
.badge-better,.badge-complete {background:#daf2e5;color:#166342}
.badge-worse,.badge-result-only {background:#fce5df;color:#9a452f}
.badge-same,.badge-call-only {background:#fff0d6;color:#79520f}
.badge-mixed,.badge-unknown,.badge-not_comparable {background:#e8ecee;color:#485c65}
.badge-full-pass {background:#daf2e5;color:#166342}
.badge-partial-pass {background:#fff0d6;color:#79520f}
.badge-failure {background:#fce5df;color:#9a452f}
table {border-collapse:collapse;width:100%;margin:14px 0;background:#fff}
td,th {padding:11px 13px;text-align:left;border-bottom:1px solid #e3eae7;vertical-align:top;overflow-wrap:anywhere}
th {background:#eaf1ee;color:#3b545a;font-size:.76rem;text-transform:uppercase;letter-spacing:.07em}
[data-results-table] {min-width:1050px}
[data-results-table] td:first-child {min-width:160px}
tbody tr:hover {background:#f7fbf9}.table-wrap {overflow:auto;border:1px solid #dce6e2;border-radius:12px}
pre {white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;background:#e9efee;padding:14px;border-radius:8px}
.diff-preview {padding:0;background:#fff;border:1px solid #dce6e2;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
.diff-preview span {display:block;padding:0 12px;min-height:1.5em}
.diff-file {background:#e6eeee;color:#31545a;font-weight:700}
.diff-hunk {background:#e5edfa;color:#355c95}
.diff-add {background:#e8f6ec;color:#225e36}
.diff-del {background:#fceae7;color:#8e382b}
details {margin:12px 0;border:1px solid #dce6e2;border-radius:10px;background:#fff;padding:10px 14px}
details details {background:#f9fbfa}summary {cursor:pointer;font-weight:650}
.warning {background:#fff1dc;border-left:4px solid #d28a25;padding:12px 16px;border-radius:6px}
.chart-wrap {height:320px;max-width:950px;background:#fff;padding:20px;border:1px solid #dce6e2;border-radius:12px;margin:16px 0}
@media (max-width:640px) {body {padding-top:24px}.chart-wrap {height:270px;padding:10px}td,th {padding:9px}}
@media print {body {max-width:none;padding:0;background:#fff}.stat,.chart-wrap,details,.table-wrap {box-shadow:none;break-inside:avoid}}
html {scroll-behavior:smooth;scroll-padding-top:18px}
.report-nav {display:flex;flex-wrap:wrap;gap:8px 18px;margin:0 0 24px;padding:12px 0;border-bottom:1px solid #cfddda}
.report-nav a {font-size:.9rem;font-weight:650}
.results-section {margin-top:28px}
.table-controls {display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:20px 0 12px}
.table-controls label {display:grid;gap:5px;font-size:.8rem;font-weight:650;color:#3b545a}
.table-controls input[type=search] {min-width:220px}
input,select,button {font:inherit}
.table-controls input[type=search],.table-controls select,.table-controls button {border:1px solid #bfcfca;background:#fff;border-radius:7px;padding:8px 10px;color:#1a2b34}
.table-controls .checkbox-label {display:flex;align-items:center;align-self:center;gap:6px}
.table-controls button {cursor:pointer}.result-count {font-size:.85rem;color:#596b73;align-self:center;margin-left:auto}
thead button {border:0;background:transparent;padding:0;color:inherit;font:inherit;text-transform:inherit;cursor:pointer;text-align:left}
thead button span {opacity:.65}th[aria-sort] button span {color:#08747a;opacity:1}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible {outline:3px solid #1b8a91;outline-offset:3px}
[hidden] {display:none!important}
.result-worse > tr:first-child > td:first-child {border-left:4px solid #b95742}
.result-better > tr:first-child > td:first-child {border-left:4px solid #24865b}
.diagnostic-row > td {padding-top:0}.diagnostic-row details {margin:0 0 6px;border:0;background:#f6f8f7}
.comparison-visuals {display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:20px 0}
.comparison-visuals > div {background:#fff;border:1px solid #dce6e2;border-radius:12px;padding:20px}
.comparison-visuals h3 {margin:0 0 16px}
.distribution-track {display:flex;height:14px;border-radius:8px;overflow:hidden;background:#e8ecee}
.segment {display:block}.segment-better {background:#24865b}.segment-same {background:#d6a74b}.segment-worse {background:#b95742}
.segment-unknown {background:#80949d}.segment-not_comparable {background:#c7d1d5}
.distribution-legend {display:flex;flex-wrap:wrap;gap:10px 16px;margin-top:14px;font-size:.87rem}
.distribution-legend i {display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.token-bar {display:grid;grid-template-columns:70px minmax(50px,1fr) auto;align-items:center;gap:10px;margin:10px 0;font-size:.85rem}
.bar-track {background:#edf1ef;height:9px;border-radius:6px;overflow:hidden}.bar-fill {height:100%;border-radius:6px}
.bar-baseline {background:#7d9099}.bar-candidate {background:#08747a}
.delta-better {color:#166342}.delta-worse {color:#9a452f}.delta-same {color:#79520f}
.run-differences table {margin-bottom:0}.run-differences th {width:20%}
@media (max-width:760px) {.comparison-visuals {grid-template-columns:1fr}.table-controls {gap:10px}.result-count {margin-left:0}}
@media (prefers-reduced-motion:reduce) {html {scroll-behavior:auto}}

'''


def render_page(title, sections, *, asset_prefix=None):
    content = Section.join(sections)
    from importlib.resources import files
    resources = files('acb').joinpath('report_assets')
    if asset_prefix is None:
        # Standalone in-memory reports carry their scripts inline.
        interaction = resources.joinpath('report.js').read_text().replace('</script', '<\\/script')
        assets = '<script>' + interaction + '</script>'
    else:
        assets = '<script defer src="' + escape(asset_prefix + 'report.js') + '"></script>'
    if content.charts:
        if asset_prefix is None:
            chart = resources.joinpath('chart.umd.min.js').read_text().replace('</script', '<\\/script')
            assets += '<script>' + chart + '</script>'
        else:
            assets += '<script src="' + escape(asset_prefix + 'chart.umd.min.js') + '"></script>'
        assets += ('<script type="application/json" id="chart-data">' + script_json(content.charts) + '</script>'
                   '<script>if(window.Chart){for(const [id,config] of Object.entries('
                   'JSON.parse(document.getElementById("chart-data").textContent)))'
                   '{new Chart(document.getElementById(id),config);}}</script>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>{STYLES}</style></head>'
            f'<body>{content.body}{assets}</body></html>')
