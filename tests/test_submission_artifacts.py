import json
from pathlib import Path

import pytest

from acb.harbor.results import import_results
from acb.harbor.submission import snapshot_pair
from acb.report_data import HarnessReport


def fixture(tmp_path, staged, patch=None):
    trial = tmp_path / '.harbor' / 'trial'
    native = trial / ('steps/verify/verifier/native' if staged else 'verifier/native')
    candidate = native / 'scarfbench-eval' / 'app' / 'run_1'
    for name, text in [('input', 'original'), ('output', 'updated')]:
        tree = candidate / name
        tree.mkdir(parents=True)
        (tree / 'app.txt').write_text(text + '\n')
    # Predictions are written before Harbor moves the staged verifier directory.
    old_output = trial / 'verifier/native/scarfbench-eval/app/run_1'
    prediction = {'instance_id': 'task', 'model_patch': patch, 'output': str(old_output)}
    (native / 'prediction.json').write_text(json.dumps(prediction))
    result = {'id': 'id-1', 'task_name': 'task', 'trial_name': 'trial',
              'config': {'agent': {'kwargs': {'harness': 'goose'}}},
              'verifier_result': {'rewards': {'reward': 1}}}
    if staged:
        result['step_results'] = [{'step_name': 'plan'}, {'step_name': 'execute'}, {'step_name': 'verify'}]
    plan = {'benchmark_config': {'reward_metric': 'reward', 'success_value': 1},
            'harnesses': {'goose': {}}, 'run_id': 'test', 'benchmark': 'scarfbench',
            'model': {'name': 'local'}, 'proxy': 'praxis',
            'manifest': {'source': 'local', 'revision': None}}
    return plan, result, native


@pytest.mark.parametrize('staged', [False, True])
def test_import_preserves_directory_diff(tmp_path, staged):
    plan, result, _ = fixture(tmp_path, staged)
    import_results(plan, tmp_path, [result])
    directory = tmp_path / 'goose/id-1'
    assert json.loads((directory / 'prediction.json').read_text())['instance_id'] == 'id-1'
    diff = (directory / 'source-changes.diff').read_text()
    assert '-original' in diff and '+updated' in diff


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.parametrize('patch', [None, 'diff --git a/app b/app\n'])
def test_report_recovers_unimported_submission_without_writing(tmp_path, staged, patch):
    _, result, _ = fixture(tmp_path, staged, patch)
    directory = tmp_path / 'goose/id-1'
    directory.mkdir(parents=True)
    (directory / 'harbor-result.json').write_text(json.dumps(result))
    report = HarnessReport(tmp_path / 'goose', {})
    if patch is not None:
        assert report.prediction('id-1') == patch
    else:
        diff = report.source_changes('id-1')['diff']
        assert '-original' in diff and '+updated' in diff
    assert not (directory / 'prediction.json').exists()
    assert not (directory / 'source-changes.diff').exists()


def test_report_supports_older_imported_prediction(tmp_path):
    _, result, native = fixture(tmp_path, False)
    directory = tmp_path / 'goose/id-1'
    directory.mkdir(parents=True)
    (directory / 'prediction.json').write_bytes((native / 'prediction.json').read_bytes())
    assert '+updated' in HarnessReport(tmp_path / 'goose', {}).source_changes('id-1')['diff']


def test_snapshot_rejects_path_traversal_and_symlink_escape(tmp_path):
    native = tmp_path / 'trial/verifier/native'
    native.mkdir(parents=True)
    outside = tmp_path / 'outside'
    for name in ('input', 'output'):
        (outside / name).mkdir(parents=True)
    prediction = native / 'prediction.json'
    assert snapshot_pair({'output': str(native / '../../../outside')}, prediction) is None
    (native / 'escape').symlink_to(outside, target_is_directory=True)
    assert snapshot_pair({'output': str(native / 'escape')}, prediction) is None


def test_report_rejects_invalid_trial_name(tmp_path):
    directory = tmp_path / 'goose/id-1'
    directory.mkdir(parents=True)
    (directory / 'harbor-result.json').write_text(json.dumps({'trial_name': '../outside'}))
    assert HarnessReport(tmp_path / 'goose', {}).source_changes('id-1') is None
