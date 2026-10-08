import json
import math
from pathlib import Path

import pytest

from acb.harbor.results import evaluation, import_results
from acb.harbor.dataset import TASK_CHECKSUM_VERSION, checksum, select, verify_manifest


def test_rewards_preserve_errors_and_unknown_resolution():
    assert evaluation({"verifier_result": {"rewards": {"reward": 0}}}, "reward", 1)["resolved"] is False
    assert evaluation({"verifier_result": {"rewards": {"reward": 1}}}, "reward", 1)["resolved"] is True
    assert evaluation({"verifier_result": {"rewards": {"score": .7}}}, None)["resolved"] is None
    assert evaluation({"verifier_result": {"rewards": {"score": .7}}}, "score", .7)["resolved"] is True
    assert evaluation({"verifier_result": {"rewards": {"reward": 1}}, "exception_info": {"exception_type": "Crash"}}, "reward", 1)["status"] == "error"


@pytest.mark.parametrize("rewards", [None, {}, {"reward": "1"}, {"reward": float('nan')}, {"reward": float('inf')}, {"reward": True}])
def test_malformed_rewards_are_errors(rewards):
    value = evaluation({"verifier_result": {"rewards": rewards}}, "reward", 1)
    assert value["status"] == "error"
    assert value["resolved"] is None


def test_zero_traffic_failure_is_in_report(tmp_path):
    plan = {"benchmark_config": {"reward_metric": "reward", "success_value": 1}, "harnesses": {"goose": {}}, "run_id": "test", "benchmark": "rh-swe-bench", "model": {"name": "local"}, "proxy": "praxis", "manifest": {"source": "rounakbende/rh-swe-bench", "revision": "abc"}}
    result = {"id": "id-1", "task_name": "task-1", "trial_name": "task-1__trial", "config": {"agent": {"kwargs": {"harness": "goose"}}}, "exception_info": {"exception_type": "SetupError"}}
    import_results(plan, tmp_path, [result])
    report = json.loads((tmp_path / "goose/report.json").read_text())
    assert report["instances"] == 1
    assert report["evaluation_errors"] == 1
    assert report["unknown_resolution"] == 1
    assert report["avg_total_tokens"] == 0


def test_directory_submission_diff_is_saved_with_trial(tmp_path):
    from acb.harbor.paths import job_dir
    native = job_dir(tmp_path) / 'trial' / 'verifier' / 'native'
    candidate = native / 'candidate'
    before, after = candidate / 'input', candidate / 'output'
    before.mkdir(parents=True)
    after.mkdir()
    (before / 'app.txt').write_text('original\n')
    (after / 'app.txt').write_text('updated\n')
    (native / 'prediction.json').write_text(json.dumps({
        'instance_id': 'task', 'model_patch': None, 'output': str(candidate),
    }))
    plan = {'benchmark_config': {'reward_metric': 'reward', 'success_value': 1},
            'harnesses': {'goose': {}}, 'run_id': 'test', 'benchmark': 'scarfbench',
            'model': {'name': 'local'}, 'proxy': 'praxis',
            'manifest': {'source': 'local', 'revision': None}}
    result = {'id': 'id-1', 'task_name': 'task', 'trial_name': 'trial',
              'config': {'agent': {'kwargs': {'harness': 'goose'}}},
              'verifier_result': {'rewards': {'reward': 1}}}

    import_results(plan, tmp_path, [result])

    saved = tmp_path / 'goose' / 'id-1' / 'source-changes.diff'
    assert saved.is_file()
    assert '-original' in saved.read_text() and '+updated' in saved.read_text()


