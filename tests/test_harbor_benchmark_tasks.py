import asyncio
import json
from pathlib import Path
import shutil

import pytest

from acb.harbor.dataset import prepare_dataset, verify_manifest
from acb.harbor.benchmark_verifier import BenchmarkVerifier, validate_candidate_tree


def scarf_plan(tmp_path):
    bundle = tmp_path / "bundle"
    app = bundle / "business_domain/cart"
    for framework in ("jakarta", "quarkus"):
        directory = app / framework
        directory.mkdir(parents=True)
        (directory / "Main.java").write_text(framework)
        (directory / "test.sh").write_text("exit 0\n")
        (directory / "Dockerfile").write_text("FROM fixture\n")
        (directory / "nested").mkdir()
        (directory / "nested/Makefile").write_text("source-owned nested build file")
    return {"benchmark": "scarfbench", "benchmark_config": {
        "benchmark_cache_dir": str(bundle), "source": "jakarta", "target": "quarkus"},
        "cache_dir": str(tmp_path / "cache"), "offline": True, "subset": None, "limit": None}


def test_scarfbench_snapshot_separates_agent_and_grading_inputs(tmp_path):
    plan = scarf_plan(tmp_path)
    manifest = prepare_dataset(plan)
    task = manifest["tasks"][0]
    root = Path(task["path"])
    assert task["metadata"]["instance_id"] == "business_domain/cart/jakarta-to-quarkus"
    assert (root / "environment/source/Main.java").read_text() == "jakarta"
    assert not (root / "environment/source/Dockerfile").exists()
    assert (root / "environment/source/test.sh").exists()
    assert (root / "environment/source/nested/Makefile").exists()
    target = "business_domain/cart/quarkus"
    assert (root / "tests/benchmark" / target / "Main.java").read_text() == "quarkus"
    assert (root / "tests/benchmark" / target / "Makefile").exists()
    assert not (Path(plan["benchmark_config"]["benchmark_cache_dir"]) / target / "Makefile").exists()
    assert task["oracle"]
    (Path(plan["benchmark_config"]["benchmark_cache_dir"]) / target / "Main.java").write_text("changed")
    verify_manifest(manifest)
    assert prepare_dataset(plan)["tasks"][0]["sha256"] != task["sha256"]


def test_unknown_subset_fails_before_running(tmp_path):
    plan = scarf_plan(tmp_path)
    plan["subset"] = ["missing"]
    with pytest.raises(ValueError):
        prepare_dataset(plan)


def test_candidate_links_cannot_redirect_grader_writes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "Makefile").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escapes"):
        validate_candidate_tree(project)


@pytest.mark.parametrize("case,recipe,passed", [
    ("pass", "@echo BUILD SUCCESS\n\t@echo 'Application started and ready.'\n\t@echo '===== 1 passed ====='", True),
    ("build-failure", "@false", False),
    ("startup-failure", "@echo BUILD SUCCESS\n\t@false", False),
    ("smoke-failure", "@echo BUILD SUCCESS\n\t@echo 'Application started and ready.'\n\t@false", False),
    ("false-success", "@echo BUILD SUCCESS\n\t@echo 'Application started and ready.'\n\t@echo '===== 1 passed ====='\n\t@false", False),
])
def test_real_scarf_validator_preserves_grade_independent_of_exit(tmp_path, case, recipe, passed):
    if not shutil.which("scarf"):
        pytest.skip("native ScarfBench CLI is not installed")
    plan = scarf_plan(tmp_path)
    target = Path(plan["benchmark_config"]["benchmark_cache_dir"]) / "business_domain/cart/quarkus"
    (target / "metadata.json").write_text('{"num_smoke_tests": 1}')
    # A fixed validator fixture exercises scarf's metadata parser and ACB's
    # make-failure guard, without building Java dependencies or using a model.
    (target / "Makefile").write_text("test:\n\t" + recipe + "\n")
    manifest = prepare_dataset(plan)
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    task = Task(Path(manifest["tasks"][0]["path"]))
    trial = TrialPaths(tmp_path / "trial")
    trial.verifier_dir.mkdir(parents=True)

    class Environment:
        async def download_dir(self, source, destination):
            shutil.copytree(task.paths.environment_dir / "source", destination)

    verifier = BenchmarkVerifier(task=task, trial_paths=trial, environment=Environment(),
                                 benchmark_config=plan["benchmark_config"])
    result = asyncio.run(verifier.verify())
    assert result.rewards == {"reward": int(passed)}
    assert (trial.verifier_dir / "native/grader.log").exists()
    assert json.loads(trial.reward_json_path.read_text()) == result.rewards
    # Feed the same fixed candidate to the retained adapter directly. Compare
    # detailed native evidence as well as the binary reward.
    from acb.benchmarks.base import Prediction
    from acb.benchmarks.scarfbench import ScarfBench, _write_metadata_json
    baseline = tmp_path / "baseline"
    bench = ScarfBench(plan["benchmark_config"])
    iid = "business_domain/cart/jakarta-to-quarkus"
    run = bench._run_dir_for_instance(baseline, iid)
    run.mkdir(parents=True)
    shutil.copytree(task.paths.environment_dir / "source", run / "output")
    shutil.copytree(task.paths.environment_dir / "source", run / "input")
    (run / "validation").mkdir()
    (run / "validation/agent.out").touch()
    (run / "validation/agent.err").touch()
    _write_metadata_json(run / "metadata.json", agent="acb", app="cart", layer="business_domain",
                         source_framework="jakarta", target_framework="quarkus", model="harbor")
    direct = bench.evaluate([Prediction(iid, "harbor", output=str(run))], "parity", baseline)
    assert direct[iid] == passed
    native = bench._run_dir_for_instance(trial.verifier_dir / "native", iid)
    before, after = (json.loads((path / "metadata.json").read_text()) for path in (run, native))
    for key in ("compile_ok", "deploy_ok", "tests_passed", "num_smoke_tests"):
        assert before.get(key) == after.get(key), (case, key)


