import json

from acb.harbor.job_log import write_job_log


def test_job_log_identifies_failed_task_and_evidence(tmp_path):
    plan = {"run_id": "sample", "benchmark": "harbor", "harnesses": {"pi": {}},
            "model": {"name": "model"}}
    native = tmp_path / ".harbor" / "task-1__attempt"
    native.mkdir(parents=True)
    (native / "exception.txt").write_text("traceback")
    (native / "verifier").mkdir()
    (native / "verifier" / "test-stdout.txt").write_text("tests failed")
    write_job_log(plan, tmp_path)
    assert "Status: running" in (tmp_path / "job.log").read_text()
    (tmp_path / "report.json").write_text(json.dumps({"harnesses": {"pi": {
        "evaluations": [{"task_id": "task-1", "trial_name": "task-1__attempt",
                         "status": "error", "error_phase": "agent_execution",
                         "measurement_complete": False, "rewards": {"reward": 0.0},
                         "exception": {"exception_type": "AgentTimeoutError",
                                       "exception_message": "timed out after 600 seconds"}}]}}}))
    write_job_log(plan, tmp_path, finished=True)
    text = (tmp_path / "job.log").read_text()
    assert "Status: failed" in text
    assert "task-1 | pi | task-1__attempt" in text
    assert "AgentTimeoutError: timed out after 600 seconds" in text
    assert str(native / "exception.txt") in text
    assert str(native / "verifier" / "test-stdout.txt") in text


def test_job_log_never_reports_success_without_results(tmp_path):
    plan = {"run_id": "sample", "benchmark": "harbor", "harnesses": {"pi": {}},
            "model": {"name": "model"}}
    write_job_log(plan, tmp_path, finished=True)
    assert "Status: incomplete" in (tmp_path / "job.log").read_text()
    write_job_log(plan, tmp_path, error=RuntimeError("worker failed"), finished=True)
    assert "Status: failed" in (tmp_path / "job.log").read_text()
    assert "worker failed" in (tmp_path / "job.log").read_text()
