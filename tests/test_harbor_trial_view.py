import json

from acb.harbor.results import import_results
from acb.harbor.trial_view import archive_job_log, expose_trial


def test_trial_logs_can_be_followed_before_import_and_remain_readable_after(tmp_path):
    native = tmp_path / ".harbor" / "task-1__attempt"
    agent = native / "agent" / "acb"
    agent.mkdir(parents=True)
    (native / "verifier").mkdir()
    (native / "verifier" / "test-stdout.txt").write_text("verifier output")
    (native / "trial.log").write_text("trial started\n")
    (agent / "transcript.jsonl").write_text('{"type":"start"}\n')
    (agent / "praxis.log").write_text("model connected\n")
    (agent / "measurement.json").write_text('{"complete":true}')
    (tmp_path / "job.log").write_text("job running\n")

    view = expose_trial(tmp_path, "pi", "id-1", native.name)
    assert (tmp_path / "pi" / "instances" / "id-1").is_symlink()
    assert (view / "job.log").read_text() == "job running\n"
    assert (view / "trial.log").read_text() == "trial started\n"
    assert (view / "transcript.log").read_text() == '{"type":"start"}\n'
    assert (view / "praxis.log").read_text() == "model connected\n"
    with (agent / "transcript.jsonl").open("a") as stream:
        stream.write('{"type":"turn_end"}\n')
    assert "turn_end" in (view / "transcript.log").read_text()

    plan = {"benchmark_config": {"reward_metric": "reward", "success_value": 1},
            "harnesses": {"pi": {}}, "run_id": "test", "benchmark": "harbor",
            "model": {"name": "model"}, "proxy": "praxis",
            "manifest": {"source": "local", "revision": None,
                         "tasks": [{"id": "task-1", "sha256": "hash"}]}}
    result = {"id": "id-1", "task_name": "task-1", "trial_name": native.name,
              "config": {"agent": {"kwargs": {"harness": "pi"}}},
              "verifier_result": {"rewards": {"reward": 1}}}
    import_results(plan, tmp_path, [result])
    assert (view / "trial.log").read_text() == "trial started\n"
    assert not (view / "trial.log").is_symlink()
    assert not (view / "transcript.jsonl").is_symlink()
    assert (view / "verifier" / "test-stdout.txt").read_text() == "verifier output"
    assert (view / "evaluation.json").is_file()
    assert (tmp_path / "pi" / "instances" / "id-1" / "transcript.jsonl").samefile(view / "transcript.jsonl")
    (tmp_path / "job.log").write_text("job completed\n")
    archive_job_log(tmp_path, ["pi"])
    assert (view / "job.log").read_text() == "job completed\n"
    assert not (view / "job.log").is_symlink()