def test_selection_determinism_and_tamper_detection(tmp_path):
    assert select(["b", "a", "c"], ["c", "a"], 1) == ["a"]
    with pytest.raises(ValueError, match="unknown task"):
        select(["a"], ["x"], None)
    (tmp_path / "instruction.md").write_text("original")
    manifest = {"task_checksum_version": TASK_CHECKSUM_VERSION,
                "tasks": [{"id": "a", "path": str(tmp_path), "sha256": checksum(tmp_path)}]}
    verify_manifest(manifest)
    (tmp_path / "instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        verify_manifest(manifest)


def test_cancelled_job_counts_slots_without_trial_results(tmp_path):
    plan = {'benchmark_config': {'reward_metric': 'reward', 'success_value': 1},
            'harnesses': {'goose': {}, 'pi': {}}, 'attempts': 2,
            'run_id': 'test', 'benchmark': 'fixture', 'model': {'name': 'local'}, 'proxy': 'praxis',
            'manifest': {'source': 'local', 'revision': None, 'tasks': [{'id': 'task', 'sha256': 'hash'}]}}
    import_results(plan, tmp_path, [])
    for harness in plan['harnesses']:
        report = json.loads((tmp_path / harness / 'report.json').read_text())
        assert report['instances'] == 2
        assert report['missing_trials'] == 2
        assert report['evaluation_errors'] == 2
        assert all(item['trial_id'] is None for item in report['evaluations'])
    assert len(json.loads((tmp_path / 'missing-trials.json').read_text())) == 4


def test_controls_do_not_create_phantom_harness_trials(tmp_path):
    plan = {'benchmark_config': {}, 'harnesses': {'goose': {}, 'pi': {}},
            'run_id': 'test', 'benchmark': 'fixture', 'model': {'name': 'local'}, 'proxy': 'praxis',
            'manifest': {'source': 'local', 'revision': None, 'tasks': [{'id': 'task', 'sha256': 'hash'}]}}
    import_results(plan, tmp_path, [], control='oracle')
    assert (tmp_path / 'oracle/report.json').exists()
    assert not (tmp_path / 'goose/report.json').exists()


def test_local_task_snapshot_survives_source_edits(tmp_path):
    pytest.importorskip('filelock')
    from acb.harbor.dataset import snapshot_local_task
    source = tmp_path / 'source' / 'task'
    source.mkdir(parents=True)
    (source / 'instruction.md').write_text('original')
    frozen = snapshot_local_task(source, tmp_path / 'cache')
    (source / 'instruction.md').write_text('edited')
    assert (frozen / 'instruction.md').read_text() == 'original'
    second = snapshot_local_task(source, tmp_path / 'cache')
    assert frozen != second
    assert (second / 'instruction.md').read_text() == 'edited'


def test_permission_changes_create_new_snapshot_and_invalidate_prepared_manifest(tmp_path):
    from acb.harbor.dataset import snapshot_local_task
    source = tmp_path / "task"
    source.mkdir()
    script = source / "test.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o644)
    first = snapshot_local_task(source, tmp_path / "cache")
    manifest = {"task_checksum_version": TASK_CHECKSUM_VERSION,
                "tasks": [{"id": "task", "path": str(first), "sha256": checksum(first)}]}
    script.chmod(0o755)
    second = snapshot_local_task(source, tmp_path / "cache")
    assert first != second
    assert (first / "test.sh").stat().st_mode & 0o777 == 0o644
    assert (second / "test.sh").stat().st_mode & 0o777 == 0o755
    assert snapshot_local_task(source, tmp_path / "cache") == second
    verify_manifest(manifest)
    (first / "test.sh").chmod(0o755)
    with pytest.raises(ValueError, match="prepared task changed"):
        verify_manifest(manifest)


def test_task_checksum_includes_directories_and_rejects_special_entries(tmp_path):
    import os
    initial = checksum(tmp_path)
    directory = tmp_path / "empty"
    directory.mkdir(mode=0o755)
    with_directory = checksum(tmp_path)
    assert with_directory != initial
    directory.chmod(0o700)
    assert checksum(tmp_path) != with_directory
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(ValueError, match="unsupported entry"):
        checksum(tmp_path)


@pytest.mark.parametrize("version", [None, 1, 999])
def test_old_or_unknown_task_checksums_require_repreparation(version):
    manifest = {"tasks": []}
    if version is not None:
        manifest["task_checksum_version"] = version
    with pytest.raises(ValueError, match="prepare again"):
        verify_manifest(manifest)


def test_finished_phase_timestamp_does_not_hide_agent_crash():
    result = {'exception_info': {'exception_type': 'RuntimeError'},
              'environment_setup': {'started_at': '1', 'finished_at': '2'},
              'agent_setup': {'started_at': '2', 'finished_at': '3'},
              'agent_execution': {'started_at': '3', 'finished_at': '4'},
              'verifier': None}
    assert evaluation(result, 'reward', 1)['error_phase'] == 'agent_execution'


@pytest.mark.parametrize('exception_type', ['AgentTimeoutError', 'RuntimeError'])
def test_later_verification_does_not_relabel_agent_failure(exception_type):
    result = {'exception_info': {'exception_type': exception_type,
                                 'occurred_at': '2026-09-16T12:00:01+00:00'},
              'agent_execution': {'started_at': '2026-09-16T12:00:00+00:00'},
              'verifier': {'started_at': '2026-09-16T12:00:02+00:00'},
              'verifier_result': {'rewards': {'reward': 1}}}
    record = evaluation(result, 'reward', 1)
    assert record['error_phase'] == 'agent_execution'
    assert record['resolved'] is None


def test_failed_step_cannot_be_hidden_by_aggregate_reward(tmp_path):
    plan = {'benchmark_config': {'reward_metric': 'reward', 'success_value': 1},
            'harnesses': {'goose': {}}, 'run_id': 'test', 'benchmark': 'fixture',
            'model': {'name': 'local'}, 'proxy': 'praxis',
            'manifest': {'source': 'local', 'revision': None}}
    result = {'id': 'id-1', 'task_name': 'task', 'trial_name': 'trial',
              'config': {'agent': {'kwargs': {'harness': 'goose'}}},
              'verifier_result': {'rewards': {'reward': 1}},
              'step_results': [{'step_name': 'first',
                                'exception_info': {'exception_type': 'AgentTimeoutError'}}]}
    artifacts = tmp_path / '.harbor/trial/agent/acb'
    artifacts.mkdir(parents=True)
    (artifacts / 'measurement.json').write_text('{"complete": true}')
    import_results(plan, tmp_path, [result])
    report = json.loads((tmp_path / 'goose/report.json').read_text())
    record = report['evaluations'][0]
    assert record['status'] == 'error'
    assert record['failed_step'] == 'first'
    assert record['error_phase'] == 'agent_execution'
    assert record['resolved'] is None
    assert record['measurement_collection_complete'] is True
    assert record['measurement_complete'] is False
    assert report['reward_aggregates']['reward']['count'] == 0


@pytest.mark.parametrize('missing_second', [False, True])
def test_step_usage_follows_execution_order_and_missing_steps_are_incomplete(tmp_path, missing_second):
    plan={'benchmark_config':{'reward_metric':'reward','success_value':1},'harnesses':{'pi':{}},
          'run_id':'test','benchmark':'fixture','model':{'name':'local'},'proxy':'praxis',
          'manifest':{'source':'local','revision':None}}
    result={'id':'trial-id','task_name':'task','trial_name':'trial','config':{'agent':{'kwargs':{'harness':'pi'}}},
            'verifier_result':{'rewards':{'reward':1}},'step_results':[{'step_name':'z-first'},{'step_name':'a-second'}]}
    for step in ('z-first',) if missing_second else ('z-first','a-second'):
        folder=tmp_path/'.harbor/trial/steps'/step/'agent/acb';folder.mkdir(parents=True)
        (folder/'measurement.json').write_text('{"complete":true}')
        row={'run_id':'test','benchmark':'fixture','harness':'pi','model':'local','instance_id':'trial-id',
             'turn_index':0,'request_id':step,'input_tokens':10,'output_tokens':2}
        (folder/'usage.jsonl').write_text(json.dumps(row)+'\n')
    import_results(plan,tmp_path,[result])
    rows=[json.loads(line) for line in (tmp_path/'pi/usage.jsonl').read_text().splitlines()]
    assert [row['step_name'] for row in rows]==(['z-first'] if missing_second else ['z-first','a-second'])
    assert [row['turn_index'] for row in rows]==list(range(len(rows)))
    assert all(row['step_turn_index']==0 for row in rows)
    record=json.loads((tmp_path/'pi/report.json').read_text())['evaluations'][0]
    assert record['measurement_complete'] is (not missing_second)
