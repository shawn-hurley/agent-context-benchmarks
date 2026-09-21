"""Benchmark detail sections shared by standalone and comparison reports."""

from acb.costs import load_cost_table
from acb.html_components import (
    RenderContext, Section, artifact_sections, escape, json_details, preview, render_page, table, value, grade_definition,
)
from acb.report_metrics import (
    aggregate_benchmark_metrics, content_type_breakdown, context_growth_datasets,
    cost_datasets, per_turn_averages, prepare_timeline_data, shell_command_breakdown, color,
)
from acb.usage import is_model_request


def request_section(record):
    rows = []
    for trial in record['trials']:
        for request in trial['requests']:
            status = request.get('status_code')
            rows.append([trial['id'], request.get('step_name') or 'single', request.get('turn_index'),
                         *[request.get(key) for key in ('input_tokens', 'output_tokens', 'cache_read_tokens',
                                                      'cache_creation_tokens', 'duration_ms')],
                         status if type(status) is int and 100 <= status <= 599 else None])
    return Section('<h2>Recorded model requests</h2><p>Request indices are independent of agent turns. '
                   'Tool arguments and results appear in trial trajectories.</p>' + table(
                       ['Trial', 'Step', 'Request index', 'Fresh input', 'Output', 'Cache read',
                        'Cache creation', 'Duration (ms)', 'HTTP status'], rows))


def evidence_section(record, source):
    body = (f'<h2>{escape(record["benchmark"])}</h2><p>{escape(record["dataset"])} · '
            f'{escape(record["harness"])} · {escape(record["model"])}</p>'
            f'<p>Grade: {value(record["grade"])}; definition: {grade_definition(record["definition"])}. '
            f'Tokens: {value(record["tokens"])}; captured tokens: {value(record["captured_tokens"])}.</p>')
    if not record['measurement_complete']:
        body += '<p class="warning">Incomplete measurements: captured observations and charts are partial, not comparable totals.</p>'
    body += json_details('Configuration and comparability provenance', {
        key: record[key] for key in ('dataset', 'harness', 'model', 'provenance', 'grades',
                                    'grade_aggregation', 'measurement_complete', 'token_buckets')})
    body += '<p>Agent turns: ' + value(record['turns']) + '; model requests: ' + value(record['model_requests']) + '; tool calls: ' + value(record['tool_calls']) + '.</p>'
    if not record['measurement_complete']:
        body += ('<p>Captured observations (incomplete): turns ' + value(record['captured_turns']) +
                 ', model requests ' + value(record['captured_model_requests']) +
                 ', tool calls ' + value(record['captured_tool_calls']) + '.</p>')
    if record.get('dataset_metrics'):
        body += json_details('Dataset-native metrics (whole configuration, not this benchmark grade)', record['dataset_metrics'])
    for trial in record['trials']:
        evaluation = trial['evaluation']
        status = evaluation.get('status', 'unknown')
        exception = evaluation.get('exception') or {}
        body += (f'<details><summary>Trial {escape(trial["id"])} — {escape(status)} '
                 f'{escape(exception.get("exception_type", ""))}</summary>')
        body += json_details('Grade and step outcomes', evaluation)
        body += json_details('Agent trajectory and tool interactions', trial['trajectory'])
        body += artifact_sections(source.trial_directory(trial['id']))
        patch = source.prediction(trial['id'])
        body += '<details><summary>Patch</summary>' + (preview(patch) if patch else '<p>No patch produced.</p>') + '</details></details>'
    return Section(body)


def chart_section(record, source, context):
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
    sections = [Section('<h2>Captured request charts and tool breakdowns</h2><p class="muted">Charts describe recorded requests. '
                        'Missing telemetry is unavailable. Charts require Chart.js; evidence tables remain readable offline.</p>')]
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


def benchmark_section(record, source, context):
    identity = context.identifier('benchmark')
    content = Section.join([evidence_section(record, source), request_section(record), chart_section(record, source, context)])
    return Section(f'<section class="benchmark" id="{identity}">{content.body}</section>', content.charts)


def render_benchmark(record, source):
    return render_page(record['benchmark'], [Section('<h1>Benchmark detail</h1>'),
                                            benchmark_section(record, source, RenderContext())])
