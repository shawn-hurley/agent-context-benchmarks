"""HTML presentation for the shared per-benchmark comparison contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from acb.comparison import compare_records, delta
from acb.html_components import RenderContext, Section, escape as esc, render_page, run_facts, run_title, stat_cards, value, grade_definition, pass_outcome, outcome_badge
from acb.html_report import benchmark_section, render_benchmark
from acb.provenance import requested_config
from acb.report_data import ReportSource
from acb.report_ui import (navigation, paired_tokens, quality_distribution, row_attributes,
                           run_differences, sort_header, table_controls)


def percent(item):
    return 'unavailable' if item is None else value(item) + '%'


def setting_label(setting):
    labels = {
        'conditions.timeout': 'Agent timeout (seconds)',
        'conditions.attempts': 'Attempts',
        'conditions.cache_policy': 'Cache policy',
        'conditions.environment': 'Container backend',
        'conditions.backend': 'Execution backend',
        'conditions.dataset_revision': 'Dataset revision',
        'task.sha256': 'Experiment bundle',
        'task.runtime.container.image_id': 'Container image',
        'task.runtime.container.image_architecture': 'Container architecture',
        'task.runtime.container.resources.cpu_limit': 'CPU limit',
        'task.runtime.container.resources.memory_limit_bytes': 'Memory limit',
    }
    return labels.get(setting, setting.replace('conditions.', '').replace('task.runtime.', '').replace('_', ' '))


def filename(identity):
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24] + '.html'


def comparison_section(payload, links=None, baseline_info=None, candidate_info=None, *, section_id='comparison-1'):
    links = links or {}
    coverage = payload['coverage']
    verdict = payload['quality'] or 'No comparable grades'
    qualifier = '' if coverage['complete'] else ' — incomplete comparison'
    benchmark = (baseline_info or candidate_info or {}).get('benchmark') or 'Benchmark'
    body = f'<p class="eyebrow">Run comparison</p><h1>{esc(benchmark)} comparison</h1><h2>{esc(verdict.capitalize())}{qualifier}</h2>'
    if baseline_info and candidate_info:
        body += ('<div class="comparison-runs"><div><h3>Baseline · ' + esc(run_title(baseline_info)) +
                 '</h3>' + run_facts(baseline_info) + '</div><div><h3>Candidate · ' +
                 esc(run_title(candidate_info)) + '</h3>' + run_facts(candidate_info) + '</div></div>')
        body += run_differences(baseline_info, candidate_info)
    differences = {setting_label(item['setting']) for row in payload['rows']
                   for item in row.get('setup_differences', [])
                   if not item['setting'].startswith('task.benchmark_contract')}
    if differences:
        body += '<p class="muted">Experiment setup differences are included in this comparison: ' + esc(', '.join(sorted(differences))) + '. Details are recorded with each benchmark.</p>'
    body += stat_cards([
        ('Quality', verdict.capitalize()),
        ('Graded benchmarks', f'{coverage["graded"]} / {coverage["total"]}'),
        ('Comparable tokens', f'{coverage["measured"]} / {coverage["total"]}'),
        ('Token change', percent(payload['matched_tokens']['percent'])),
    ])
    total = payload['matched_tokens']
    body += ('<div class="comparison-visuals"><div><h3>Grade outcomes</h3>' +
             quality_distribution(payload['rows']) + '</div><div><h3>Tokens over ' +
             str(coverage['measured']) + ' comparable benchmarks</h3>' +
             paired_tokens(total['baseline'], total['candidate']) + '</div></div>')
    body += navigation([('Benchmark results', '#' + section_id + '-results')], label='Comparison sections')
    body += (f'<section class="results-section" id="{esc(section_id)}-results" data-results>'
             '<h3>Benchmark results</h3>')
    harnesses = [record['harness'] for row in payload['rows']
                 for record in (row['baseline'], row['candidate']) if record]
    body += table_controls(harnesses, len(payload['rows']), comparison=True)
    body += ('<div class="table-wrap"><table data-results-table><thead><tr>' +
             sort_header('Benchmark / harness', 'benchmark') + sort_header('Baseline grade', 'baselineGrade', numeric=True) +
             sort_header('Candidate grade', 'candidateGrade', numeric=True) + sort_header('Grade change', 'gradeDelta', numeric=True) +
             sort_header('Tokens: baseline → candidate', 'tokens', numeric=True) +
             sort_header('Token change', 'tokenChange', numeric=True) + '<th>Details</th></tr></thead>')

    def grade_cell(record):
        if record is None:
            return 'unavailable'
        outcome, passed, scheduled = pass_outcome([record])
        return (value(record['grade']) + '<small>' + outcome_badge(outcome) +
                f' {passed} / {scheduled} trials passed</small>')

    severity = {'worse': 0, 'unknown': 1, 'not_comparable': 2, 'better': 3, 'same': 4}
    for row in sorted(payload['rows'], key=lambda row: (severity[row['quality']], row['identity'])):
        left, right = row['baseline'], row['candidate']
        record = left or right
        def detail_link(side):
            item = row[side]
            return links.get((side, (item['dataset'], item['benchmark'], item['harness']))) if item else None
        notices = list(row['reasons']) + row['telemetry_notes']
        for difference in row.get('setup_differences', []):
            if difference['setting'] == 'task.benchmark_contract' or difference['setting'].startswith('task.benchmark_contract.'):
                continue
            notices.append('Experiment setup differs: ' + setting_label(difference['setting']) + ': ' +
                           str(difference['baseline']) + ' → ' + str(difference['candidate']))
        if left and right and left['model'] != right['model']:
            notices.append('Models differ; token counts may use different tokenizers and are not a price comparison')
        if not row['measurement_comparable']:
            notices.append('Token comparison unavailable: different benchmark inputs/grading or incomplete measurements')
        if row['quality'] == 'unknown':
            notices.append('Grade missing or evaluation failed')
        quality = row['quality']
        grade_change = (right['grade'] - left['grade'] if quality in ('same', 'better', 'worse') else None)
        attributes = row_attributes(record, outcome=pass_outcome([right])[0] if right else 'unknown', quality=quality,
                                    incomplete=any(not item['measurement_complete'] for item in (left, right) if item),
                                    harness='|'.join(dict.fromkeys(item['harness'] for item in (left, right) if item)),
                                    **{'baseline-grade': left['grade'] if left else None,
                                       'candidate-grade': right['grade'] if right else None,
                                       'grade-delta': grade_change, 'tokens': right['tokens'] if right else None,
                                       'token-change': row['tokens']['absolute']})
        badge = f'<span class="badge badge-{esc(quality)}">{esc(quality.replace("_", " "))}</span>'
        change_text = ('unavailable' if grade_change is None else
                       f'{grade_change:+.3f}'.rstrip('0').rstrip('.'))
        detail_links = ' '.join(f'<a href="{esc(detail_link(side))}">{side.capitalize()} detail</a>'
                                for side in ('baseline', 'candidate') if detail_link(side))
        body += (f'<tbody class="result-{esc(quality)}" {attributes}><tr><td><strong>{esc(record["benchmark"])}</strong>'
                 f'<small>{esc(record["dataset"])}</small><small>{esc(left["harness"] if left else "absent")} → '
                 f'{esc(right["harness"] if right else "absent")}</small></td><td>{grade_cell(left)}</td>'
                 f'<td>{grade_cell(right)}</td><td>{badge}<small class="delta-{esc(quality)}">{esc(change_text)}</small></td><td>' +
                 (paired_tokens(left['tokens'], right['tokens']) if row['measurement_comparable'] else
                  f'{value(left["tokens"] if left else None)} → {value(right["tokens"] if right else None)}<small>Not comparable</small>') +
                 f'</td><td>{value(row["tokens"]["absolute"])}<small>{percent(row["tokens"]["percent"])}</small></td>'
                 f'<td>{detail_links}</td></tr>')
        body += '<tr class="diagnostic-row"><td colspan="7"><details><summary>Measurements and comparison notes</summary>'
        if notices:
            body += '<ul>' + ''.join('<li>' + esc(note) + '</li>' for note in notices) + '</ul>'
        body += '<table><thead><tr><th>Measure</th><th>Baseline</th><th>Candidate</th><th>Change</th></tr></thead><tbody>'
        for field in ('turns', 'model_requests', 'tool_calls'):
            a, b = (left.get(field) if left else None), (right.get(field) if right else None)
            change = value(delta(a, b)['absolute']) if row['diagnostic_comparability'][field] else 'not comparable'
            body += f'<tr><td>{esc(field.replace("_", " "))}</td><td>{value(a)}</td><td>{value(b)}</td><td>{change}</td></tr>'
        for side, item in [('Baseline', left), ('Candidate', right)]:
            if item and not item['measurement_complete']:
                body += '<tr><td colspan="4">' + side + ' captured observations (incomplete): ' + ', '.join(
                    field.replace('_', ' ') + ': ' + value(item['captured_' + field])
                    for field in ('turns', 'model_requests', 'tool_calls')) + '</td></tr>'
        body += '</tbody></table><p>Primary grade: ' + grade_definition(record['definition']) + '.</p>'
        body += '<details><summary>Raw comparison metadata</summary>'
        for side, item in [('Baseline', left), ('Candidate', right)]:
            if item:
                body += f'<h3>{side}</h3><pre>' + esc(json.dumps({
                    'buckets': item['token_buckets'], 'captured_tokens': item['captured_tokens'],
                    'provenance': item['provenance']}, indent=2)) + '</pre>'
        body += '</details></details></td></tr></tbody>'
    body += ('</table></div><p data-empty-results hidden>No benchmarks match these filters.</p></section>'
             '<p class="muted">Grade direction and tolerance come from each benchmark. Token totals include fresh input, '
             'output, cache reads and cache creation, and exclude incomplete or incompatible measurements. '
             'Turns, model requests and tool calls count different events.</p>')
    return Section(f'<section id="{esc(section_id)}">' + body + '</section>')


def benchmark_grade(records):
    """Mean scheduled trial grade for one comparable benchmark configuration."""
    if not records:
        return None
    definitions = {
        (record['dataset'], record['harness'], record['model'],
         json.dumps(record['definition'], sort_keys=True))
        for record in records
    }
    if len(definitions) != 1:
        return None
    grades = [grade for record in records for grade in record['grades']]
    if not grades or any(grade is None for grade in grades):
        return None
    return sum(grades) / len(grades)


def single_run_index(entries, info):
    """Summarize benchmark coverage before linking to the detailed pages."""
    records = [record for _, _, _, record in entries]
    outcome, passed, scheduled = pass_outcome(records)
    body = '<p class="eyebrow">Run overview</p><h1>' + esc(run_title(info)) + '</h1>'
    body += run_facts(info)
    body += '<p class="subtitle">Grades and measurements are shown per benchmark. Open a row for trial evidence and request charts.</p>'
    body += stat_cards([
        ('Benchmarks', len(records)),
        ('Graded', sum(record['grade'] is not None for record in records)),
        ('Benchmark grade', benchmark_grade(records)),
        ('Captured tokens', sum(record['captured_tokens'] for record in records)),
    ])
    body += ('<p class="muted">Benchmark grade is the mean of scheduled trial grades. '
             'It is unavailable when a task grade is missing or the configurations differ. '
             'Benchmarks can use different grade scales; pass labels use verifier outcomes.</p>')
    body += (f'<p><strong>Pass outcome:</strong> {outcome_badge(outcome)} '
             f'{passed} / {scheduled} trials passed. A partial pass means some trials passed and some failed; '
             'unknown means at least one result is missing or has no pass criterion.</p>')
    body += '<section class="results-section" id="benchmark-results" data-results><h2>Benchmark results</h2>'
    body += table_controls([record['harness'] for record in records], len(records))
    body += ('<div class="table-wrap"><table data-results-table><thead><tr>' +
             sort_header('Benchmark', 'benchmark') + sort_header('Harness', 'harness') +
             sort_header('Grade', 'grade', numeric=True) + '<th>Measurements</th>' +
             sort_header('Model requests', 'requests', numeric=True) + sort_header('Tool calls', 'tools', numeric=True) +
             sort_header('Captured tokens', 'tokens', numeric=True) + '</tr></thead>')
    for link, _, identity, record in entries:
        row_outcome, row_passed, row_scheduled = pass_outcome([record])
        measurement = 'complete' if record['measurement_complete'] else 'incomplete'
        badge = 'badge-complete' if record['measurement_complete'] else 'badge-worse'
        attributes = row_attributes(record, outcome=row_outcome, incomplete=not record['measurement_complete'],
                                    grade=record['grade'], requests=record['model_requests'],
                                    tools=record['tool_calls'], tokens=record['captured_tokens'])
        body += (f'<tbody {attributes}><tr><td><a href="{esc(link)}">{esc(identity[1])}</a>'
                 f'<small>{esc(identity[0])}</small></td><td>{esc(identity[2])}</td>'
                 f'<td><strong>{value(record["grade"])}</strong> {outcome_badge(row_outcome)}'
                 f'<small>{row_passed} / {row_scheduled} trials passed</small></td>'
                 f'<td><span class="badge {badge}">{measurement}</span></td>'
                 f'<td>{value(record["model_requests"])}</td><td>{value(record["tool_calls"])}</td>'
                 f'<td>{value(record["captured_tokens"])}</td></tr></tbody>')
    return Section(body + '</table></div><p data-empty-results hidden>No benchmarks match these filters.</p></section>')


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
        sections.append(comparison_section(payload, local_links, sources[0].run_info, source.run_info,
                                           section_id=f'comparison-{index}'))
        payloads.append(payload)
    if len(sources) > 2:
        links = [('Candidate ' + str(index) + ' · ' + run_title(source.run_info), f'#comparison-{index}')
                 for index, source in enumerate(sources[1:], 1)]
        sections.insert(0, Section(navigation(links, label='Candidates')))
    return sections, payloads


def build_report(run_dirs):
    """Compose an in-memory document using the same sections as written reports."""
    sources = load_sources(run_dirs)
    if len(sources) > 1:
        sections, _ = comparison_sections(sources)
        return render_page(comparison_title(sources), sections)
    source = sources[0]
    context = RenderContext()
    info = source.run_info
    sections = [Section('<p class="eyebrow">Run overview</p><h1>' + esc(run_title(info)) +
                        '</h1>' + run_facts(info))]
    for record in source.benchmarks.values():
        sections.append(benchmark_section(record, source.harnesses[Path(record['directory'])], context))
    return render_page(run_title(info), sections)


def comparison_title(sources):
    baseline = sources[0].run_info
    benchmark = baseline.get('benchmark') or 'Benchmark'
    if len(sources) == 2:
        return f"{benchmark} comparison · {baseline.get('model') or 'unknown'} vs {sources[1].run_info.get('model') or 'unknown'}"
    return f"{benchmark} comparison · {len(sources) - 1} candidates"


def write_reports(run_dirs, destination):
    """Write portable HTML, local assets, selected evidence, and comparison JSON."""
    from importlib.resources import files
    from acb.report_bundle import EvidenceExport
    sources = load_sources(run_dirs)
    for source in sources:
        source.benchmarks
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    assets = destination.parent / (destination.stem + '-benchmarks')
    assets.mkdir(exist_ok=True)
    for name in ('chart.umd.min.js', 'Chart.js.LICENSE.md', 'report.js'):
        (assets / name).write_bytes(files('acb').joinpath('report_assets', name).read_bytes())
    links, index, pages = {}, [], []
    for number, source in enumerate(sources):
        side = 'run' if len(sources) == 1 else ('baseline' if number == 0 else f'candidate-{number}')
        for identity, record in source.benchmarks.items():
            name = side + '-' + filename(identity)
            link = assets.name + '/' + name
            links[(number, identity)] = link
            index.append((link, side, identity, record))
            pages.append((source, record, name))
    for number, (source, record, name) in enumerate(pages):
        harness = source.harnesses[Path(record['directory'])]
        evidence_name = Path(name).stem
        exporter = EvidenceExport(assets / 'evidence' / evidence_name, 'evidence/' + evidence_name)
        (assets / name).write_text(render_benchmark(
            record, harness, source.run_info, overview='../' + destination.name,
            previous=pages[number - 1][2] if number else None,
            next_page=pages[number + 1][2] if number + 1 < len(pages) else None,
            asset_prefix='', exporter=exporter))
    prefix = assets.name + '/'
    if len(sources) > 1:
        sections, payloads = comparison_sections(sources, links)
        metadata = destination.with_suffix('.comparison.json')
        sections.insert(0, Section(navigation([('Download comparison data', metadata.name)])))
        destination.write_text(render_page(comparison_title(sources), sections, asset_prefix=prefix))
        metadata.write_text(json.dumps(requested_config(payloads), indent=2))
    else:
        info = sources[0].run_info
        nav = Section(navigation([('Benchmark results', '#benchmark-results')]))
        destination.write_text(render_page(run_title(info), [nav, single_run_index(index, info)], asset_prefix=prefix))
    return destination
