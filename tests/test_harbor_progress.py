from acb.harbor.progress import HarborProgress
from acb.ui import InstanceStatus


def plan(attempts=1):
    return {
        'run_id': 'run', 'benchmark': 'fixture', 'max_workers': 2, 'attempts': attempts,
        'model': {'name': 'model'}, 'harnesses': {'pi': {}, 'goose': {}},
        'manifest': {'tasks': [{'id': 'task-1'}]},
    }


def event(kind, harness='pi', trial_id='trial-1', **values):
    return {'event': kind, 'harness': harness, 'task_id': 'task-1',
            'trial_id': trial_id, 'trial_name': 'task-1__attempt', **values}


def test_harbor_lifecycle_uses_existing_progress_states(tmp_path):
    progress = HarborProgress(plan(), tmp_path)
    key = 'pi-task-1'
    progress.update(event('trial-started'))
    assert progress.tracker.instances[key].status == InstanceStatus.RUNNING
    progress.update(event('agent-started'))
    assert progress.tracker.instances[key].last_activity == 'agent running'
    progress.update(event('verification-started'))
    assert progress.tracker.instances[key].status == InstanceStatus.VERIFYING
    progress.update(event('trial-ended', resolved=True))
    assert progress.tracker.instances[key].status == InstanceStatus.VERIFIED_PASS


def test_harbor_progress_preserves_failure_and_attempt_identity(tmp_path):
    progress = HarborProgress(plan(attempts=2), tmp_path)
    progress.update(event('trial-started'))
    progress.update(event('trial-ended', error='AgentTimeoutError: timed out'))
    first = progress.tracker.instances['pi-task-1 (attempt 1)']
    second = progress.tracker.instances['pi-task-1 (attempt 2)']
    assert first.status == InstanceStatus.FAILED
    assert first.error_message == 'AgentTimeoutError: timed out'
    assert second.status == InstanceStatus.QUEUED


def test_missing_grade_is_visible_as_error_instead_of_valid_failed_verification(tmp_path):
    progress = HarborProgress(plan(), tmp_path)
    progress.update(event('trial-started'))
    progress.update(event('trial-ended', status='error', resolved=None))
    record = progress.tracker.instances['pi-task-1']
    assert record.status == InstanceStatus.FAILED
    assert 'usable grade' in record.error_message


def test_control_summary_labels_expected_outcome_and_unknown_usage(tmp_path):
    progress = HarborProgress(plan(), tmp_path, control='nop')
    progress.update(event('trial-started', harness='nop'))
    progress.update(event('trial-ended', harness='nop', resolved=True))
    summary = progress.tracker.summary()
    assert 'Control outcome (nop; expected grade 0)' in summary
    assert 'Passed: 1' in summary
    assert 'Average tokens per instance: unavailable' in summary
    progress.tracker.instances['nop-task-1'].tokens_used = 0
    assert 'Average tokens per instance: 0 (coverage 1/1)' in progress.tracker.summary()
