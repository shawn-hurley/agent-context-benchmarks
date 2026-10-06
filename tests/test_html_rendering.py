from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from acb.comparison_html import build_report, write_reports
from acb.html_components import ARTIFACT_BUDGET, PREVIEW_BYTES, script_json
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
                     'tool_name': 'Bash', 'tool_detail': 'echo', 'request_id': row['instance_id'],
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
    assert len(document.links) == 8  # Baseline reused for two candidate comparisons.
    assert all((destination.parent / link).is_file() for link in document.links)
    payloads = json.loads(destination.with_suffix('.comparison.json').read_text())
    assert len(payloads) == 2
    for path in (destination.parent / 'comparison-benchmarks').glob('*.html'):
        text = path.read_text()
        selected, excluded = ('alpha', 'beta') if '<h2>alpha</h2>' in text else ('beta', 'alpha')
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
    assert '&lt;/script&gt;' in text
    assert 'OMITTED-TAIL' not in text
    assert 'Additional artifact previews omitted' in text
    assert len(text) < ARTIFACT_BUDGET + 60000
    assert (trial / 'large.log').stat().st_size > PREVIEW_BYTES
    assert 'Original trial artifacts' in text


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
    path = write_reports(root, tmp_path / 'report.html')
    document = Document(path.read_text())
    assert len(document.links) == 1
    assert (path.parent / document.links[0]).is_file()
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
