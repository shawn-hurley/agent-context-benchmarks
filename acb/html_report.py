"""Benchmark detail sections shared by standalone and comparison reports."""

from acb.costs import load_cost_table
from acb.html_components import (
    RenderContext, Section, artifact_sections, diff_preview, escape, json_details, render_page,
    run_facts, run_title, stat_cards, table, value, grade_definition, pass_outcome, outcome_badge,
)
from acb.report_metrics import (
    aggregate_benchmark_metrics, content_type_breakdown, context_growth_datasets,
    cost_datasets, normalize_tool_interactions, per_turn_averages, prepare_timeline_data,
    shell_command_breakdown, color,
)
from acb.usage import is_model_request
from acb.report_ui import navigation
from acb.provenance import requested_config


def request_section(record, section_id="requests"):
    rows = []
    for trial in record['trials']:
        for request in trial['requests']:
            status = request.get('status_code')
            rows.append([trial['id'], request.get('step_name') or 'single', request.get('turn_index'),
                         *[request.get(key) for key in ('input_tokens', 'output_tokens', 'cache_read_tokens',
                                                      'cache_creation_tokens', 'duration_ms')],
                         status if type(status) is int and 100 <= status <= 599 else None])
    return Section(f'<h2 id="{escape(section_id)}">Recorded model requests</h2><p>Request indices are independent of agent turns. '
                   'Tool arguments and results appear in trial trajectories.</p>' + table(
                       ['Trial', 'Step', 'Request index', 'Fresh input', 'Output', 'Cache read',
                        'Cache creation', 'Duration (ms)', 'HTTP status'], rows))


def evidence_section(record, source, *, section_ids=None, exporter=None):
    section_ids = section_ids or {"summary": "summary", "evidence": "evidence"}
    outcome, passed, scheduled = pass_outcome([record])
    body = (f'<p class="eyebrow">Benchmark detail</p><h2 id="{escape(section_ids['summary'])}">{escape(record["benchmark"])}</h2>'
            f'<p class="subtitle">{escape(record["dataset"])} · '
            f'{escape(record["harness"])} · {escape(record["model"])}</p>')
    body += stat_cards([
        ('Result', outcome.capitalize() + (f' ({passed}/{scheduled})' if scheduled > 1 else ''),
         outcome.replace(' ', '-')),
        ('Grade', record['grade']), ('Attempts', len(record['trials'])),
        ('Model requests', record['model_requests']), ('Agent turns', record['turns']),
        ('Tool calls', record['tool_calls']), ('Captured tokens', record['captured_tokens']),
    ])
    body += (f'<p>Grade: {value(record["grade"])}; definition: {grade_definition(record["definition"])}. '
            f'Tokens: {value(record["tokens"])}; captured tokens: {value(record["captured_tokens"])}.</p>')
    if not record['measurement_complete']:
        body += '<p class="warning">Incomplete measurements: captured observations and charts are partial, not comparable totals.</p>'
    body += '<p>Agent turns: ' + value(record['turns']) + '; model requests: ' + value(record['model_requests']) + '; tool calls: ' + value(record['tool_calls']) + '.</p>'
    if not record['measurement_complete']:
        body += ('<p>Captured observations (incomplete): turns ' + value(record['captured_turns']) +
                 ', model requests ' + value(record['captured_model_requests']) +
                 ', tool calls ' + value(record['captured_tool_calls']) + '.</p>')
    body += '<h3 id="' + escape(section_ids['evidence']) + '">Trial evidence</h3>'
    for trial in record['trials']:
        trial_exporter = exporter.trial(trial['id']) if exporter else None
        evaluation = trial['evaluation']
        status = evaluation.get('status', 'unknown')
        exception = evaluation.get('exception') or {}
        trial_outcome, _, _ = pass_outcome([{'trials': [trial]}])
        body += (f'<details><summary>Trial {escape(trial["id"])} — {escape(status)} '
                 f'{outcome_badge(trial_outcome)} '
                 f'{escape(exception.get("exception_type", ""))}</summary>')
        body += json_details('Grade and step outcomes', requested_config(evaluation) if exporter else evaluation)
        body += json_details('Agent trajectory and tool interactions',
                             requested_config(trial['trajectory']) if exporter else trial['trajectory'])
        if trial_exporter:
            body += trial_exporter.trial_evidence(trial)
            body += trial_exporter.artifact_sections(source.trial_directory(trial['id']))
        else:
            body += artifact_sections(source.trial_directory(trial['id']))
        patch = source.prediction(trial['id'])
        changes = source.source_changes(trial['id']) if not patch else None
        if patch:
            body += '<details><summary>Agent patch</summary>' + diff_preview(patch)
            if trial_exporter:
                href = trial_exporter.write_text('model.patch.txt', patch)
                body += '<p><a href="' + escape(href) + '">Open full agent patch</a></p>'
            body += '</details>'
        elif changes is not None:
            body += '<details><summary>Source changes before verification</summary>'
            body += (diff_preview(changes['diff']) if changes['diff'] else '<p>No source changes detected.</p>')
            if trial_exporter:
                href = trial_exporter.write_text('source-changes.diff.txt', changes['diff'])
                body += '<p><a href="' + escape(href) + '">Open full source diff</a></p>'
            elif changes['artifact']:
                body += f'<p><a href="{escape(changes["artifact"].as_uri())}">Open full source diff</a></p>'
            elif changes['source'] and changes['candidate']:
                body += (f'<p><a href="{escape(changes["source"].as_uri())}">Original app</a> · '
                         f'<a href="{escape(changes["candidate"].as_uri())}">Submitted app</a></p>')
            body += '</details>'
        else:
            body += '<details><summary>Changes</summary><p>No patch or source diff was captured.</p></details>'
        body += '</details>'
    return Section(body)


