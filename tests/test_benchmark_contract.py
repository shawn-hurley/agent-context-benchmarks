import json
from pathlib import Path
import shutil

from acb.benchmark_contract import benchmark_contract
from acb.comparison import compare
from acb.harbor.dataset import prepare_dataset
from acb.workflows import load_workflow
from test_comparison import run
from test_harbor_benchmark_tasks import scarf_plan


def snapshots(tmp_path):
    plan = scarf_plan(tmp_path)
    baseline = Path(prepare_dataset(plan)['tasks'][0]['path'])
    plan['workflow'] = load_workflow('kantra-controller', tmp_path, 'scarfbench', ['goose'])
    candidate = Path(prepare_dataset(plan)['tasks'][0]['path'])
    return baseline, candidate


def test_workflow_changes_preserve_benchmark_contract(tmp_path):
    before, after = snapshots(tmp_path)
    assert before != after
    assert benchmark_contract(before) == benchmark_contract(after)
    assert benchmark_contract(before) is not None


def test_deployment_changes_preserve_behavior_contract(tmp_path):
    before, after = snapshots(tmp_path)
    contract = benchmark_contract(after)
    target = after / 'tests/benchmark/business_domain/cart/quarkus'
    (target / 'Dockerfile').write_text('FROM changed-build-environment')
    (target / 'Makefile').write_text('changed deployment setup')
    assert benchmark_contract(after) == contract
    (target / 'test.sh').write_text('new behavioral assertions')
    assert benchmark_contract(after)['grading'] != contract['grading']


def test_source_changes_modify_input_identity(tmp_path):
    before, after = snapshots(tmp_path)
    contract = benchmark_contract(after)
    (after / 'environment/source/Main.java').write_text('changed application')
    assert benchmark_contract(after)['inputs'] != contract['inputs']


def test_legacy_report_recovers_contract_without_reimport(tmp_path):
    before, after = snapshots(tmp_path)
    a = run(tmp_path / 'a', {'one': 0}, tokens=100)
    b = run(tmp_path / 'b', {'one': 1}, tokens=120)
    for index, (path, snapshot) in enumerate(((a,before),(b,after))):
        report = json.loads((path / 'report.json').read_text())
        report['comparison_provenance']['tasks']['one'] = {'sha256': f'different-bundle-{index}'}
        report['comparison_provenance']['conditions']['timeout'] = 900 * (index+1)
        (path / 'report.json').write_text(json.dumps(report))
        plan = {'run_id': path.name, 'manifest': {'tasks': [{'id': 'one', 'path': str(snapshot)}]}}
        (path / 'resolved.json').write_text(json.dumps(plan))
    saved = [(path / 'report.json').read_bytes() for path in (a,b)]
    result = compare(a,b)
    assert result['quality'] == 'better'
    assert result['matched_tokens']['percent'] == 20
    assert saved == [(path / 'report.json').read_bytes() for path in (a,b)]


def test_swebench_image_setup_does_not_change_inputs(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    row = {'repo': 'owner/repo', 'base_commit': 'source-commit', 'problem_statement': 'Fix the bug',
           'test_patch': 'test assertions', 'FAIL_TO_PASS': ['test_bug'], 'PASS_TO_PASS': [],
           'image': 'old-image', 'eval_type': 'pytest', 'eval_script': 'pytest'}
    record = {'benchmark': 'swebench', 'extra': {'dataset_row': row}}
    path = tests / 'benchmark.json'
    path.write_text(json.dumps(record))
    contract = benchmark_contract(tmp_path)
    row['image'] = 'new-image'
    path.write_text(json.dumps(record))
    assert benchmark_contract(tmp_path) == contract
    row['test_patch'] = 'different assertions'
    path.write_text(json.dumps(record))
    assert benchmark_contract(tmp_path)['grading'] != contract['grading']
