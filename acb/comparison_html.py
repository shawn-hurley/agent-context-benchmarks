"""HTML presentation for the shared per-benchmark comparison contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from acb.comparison import compare_records, delta
from acb.html_components import RenderContext, Section, escape as esc, render_page, value, grade_definition
from acb.html_report import benchmark_section, render_benchmark
from acb.report_data import ReportSource


def percent(item):
    return 'unavailable' if item is None else value(item) + '%'


def filename(identity):
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24] + '.html'


def comparison_section(payload, links=None):
    links = links or {}
    coverage = payload['coverage']
    verdict = payload['quality'] or 'No comparable grades'
    qualifier = '' if coverage['complete'] else ' — incomplete comparison'
    body = f'<h1>Benchmark comparison</h1><h2>{esc(verdict.capitalize())}{qualifier}</h2>'
    body += f'<p>Baseline: {esc(payload["baseline"])}<br>Candidate: {esc(payload["candidate"])}</p>'
    body += f'<p>Candidate relative to baseline. Graded coverage: {coverage["graded"]}/{coverage["total"]}; comparable token measurements: {coverage["measured"]}/{coverage["total"]}.</p>'
    body += '<p>Quality is compared per benchmark. No universal suite quality score is calculated. Tokens include fresh input, output, cache reads and cache creation.</p>'
    body += '<table><tr><th>Benchmark / harness</th><th>Baseline grade</th><th>Candidate grade</th><th>Quality</th><th>Tokens: baseline → candidate</th><th>Token change</th><th>Comparability / detail</th></tr>'
    for row in payload['rows']:
        left, right = row['baseline'], row['candidate']
        label = (left or right)['benchmark']
        def detail_link(side):
            record = row[side]
            return links.get((side, (record['dataset'], record['benchmark'], record['harness']))) if record else None
        notices = list(row['reasons']) + row['telemetry_notes']
        if left and right and left['model'] != right['model']:
            notices.append('Models differ; token counts may use different tokenizers and are not a price comparison')
        if not row['measurement_comparable']:
            notices.append('Token comparison unavailable: unmatched conditions or incomplete measurements')
        if row['quality'] == 'unknown':
            notices.append('Grade missing or evaluation failed')
        link_text = ' '.join(f'<a href="{esc(detail_link(side))}">{side} detail</a>' for side in ('baseline', 'candidate') if detail_link(side))
        body += f'<tr><td><strong>{esc(label)}</strong><small>{esc((left or right)['dataset'])}</small><small>{esc(left['harness'] if left else 'absent')} → {esc(right['harness'] if right else 'absent')}</small></td><td>{value(left["grade"] if left else None)}</td><td>{value(right["grade"] if right else None)}</td><td>{esc(row["quality"].replace('_', ' '))}</td>'
        body += f'<td>{value(left["tokens"] if left else None)} → {value(right["tokens"] if right else None)}</td><td>{value(row["tokens"]["absolute"])}<small>{percent(row["tokens"]["percent"])}</small></td><td>{"<br>".join(esc(n) for n in notices)}<br>{link_text}</td></tr>'
        body += '<tr><td colspan="7"><details><summary>Turns, requests, tools and token buckets</summary><table><tr><th>Measure</th><th>Baseline</th><th>Candidate</th><th>Change</th></tr>'
        for field in ('turns', 'model_requests', 'tool_calls'):
            a,b = (left.get(field) if left else None),(right.get(field) if right else None)
            body += f'<tr><td>{esc(field)}</td><td>{value(a)}</td><td>{value(b)}</td><td>{value(delta(a,b)["absolute"]) if row["diagnostic_comparability"][field] else "not comparable"}</td></tr>'
        for side, record in [('Baseline', left), ('Candidate', right)]:
            if record and not record['measurement_complete']:
                body += '<tr><td colspan="4">'+side+' captured observations (incomplete): '+', '.join(
                    field.replace('_',' ')+': '+value(record['captured_'+field])
                    for field in ('turns','model_requests','tool_calls'))+'</td></tr>'
        body += '</table><p>Primary grade: '+grade_definition((left or right)['definition'])+'.</p><p>Turns, model requests and tool calls are distinct. Unavailable telemetry is not zero; different trajectories are not aligned call-by-call.</p>'
        for side, record in [('Baseline',left),('Candidate',right)]:
            if record:
                body += f'<h3>{side}</h3><pre>{esc(json.dumps({"buckets":record["token_buckets"],"captured_tokens":record["captured_tokens"],"provenance":record["provenance"]},indent=2))}</pre>'
        body += '</details></td></tr>'
    body += '</table>'
    total = payload['matched_tokens']
    if coverage['measured']:
        body += f'<h2>Tokens over the {coverage["measured"]} comparable benchmarks only</h2><p>{value(total["baseline"])} → {value(total["candidate"])}; change {value(total["absolute"])} ({percent(total["percent"])}).</p>'
    return Section('<section>'+body+'</section>')


def load_sources(run_dirs):
    roots = [run_dirs] if isinstance(run_dirs, (str, Path)) else list(run_dirs)
    if not roots:
        raise ValueError('at least one run is required')
    cache = {}
    sources = []
    for root in roots:
        path = Path(root).resolve()
        if path not in cache:
            cache[path] = ReportSource(path)
        sources.append(cache[path])
    return sources


def comparison_sections(sources, links=None):
    sections, payloads = [], []
    for index, source in enumerate(sources[1:], 1):
        payload = compare_records(sources[0].benchmarks, source.benchmarks,
                                  sources[0].root, source.root)
        local_links = {(side, identity): link
                       for (number, identity), link in (links or {}).items()
                       for side in ['baseline' if number == 0 else 'candidate']
                       if number in (0, index)}
        sections.append(comparison_section(payload, local_links))
        payloads.append(payload)
    return sections, payloads


def build_report(run_dirs):
    """Compose an in-memory document using the same sections as written reports."""
    sources = load_sources(run_dirs)
    if len(sources) > 1:
        sections, _ = comparison_sections(sources)
        return render_page('Benchmark comparisons', sections)
    source = sources[0]
    context = RenderContext()
    sections = [Section('<h1>Individual benchmark reports</h1>')]
    for record in source.benchmarks.values():
        sections.append(benchmark_section(record, source.harnesses[Path(record['directory'])], context))
    return render_page('Individual benchmark reports', sections)


def write_reports(run_dirs, destination):
    """Write an overview, one page per benchmark/harness, and comparison JSON."""
    sources = load_sources(run_dirs)
    # Validate inputs before changing any existing report output.
    for source in sources:
        source.benchmarks
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    assets = destination.parent / (destination.stem + '-benchmarks')
    assets.mkdir(exist_ok=True)
    links, index = {}, []
    for number, source in enumerate(sources):
        side = 'run' if len(sources) == 1 else ('baseline' if number == 0 else f'candidate-{number}')
        for identity, record in source.benchmarks.items():
            name = side + '-' + filename(identity)
            harness = source.harnesses[Path(record['directory'])]
            (assets / name).write_text(render_benchmark(record, harness))
            link = assets.name + '/' + name
            links[(number, identity)] = link
            index.append(f'<li><a href="{esc(link)}">{esc(side+": "+" / ".join(identity))}</a> — grade {value(record["grade"])}</li>')
    if len(sources) > 1:
        sections, payloads = comparison_sections(sources, links)
        destination.write_text(render_page('Benchmark comparisons', sections))
        destination.with_suffix('.comparison.json').write_text(json.dumps(payloads, indent=2))
    else:
        destination.write_text(render_page('Benchmark reports', [Section(
            '<h1>Individual benchmark reports</h1><ul>' + ''.join(index) + '</ul>')]))
    return destination
