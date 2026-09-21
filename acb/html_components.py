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
:root {color-scheme:light; font:16px system-ui,sans-serif;color:#17202a;background:#f7f7f9}
body {max-width:1300px;margin:32px auto;padding:0 24px}
h1 {font-size:1.8rem} h2 {margin-top:2.5rem} h3 {margin-top:1.5rem;font-size:1.1rem}
a {color:#185abc} small,.muted {color:#586574} small {display:block}
section {margin:24px 0} .benchmark {border-top:2px solid #dce2ea;padding-top:20px}
table {border-collapse:collapse;width:100%;margin:20px 0;background:white}
td,th {padding:10px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top;overflow-wrap:anywhere}
th {background:#edf1f6} .table-wrap {overflow:auto}
pre {white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;background:#eef1f5;padding:12px;border-radius:6px}
details {margin:12px 0} summary {cursor:pointer;font-weight:500}
.warning {background:#fff3cd;padding:12px;border-radius:6px}
.chart-wrap {height:320px;max-width:950px;background:white;padding:20px;border-radius:8px;margin:16px 0}
'''


def render_page(title, sections):
    content = Section.join(sections)
    assets = ''
    if content.charts:
        assets = ('<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
                  '<script type="application/json" id="chart-data">' + script_json(content.charts) + '</script>'
                  '<script>if(window.Chart){for(const [id,config] of Object.entries('
                  'JSON.parse(document.getElementById("chart-data").textContent)))'
                  '{new Chart(document.getElementById(id),config);}}</script>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>{STYLES}</style></head>'
            f'<body>{content.body}{assets}</body></html>')