def tool_interaction_section(record, source, section_id="tools"):
    """Show call/result identities captured by Praxis for this benchmark."""
    rows = []
    counts = {'complete': 0, 'call only': 0, 'result only': 0}
    for trial in record['trials']:
        metrics = [row for row in source.benchmark_metrics.get(trial['id'], [])
                   if not row.get('endpoint') or is_model_request(row)]
        for interaction in normalize_tool_interactions(metrics):
            state = ('complete' if interaction['complete_pair'] else
                     'call only' if interaction['call_only'] else 'result only')
            counts[state] += 1
            rows.append([
                trial['id'], interaction['name'], interaction['detail'],
                interaction['call_id'], state, ', '.join(interaction['request_ids']),
                interaction['tokens'], interaction['duration_ms'],
            ])
    body = f'<h2 id="{escape(section_id)}">Tool interactions</h2>'
    if not rows:
        return Section(body + '<p class="muted">Tool identities were not captured for this benchmark.</p>')
    body += stat_cards([('Complete pairs', counts['complete']),
                        ('Call only', counts['call only']), ('Result only', counts['result only'])])
    body += ('<p class="muted">A call and its matching result count as one interaction. '
             'When one model request contains several tool events, its tokens and duration are shared across them.</p>')
    body += ('<details><summary>Inspect ' + value(len(rows)) + ' tool interactions</summary>' +
             table(['Trial', 'Tool', 'Detail', 'Call ID', 'State', 'Request IDs', 'Tokens', 'Duration (ms)'], rows) +
             '</details>')
    return Section(body)


