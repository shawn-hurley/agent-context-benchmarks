"""Worker exit codes describe infrastructure, independently of task grades."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from acb.harbor import worker
from acb.harbor.dataset import TASK_CHECKSUM_VERSION, checksum


@pytest.fixture
def execution(tmp_path, monkeypatch):
    task = tmp_path / "task"
    task.mkdir()
    plan = {
        "protocol_version": 2,
        "run_id": "test", "benchmark": "harbor", "proxy": "praxis",
        "benchmark_config": {"reward_metric": "reward", "success_value": 1},
        "harnesses": {"pi": {}}, "model": {"name": "model"}, "attempts": 1,
        "manifest": {"source": "local", "revision": None, "task_checksum_version": TASK_CHECKSUM_VERSION,
                     "tasks": [{"id": "task", "path": str(task), "sha256": checksum(task)}]},
    }
    result = {
        "id": str(uuid4()), "task_name": "task", "trial_name": "trial",
        "trial_uri": "file:///trial", "task_id": {"path": str(task)}, "task_checksum": checksum(task),
        "agent_info": {"name": "acb-pi", "version": "1"},
        "config": {"task": {"path": str(task)}, "agent": {"kwargs": {"harness": "pi"}}},
        "verifier_result": {"rewards": {"reward": 1}},
    }
    output = tmp_path / "run"
    folder = output / ".harbor" / "trial"
    folder.mkdir(parents=True)
    job = Mock()
    job.run = AsyncMock(return_value=SimpleNamespace(stats=SimpleNamespace(model_dump_json=lambda **kw: '{}')))
    monkeypatch.setattr("harbor.job.Job.create", AsyncMock(return_value=job))
    monkeypatch.setattr(worker, "job_config", lambda *a, **kw: None)
    monkeypatch.setattr("acb.harbor.metrics.bind_container_metrics", lambda *a: None)
    return plan, result, output, job


def save_result(output, result):
    (output / ".harbor" / "trial" / "result.json").write_text(json.dumps(result))


def exception(kind):
    return {"exception_type": kind, "exception_message": "fixture error",
            "exception_traceback": "traceback", "occurred_at": "2026-09-18T12:00:00Z"}


@pytest.mark.parametrize("kind", ["failed_grade", "missing_grade", "timeout", "verifier_error"])
def test_grading_and_agent_failures_do_not_fail_execution(execution, kind):
    plan, result, output, job = execution
    if kind == "failed_grade":
        result["verifier_result"]["rewards"]["reward"] = 0
    elif kind == "missing_grade":
        result["verifier_result"]["rewards"] = {"other": 1}
    else:
        result["exception_info"] = exception("AgentTimeoutError" if kind == "timeout" else "VerifierTimeoutError")
    save_result(output, result)
    asyncio.run(worker.execute(plan, output))
    summary = json.loads((output / "execution-status.json").read_text())
    assert summary["status"] == "completed"
    record = json.loads((output / "pi/report.json").read_text())["evaluations"][0]
    assert record["resolved"] is (False if kind == "failed_grade" else None)
    assert record["status"] == ("completed" if kind == "failed_grade" else "error")


@pytest.mark.parametrize("kind", ["EnvironmentStartTimeoutError", "HarnessStartupError", "PraxisStartupError"])
def test_infrastructure_failures_fail_execution(execution, kind):
    plan, result, output, job = execution
    result["exception_info"] = exception(kind)
    save_result(output, result)
    with pytest.raises(RuntimeError, match="Harbor run failed"):
        asyncio.run(worker.execute(plan, output))
    assert json.loads((output / "execution-status.json").read_text())["status"] == "infrastructure_error"
    assert (output / "pi/report.json").is_file()


@pytest.mark.parametrize("control,grade", [("oracle", 0), ("nop", 1)])
def test_control_outcome_and_missing_slots_are_reported_without_failure(execution, control, grade):
    plan, result, output, job = execution
    plan["attempts"] = 2
    result["config"]["agent"] = {"name": control}
    result["verifier_result"]["rewards"]["reward"] = grade
    save_result(output, result)
    asyncio.run(worker.execute(plan, output, control=control))
    summary = json.loads((output / "execution-status.json").read_text())
    assert summary["status"] == "completed"
    assert summary["verification_failures"] == summary["missing_trials"] == 1
    report = json.loads((output / control / "report.json").read_text())
    assert report["missing_trials"] == 1
    assert report["evaluations"][0]["resolved"] is False


def test_corrupt_results_preserve_healthy_trials_and_missing_slots(execution):
    plan, result, output, job = execution
    plan["attempts"] = 3
    save_result(output, result)
    for name, content in [("broken", "{"), ("invalid", '{"id":"invalid"}')]:
        folder = output / ".harbor" / name
        folder.mkdir()
        (folder / "result.json").write_text(content)
    asyncio.run(worker.execute(plan, output))
    report = json.loads((output / "pi/report.json").read_text())
    assert report["instances"] == 3
    assert report["resolved"] == 1
    assert report["missing_trials"] == 2
    errors = json.loads((output / "import-errors.json").read_text())
    assert len(errors) == 2
    assert {item["error_type"] for item in errors} == {"JSONDecodeError", "ValidationError"}
    assert (output / ".harbor/broken/result.json").read_text() == "{"


def test_report_import_failure_preserves_original_worker_error(execution, monkeypatch):
    plan, result, output, job = execution
    save_result(output, result)
    job.run.side_effect = RuntimeError("orchestrator failed")
    monkeypatch.setattr(worker, "import_results", Mock(side_effect=ValueError("artifact unreadable")))
    with pytest.raises(RuntimeError, match="orchestrator failed"):
        asyncio.run(worker.execute(plan, output))
    assert json.loads((output / "import-errors.json").read_text())[0]["error"] == "artifact unreadable"


def test_report_import_failure_alone_is_nonfatal(execution, monkeypatch):
    plan, result, output, job = execution
    save_result(output, result)
    monkeypatch.setattr(worker, "import_results", Mock(side_effect=ValueError("artifact unreadable")))
    asyncio.run(worker.execute(plan, output))
    assert json.loads((output / "execution-status.json").read_text())["import_errors"] == 1


def test_missing_preparation_results_remain_infrastructure_failure(execution):
    plan, result, output, job = execution
    with pytest.raises(RuntimeError, match="preparation probes failed"):
        asyncio.run(worker.execute(plan, output, install_only=True))
    assert json.loads((output / "execution-status.json").read_text())["status"] == "infrastructure_error"


def test_missing_primary_metric_without_success_threshold_is_still_unavailable():
    from acb.harbor.results import evaluation
    record = evaluation({"verifier_result": {"rewards": {"other": .8}}}, "score")
    assert record["status"] == "error"
    assert record["verification_error"]
    valid = evaluation({"verifier_result": {"rewards": {"score": .8}}}, "score")
    assert valid["status"] == "completed" and valid["resolved"] is None