def test_swebench_export_preserves_row_and_runtime(tmp_path):
    row = {"instance_id": "org__repo-1", "repo": "org/repo", "base_commit": "abc",
           "problem_statement": "Fix it", "image": "original", "version": "1",
           "FAIL_TO_PASS": ["test_a"], "PASS_TO_PASS": [], "log_parser": "parse_log_pytest",
           "eval_type": "pass_and_fail", "eval_script": "echo hidden", "patch": "gold"}
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps([row]))
    source = tmp_path / "tasks/org__repo-1"
    source.mkdir(parents=True)
    (source / "Dockerfile").write_text("FROM fixture\n# /opt/miniconda3\n")
    plan = {"benchmark": "swebench", "benchmark_config": {"dataset": str(dataset),
        "task_repo_cache_dir": str(tmp_path)}, "cache_dir": str(tmp_path / "cache"),
        "offline": True, "subset": None, "limit": None}
    task = prepare_dataset(plan)["tasks"][0]
    root = Path(task["path"])
    assert task["language_profile"]["conda_env"] == "testbed"
    assert json.loads((root / "tests/benchmark.json").read_text())["extra"]["dataset_row"] == row
    assert "hidden" not in (root / "instruction.md").read_text()
    assert "baseline-untracked" in (root / "environment/Dockerfile").read_text()
    assert task["oracle"]
    plan["benchmark_config"]["exclude_repos"] = ["org/repo"]
    with pytest.raises(ValueError, match="empty"):
        prepare_dataset(plan)


def test_grader_dependencies_fail_before_inference(tmp_path, monkeypatch):
    from acb.harbor.benchmark_grader import prepare_grader, verify_grader
    plan = scarf_plan(tmp_path)
    monkeypatch.setattr("acb.harbor.benchmark_grader.shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="ScarfBench grading requires"):
        prepare_grader(plan)
    monkeypatch.setattr("acb.harbor.benchmark_grader.prepare_grader", lambda _: {"sha256": "new"})
    plan["benchmark_grader"] = {"sha256": "old"}
    with pytest.raises(ValueError, match="changed"):
        verify_grader(plan)


def test_binary_test_assets_are_frozen_and_required_offline(tmp_path):
    from acb.harbor.benchmark_tasks import freeze_swebench_assets
    source = tmp_path / "repo/tasks/org__repo-1/test_assets/tests/expected.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original")
    row = {"image_assets": {"test_patch": [{"path": "tests/expected.png", "url": "https://example.invalid/asset"}]}}
    frozen = tmp_path / "frozen"
    freeze_swebench_assets(frozen, row, "org__repo-1", {"task_repo_cache_dir": str(tmp_path / "repo")}, offline=True)
    source.write_bytes(b"changed")
    assert (frozen / "tests/task_repo/tasks/org__repo-1/test_assets/tests/expected.png").read_bytes() == b"original"
    with pytest.raises(FileNotFoundError, match="missing"):
        freeze_swebench_assets(tmp_path / "missing", row, "org__repo-1", {}, offline=True)


def test_prepared_swebench_images_must_be_visible_to_native_sdk(monkeypatch):
    from types import SimpleNamespace
    from acb.harbor.benchmark_grader import verify_grading_images
    monkeypatch.setattr("acb.container.container_env", lambda _: {})
    monkeypatch.setattr("acb.harbor.benchmark_grader.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=1))
    plan = {"benchmark": "swebench", "benchmark_config": {}, "environment": "podman",
            "benchmark_grader": {"binary": "python"}}
    with pytest.raises(RuntimeError, match="prepared images"):
        verify_grading_images(plan, {"sha256:" + "a" * 64})


def test_worker_uses_native_verifier_for_benchmarks_only(tmp_path):
    from acb.harbor.worker import job_config
    plan = scarf_plan(tmp_path)
    plan.update(protocol_version=1, environment="podman", attempts=1, max_workers=1,
                model={"name": "fixture"}, harnesses={"goose": {"timeout": 30}},
                benchmark_grader={"binary": "/prepared/scarf"})
    plan["manifest"] = prepare_dataset(plan)
    config = job_config(plan, tmp_path, control="oracle")
    assert config.verifier.import_path == "acb.harbor.benchmark_verifier:BenchmarkVerifier"
    assert config.verifier.kwargs["benchmark_config"]["scarf_binary"] == "/prepared/scarf"
    assert config.verifier.kwargs["benchmark_config"]["container_backend"] == "podman"
    plan["benchmark_config"]["path"] = "/already-exported-native-harbor-tasks"
    assert job_config(plan, tmp_path, control="oracle").verifier.import_path is None