def chart_section(record, source, context, section_id="charts"):
    """Select indexed trial observations; never reread or aggregate the whole run."""
    metrics, usage, classified = [], [], []
    for trial in record['trials']:
        identity = trial['id']
        requests = [row for row in source.benchmark_metrics.get(identity, [])
                    if not row.get('endpoint') or is_model_request(row)]
        metric = aggregate_benchmark_metrics(requests) if requests else dict(source.metrics.get(identity, {}))
        if metric:
            metric['instance_id'] = identity
            metrics.append(metric)
            classified.extend(row for row in metric.get('requests', []) if 'content_type' in row)
        usage.extend(trial['requests'])
    sections = [Section(f'<h2 id="{escape(section_id)}">Captured request charts and tool breakdowns</h2><p class="muted">Charts describe recorded requests. '
                        'Missing telemetry is unavailable. Charts and evidence tables work offline.</p>')]
    datasets = context_growth_datasets(metrics)
    if any(dataset['data'] for dataset in datasets):
        sections.append(context.chart('Context growth (input + cache read + cache creation)', 'line', {
            'labels': list(range(max(len(dataset['data']) for dataset in datasets))), 'datasets': datasets,
        }, x='Model request index', y='Prompt tokens'))
    turns, inputs, outputs, durations = per_turn_averages(usage)
    if turns:
        sections.append(context.chart('Tokens per model request (average across trials)', 'bar', {
            'labels': turns, 'datasets': [
                {'label': 'Fresh input', 'data': inputs, 'backgroundColor': color(0)},
                {'label': 'Output', 'data': outputs, 'backgroundColor': color(1)},
            ]}, x='Model request index', y='Tokens'))
        if any(duration is not None for duration in durations):
            sections.append(context.chart('Duration per model request (average across trials)', 'line', {
                'labels': turns, 'datasets': [{'label': 'Duration (ms)', 'data': durations, 'borderColor': color(2)}],
            }, x='Model request index', y='Duration (ms)'))
    cost = load_cost_table().get(record['model'])
    if cost and usage:
        datasets = cost_datasets(usage, cost)
        sections.append(context.chart('Estimated cost of captured requests (cumulative per trial)', 'line', {
            'labels': list(range(max(len(dataset['data']) for dataset in datasets))), 'datasets': datasets,
        }, x='Model request index', y='USD'))
    if classified:
        breakdown = content_type_breakdown(classified)
        by_type = breakdown['by_type']
        sections.append(context.chart('Captured tokens by content type', 'pie', {
            'labels': list(by_type), 'datasets': [{'data': [item['total_tokens'] for item in by_type.values()],
                                                  'backgroundColor': [color(i) for i in range(len(by_type))]}],
        }))
        sections.append(context.chart('Content types across recorded requests', 'bar', prepare_timeline_data(classified),
                                      x='Model request index', y='Fresh input tokens', stacked=True))
        rows = []
        for tool in sorted(breakdown['tool_usage'].values(), key=lambda item: item['total_tokens'], reverse=True):
            rows.append([tool['tool_name'], tool['tool_detail'], tool['interactions'], tool['complete_pairs'],
                         tool['call_only'], tool['result_only'], tool['total_tokens'],
                         tool['total_tokens'] / tool['interactions'] if tool['interactions'] else None])
        if rows:
            sections.append(Section('<h3>Tool usage statistics</h3>' + table(
                ['Tool', 'Detail', 'Interactions', 'Complete pairs', 'Call only', 'Result only', 'Tokens', 'Tokens / interaction'], rows)))
        commands = shell_command_breakdown(classified)
        if commands:
            sections.append(context.chart('Shell command token distribution', 'pie', {
                'labels': list(commands), 'datasets': [{'data': [item['total_tokens'] for item in commands.values()],
                                                       'backgroundColor': [color(i) for i in range(len(commands))]}],
            }))
    return Section.join(sections)


def benchmark_section(record, source, context, *, exporter=None):
    identity = context.identifier('benchmark')
    ids = {key: identity + '-' + key for key in ('summary', 'evidence', 'requests', 'tools', 'charts')}
    nav = Section(navigation([(label, '#' + ids[key]) for key, label in
                             [('summary', 'Result'), ('evidence', 'Trial evidence'), ('requests', 'Requests'),
                              ('tools', 'Tool activity'), ('charts', 'Charts')]], label='Benchmark sections'))
    content = Section.join([nav, evidence_section(record, source, section_ids=ids, exporter=exporter),
                            request_section(record, ids['requests']), tool_interaction_section(record, source, ids['tools']),
                            chart_section(record, source, context, ids['charts'])])
    return Section(f'<section class="benchmark" id="{identity}">{content.body}</section>', content.charts)


def render_benchmark(record, source, run_info, *, overview=None, previous=None, next_page=None,
                     asset_prefix=None, exporter=None):
    title = run_title(run_info)
    nav = Section(navigation([('← Back to overview', overview), ('← Previous benchmark', previous),
                              ('Next benchmark →', next_page)]))
    header = Section('<p class="eyebrow">Run context</p><h1>' + escape(title) + '</h1>' +
                     run_facts(run_info))
    return render_page(f'{record["benchmark"]} · {title}',
                       [nav, header, benchmark_section(record, source, RenderContext(), exporter=exporter)],
                       asset_prefix=asset_prefix)
