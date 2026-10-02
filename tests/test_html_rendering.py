from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from acb.comparison_html import build_report, write_reports
from acb.html_components import ARTIFACT_BUDGET, PREVIEW_BYTES, diff_preview, script_json
from test_comparison import run
from acb.utils import normalize_instance_id_for_path


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = Counter()
        self.ids = []
        self.links = []
        self.chart_data = ''
        self.in_data = False
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.tags[tag] += 1
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.links.append(attrs['href'])
        if tag == 'script' and attrs.get('id') == 'chart-data':
            self.in_data = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_data = False

    def handle_data(self, value):
        if self.in_data:
            self.chart_data += value


def enrich(root):
    usage = [json.loads(line) for line in (root / 'usage.jsonl').read_text().splitlines()]
    rows = []
    for row in usage:
        trial = root / normalize_instance_id_for_path(row['instance_id'])
        trial.mkdir()
        (trial / 'prediction.json').write_text(json.dumps({'instance_id': row['instance_id'], 'model_patch': '+ patch for ' + row['instance_id']}))
        rows.append({**row, 'timestamp_ms': 100, 'content_type': 'tool_call',
                     'tool_name': 'echo', 'tool_detail': 'bash', 'request_id': row['instance_id'],
                     'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0})
    (root / 'benchmark_metrics.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (root / 'metrics.jsonl').write_text(''.join(json.dumps({'instance_id': row['instance_id'], 'resolved': True}) + '\n' for row in rows))
    return root


def test_inline_multi_harness_pages_share_components_and_unique_chart_ids(tmp_path):
    suite = tmp_path / 'suite'
    for harness in ('pi', 'goose'):
        root = enrich(run(suite / harness, {'a/b': 1, 'a_b': 0}))
        report = json.loads((root / 'report.json').read_text())
        report['harness'] = harness
        (root / 'report.json').write_text(json.dumps(report))
    text = build_report(suite)
    document = Document(text)
    assert document.tags['html'] == document.tags['body'] == 1
    assert document.tags['iframe'] == 0
    assert len(document.ids) == len(set(document.ids))
    charts = json.loads(document.chart_data)
    assert set(charts) <= set(document.ids)
    assert len(charts) == document.tags['canvas'] == 20
    assert text.count('Tool usage statistics') == 4
    assert '+ patch for a/b' in text and '+ patch for a_b' in text
    assert 'Request index' in text


def test_bundle_loads_each_harness_file_once_and_selects_only_benchmark_trials(tmp_path, monkeypatch):
    roots = [enrich(run(tmp_path / name, {'alpha': 1, 'beta': 0})) for name in ('a', 'b', 'c')]
    reads = Counter()
    original = Path.read_text

    def counted(path, *args, **kwargs):
        reads[path] += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', counted)
    destination = tmp_path / 'out' / 'comparison.html'
    write_reports(roots, destination)
    for root in roots:
        for name in ('report.json', 'usage.jsonl', 'metrics.jsonl', 'benchmark_metrics.jsonl'):
            assert reads[root / name] == 1
    document = Document(destination.read_text())
    assert document.tags['body'] == 1
    detail_links = [link for link in document.links if '-benchmarks/' in link and link.endswith('.html')]
    assert len(detail_links) == 8  # Baseline reused for two candidate comparisons.
    assert all((destination.parent / link).is_file() for link in detail_links)
    payloads = json.loads(destination.with_suffix('.comparison.json').read_text())
    assert len(payloads) == 2
    for path in (destination.parent / 'comparison-benchmarks').glob('*.html'):
        text = path.read_text()
        selected, excluded = ('alpha', 'beta') if '>alpha</h2>' in text else ('beta', 'alpha')
        assert '+ patch for ' + selected in text
        assert '+ patch for ' + excluded not in text
        assert excluded not in Document(text).chart_data


def test_escaping_and_bounded_artifacts(tmp_path):
    root = enrich(run(tmp_path / 'run', {'task': 1}))
    attack = '</script><script>alert("unsafe")</script>'
    assert '</script>' not in script_json({'data': attack})
    report = json.loads((root / 'report.json').read_text())
    report['model'] = attack
    (root / 'report.json').write_text(json.dumps(report))
    trial = root / 'task'
    (trial / 'prediction.json').write_text(json.dumps({'model_patch': attack}))
    (trial / 'transcript.jsonl').write_text(attack + '\n')
    (trial / 'large.log').write_text(attack + 'x' * (PREVIEW_BYTES * 3) + 'OMITTED-TAIL')
    for index in range(10):
        (trial / f'bulk-{index}.log').write_text('z' * PREVIEW_BYTES)
    text = build_report(root)
    assert attack not in text
    assert 'Agent patch' in text
    assert '&lt;/script&gt;' in text
    assert 'OMITTED-TAIL' not in text
    assert 'Additional artifact previews omitted' in text
    from importlib.resources import files
    inline_assets = sum(len(files('acb').joinpath('report_assets', name).read_text())
                        for name in ('report.js', 'chart.umd.min.js'))
    assert len(text) < ARTIFACT_BUDGET + 60000 + inline_assets
    assert (trial / 'large.log').stat().st_size > PREVIEW_BYTES
    assert 'Original trial artifacts' in text


def test_diff_preview_highlights_lines_and_escapes_patch_content():
    patch = 'diff --git a/a b/a\n@@ -1 +1 @@\n-<script>unsafe</script>\n+safe\n'
    result = diff_preview(patch)
    assert 'class="diff-file"' in result
    assert 'class="diff-hunk"' in result
    assert 'class="diff-del"' in result
    assert 'class="diff-add"' in result
    assert '<script>' not in result
    assert '&lt;script&gt;' in result
    assert 'Preview truncated' in diff_preview(patch, limit=10)


def test_directory_submission_shows_pre_verification_source_diff(tmp_path):
    root = run(tmp_path / 'suite' / 'goose', {'task': 1})
    native_run = tmp_path / 'suite' / '.harbor' / 'trial' / 'verifier' / 'native' / 'candidate'
    before, after = native_run / 'input', native_run / 'output'
    before.mkdir(parents=True)
    after.mkdir(parents=True)
    (before / 'pom.xml').write_text('<project>before</project>\n')
    (after / 'pom.xml').write_text('<project>after</project>\n')
    (after / 'target').mkdir()
    (after / 'target' / 'generated.txt').write_text('BUILD OUTPUT')
    trial = root / 'task'
    trial.mkdir()
    (trial / 'prediction.json').write_text(json.dumps({'model_patch': None, 'output': str(native_run)}))

    path = write_reports(root, tmp_path / 'source-tree.html')
    detail = next((tmp_path / 'source-tree-benchmarks').glob('*.html')).read_text()
    assert 'Source changes before verification' in detail
    assert 'Open full source diff' in detail and 'file://' not in detail
    assert 'before&lt;/project&gt;' in detail and 'after&lt;/project&gt;' in detail
    assert 'BUILD OUTPUT' not in detail
    assert 'No patch produced' not in detail
    assert path.exists()


def test_multistep_and_incomplete_evidence_remain_visible(tmp_path):
    root = run(tmp_path / 'run', {'task': None}, complete=False)
    report = json.loads((root / 'report.json').read_text())
    report['evaluations'][0]['step_results'] = [{'step_name': 'second'}, {'step_name': 'first'}]
    (root / 'report.json').write_text(json.dumps(report))
    for step in ('first', 'second'):
        directory = root / 'task' / 'steps' / step
        directory.mkdir(parents=True)
        (directory / 'transcript.jsonl').write_text(json.dumps({'type': 'turn_start'}) + '\n')
    rows = [json.loads(line) for line in (root / 'usage.jsonl').read_text().splitlines()]
    rows[0]['step_name'] = 'second'
    rows[0]['status_code'] = 0
    (root / 'usage.jsonl').write_text(json.dumps(rows[0]) + '\n')
    text = build_report(root)
    assert 'Grade: unavailable' in text and 'Tokens: unavailable' in text
    assert 'captured tokens: 100' in text
    assert 'Incomplete measurements' in text
    assert 'Captured observations (incomplete): turns 2' in text
    assert 'first' in text and 'second' in text
    assert 'sum of explicit per-step trajectory counts' in text
    assert '<td>second</td>' in text


def test_single_run_bundle_and_empty_input(tmp_path):
    root = run(tmp_path / 'run', {'task': 1})
    report = json.loads((root / 'report.json').read_text())
    report['dataset_metrics'] = {'aggregates': {'native-metric-marker': 1}}
    (root / 'report.json').write_text(json.dumps(report))
    path = write_reports(root, tmp_path / 'report.html')
    text = path.read_text()
    document = Document(text)
    detail_links = [link for link in document.links if link.endswith('.html')]
    assert len(detail_links) == 1
    assert (path.parent / detail_links[0]).is_file()
    assert 'Run overview' in text
    assert '<title>suite · model</title>' in text
    assert '<dt>Skills</dt><dd>Not recorded</dd>' in text
    assert 'Benchmark grade</span><strong class="stat-value">1.0' in text
    assert 'data-sort="requests"' in text
    assert '<dt>Migration</dt>' not in text
    detail = (path.parent / detail_links[0]).read_text()
    assert '<div class="stat stat-full-pass"><span class="stat-label">Result</span>' in detail
    assert 'Trial evidence</h3>' in detail
    assert 'Grade and step outcomes' in detail
    assert 'Configuration and comparability provenance' not in detail
    assert 'native-metric-marker' not in detail
    assert report['dataset_metrics'] == json.loads((root / 'report.json').read_text())['dataset_metrics']
    with pytest.raises(ValueError, match='at least one'):
        build_report([])
    with pytest.raises(ValueError, match='at least one'):
        write_reports([], path)


def test_request_gaps_are_unavailable_in_chart_data(tmp_path):
    root = run(tmp_path / 'run', {'task': 1}, complete=False)
    row = json.loads((root / 'usage.jsonl').read_text())
    row.update(turn_index=2, output_tokens=None)
    (root / 'usage.jsonl').write_text(json.dumps(row) + '\n')
    charts = json.loads(Document(build_report(root)).chart_data)
    chart = next(iter(charts.values()))
    assert chart['data']['datasets'][0]['data'] == [None, None, 100]
    assert chart['data']['datasets'][1]['data'] == [None, None, None]


def test_missing_trial_does_not_scan_other_trials_for_evidence(tmp_path):
    root = run(tmp_path / 'run', {'missing': None}, complete=False)
    report = json.loads((root / 'report.json').read_text())
    report['evaluations'][0]['trial_id'] = None
    (root / 'report.json').write_text(json.dumps(report))
    (root / 'unrelated.log').write_text('UNRELATED EVIDENCE')
    text = build_report(root)
    assert 'UNRELATED EVIDENCE' not in text
    assert 'Original trial artifacts' not in text


def test_timeline_sums_observations_at_shared_request_indices():
    from acb.report_metrics import prepare_timeline_data
    result = prepare_timeline_data([
        {'instance_id': 'a', 'turn_index': 0, 'content_type': 'tool_call', 'input_tokens': 10},
        {'instance_id': 'b', 'turn_index': 0, 'content_type': 'tool_call', 'input_tokens': 20},
    ])
    assert result['datasets'][0]['data'] == [30]


def test_tool_interaction_details_show_call_result_pair_and_next_call(tmp_path):
    root = run(tmp_path / 'run', {'task': 1})
    metrics = [
        {'instance_id': 'task', 'request_id': 'request-1', 'timestamp_ms': 1,
         'endpoint': '/v1/responses', 'content_type': 'tool_call',
         'input_tokens': 10, 'output_tokens': 2,
         'tools': [{'name': 'Bash', 'detail': 'rg TODO', 'call_id': 'call-1'}]},
        {'instance_id': 'task', 'request_id': 'request-2', 'timestamp_ms': 2,
         'endpoint': '/v1/responses', 'content_type': 'tool_call',
         'input_tokens': 20, 'output_tokens': 3,
         'tools': [{'name': 'Task', 'detail': 'research', 'call_id': 'call-2'}],
         'tool_results': [{'name': 'Bash', 'call_id': 'call-1'}]},
    ]
    (root / 'benchmark_metrics.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in metrics))

    text = build_report(root)

    assert 'Complete pairs</span><strong class="stat-value">1' in text
    assert 'Call only</span><strong class="stat-value">1' in text
    assert 'Inspect 2 tool interactions' in text
    assert 'call-1' in text and 'call-2' in text
    assert 'rg TODO' in text and 'research' in text
    assert 'request-1, request-2' in text
    assert '<span class="stat-label">Grade</span>' in text


def test_comparison_has_summary_cards_and_quality_badges(tmp_path):
    baseline = run(tmp_path / 'baseline', {'task': 0})
    candidate = run(tmp_path / 'candidate', {'task': 1})
    (baseline / 'requested.json').write_text(json.dumps({'skills': ['baseline-skill']}))
    (candidate / 'requested.json').write_text(json.dumps({'skills': ['candidate-skill']}))

    text = build_report([baseline, candidate])

    assert '<title>suite comparison · model vs model</title>' in text
    assert 'Baseline · suite · model' in text and 'Candidate · suite · model' in text
    assert '<dt>Skills</dt><dd>baseline-skill</dd>' in text
    assert '<dt>Skills</dt><dd>candidate-skill</dd>' in text
    assert 'Graded benchmarks</span><strong class="stat-value">1 / 1' in text
    assert 'Comparable tokens</span><strong class="stat-value">1 / 1' in text
    assert 'badge-better' in text
    assert 'badge-failure">failure</span>' in text
    assert 'badge-full-pass">full pass</span>' in text


def test_run_overview_grade_uses_all_tasks_and_requires_full_coverage(tmp_path):
    complete = run(tmp_path / 'complete', {'passed': 1, 'failed': 0})
    complete_page = write_reports(complete, tmp_path / 'complete.html').read_text()
    assert 'Benchmark grade</span><strong class="stat-value">0.5' in complete_page
    assert 'data-sort="grade"' in complete_page and '<th>Measurements</th>' in complete_page
    assert '<th>Pass outcome</th>' not in complete_page
    assert 'badge-partial-pass">partial pass</span> 1 / 2 trials passed' in complete_page
    assert '<td><strong>1.0</strong> <span class="badge badge-full-pass">full pass</span>' in complete_page
    assert '<td><strong>0.0</strong> <span class="badge badge-failure">failure</span>' in complete_page

    partial = run(tmp_path / 'partial', {'passed': 1, 'missing': None})
    partial_page = write_reports(partial, tmp_path / 'partial.html').read_text()
    assert 'Benchmark grade</span><strong class="stat-value">unavailable' in partial_page
    assert 'badge-unknown">unknown</span>' in partial_page

    mixed = run(tmp_path / 'mixed', {'task': 1})
    report = json.loads((mixed / 'report.json').read_text())
    report['evaluations'].append({**report['evaluations'][0], 'trial_id': 'retry',
                                  'resolved': False, 'rewards': {'score': 0}})
    (mixed / 'report.json').write_text(json.dumps(report))
    mixed_page = write_reports(mixed, tmp_path / 'mixed.html').read_text()
    assert '<td><strong>0.5</strong> <span class="badge badge-partial-pass">partial pass</span><small>1 / 2 trials passed' in mixed_page

    (complete / 'resolved.json').write_text(json.dumps({'benchmark_config': {'success_value': 1}}))
    configured_page = write_reports(complete, tmp_path / 'configured.html').read_text()
    assert '<dt>Grade metric</dt>' not in configured_page
    assert '<dt>Pass value</dt>' not in configured_page
    assert 'Benchmarks can use different grade scales' in configured_page

    scaled = run(tmp_path / 'scaled', {'partial_credit': 7, 'passed': 10})
    scaled_report = json.loads((scaled / 'report.json').read_text())
    scaled_report['evaluations'][0]['resolved'] = False
    (scaled / 'report.json').write_text(json.dumps(scaled_report))
    (scaled / 'resolved.json').write_text(json.dumps({'benchmark_config': {'success_value': 10}}))
    scaled_page = write_reports(scaled, tmp_path / 'scaled.html').read_text()
    assert '<dt>Pass value</dt>' not in scaled_page
    assert 'Benchmark grade</span><strong class="stat-value">8.5' in scaled_page
    assert 'badge-partial-pass">partial pass</span> 1 / 2 trials passed' in scaled_page

    from acb.comparison_html import benchmark_grade
    first = {'dataset': 'suite', 'harness': 'pi', 'model': 'model',
             'definition': {'metric': 'reward'}, 'grades': [1.0, 1.0]}
    second = {**first, 'grades': [0.0]}
    assert benchmark_grade([first, second]) == pytest.approx(2 / 3)
    second = {**second, 'definition': {'metric': 'resolved'}}
    assert benchmark_grade([first, second]) is None


def test_run_title_and_facts_show_model_migration_workflow_and_skills(tmp_path):
    root = run(tmp_path / 'run', {'task': 1})
    (root / 'requested.json').write_text(json.dumps({
        'run_id': 'migration-run', 'benchmark': 'scarfbench', 'model': 'requested-model',
        'workflow': 'kantra-controller', 'skills': [{'name': 'configured-skill'}],
        'mcp_servers': [{'name': 'filesystem'}], 'extensions': [],
    }))
    (root / 'resolved.json').write_text(json.dumps({
        'run_id': 'migration-run', 'benchmark': 'scarfbench',
        'model': {'name': 'gpt-5.6-luna'}, 'proxy': 'praxis',
        'benchmark_config': {'source': 'jakarta', 'target': 'quarkus'},
        'workflow': {'name': 'kantra-controller', 'environment': {
            'assets': ['skills/kantra/SKILL.md']}},
        'harnesses': {'pi': {'version': '1.2', 'skills': [],
                             'execution_integrations': [{'name': 'rtk'}]}},
    }))
    trial = root / 'task'
    trial.mkdir()
    (trial / 'skill-delivery.json').write_text(json.dumps({
        'task_skills': [{'name': 'task-guide'}], 'configured_skills': [],
    }))

    path = write_reports(root, tmp_path / 'report.html')
    overview = path.read_text()
    detail = next((tmp_path / 'report-benchmarks').glob('*.html')).read_text()
    for text in (overview, detail):
        assert 'scarfbench · jakarta → quarkus · gpt-5.6-luna · kantra-controller' in text
        assert '<dt>Run ID</dt><dd>migration-run</dd>' in text
        assert '<dt>Harness</dt><dd>pi 1.2</dd>' in text
        assert '<dt>Skills</dt><dd>configured-skill, kantra (workflow), task-guide (task)</dd>' in text
        assert '<dt>MCP servers</dt><dd>filesystem</dd>' in text
        assert '<dt>Integrations</dt><dd>rtk</dd>' in text
        assert '<dt>Migration</dt><dd>jakarta → quarkus</dd>' in text
    assert '<title>scarfbench · jakarta → quarkus · gpt-5.6-luna · kantra-controller</title>' in overview
    assert '<title>task · scarfbench · jakarta → quarkus · gpt-5.6-luna · kantra-controller</title>' in detail


def test_run_facts_escape_configured_names(tmp_path):
    root = run(tmp_path / 'run', {'task': 1})
    (root / 'requested.json').write_text(json.dumps({
        'benchmark': '<script>benchmark</script>', 'model': 'model',
        'skills': [{'name': '<script>skill</script>'}],
    }))
    text = write_reports(root, tmp_path / 'report.html').read_text()
    assert '<script>skill</script>' not in text
    assert '&lt;script&gt;skill&lt;/script&gt;' in text


def test_harness_report_uses_matching_parent_run_configuration(tmp_path):
    suite = tmp_path / 'suite'
    harness = run(suite / 'goose', {'task': 1})
    report = json.loads((harness / 'report.json').read_text())
    report['run_id'] = 'suite-run'
    (harness / 'report.json').write_text(json.dumps(report))
    (suite / 'requested.json').write_text(json.dumps({
        'run_id': 'suite-run', 'skills': ['migrate-skill'],
    }))
    (suite / 'resolved.json').write_text(json.dumps({
        'run_id': 'suite-run', 'benchmark': 'scarfbench',
        'model': {'name': 'gpt-5.6-luna'},
    }))

    text = write_reports(harness, tmp_path / 'report.html').read_text()

    assert '<title>scarfbench · gpt-5.6-luna</title>' in text
    assert '<dt>Skills</dt><dd>migrate-skill</dd>' in text
