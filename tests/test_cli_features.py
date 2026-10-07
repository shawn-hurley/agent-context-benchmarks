"""User journeys for save-only setup, summaries, discovery and starter templates."""
import curses
import json
from pathlib import Path
import sys

import pytest
import yaml

from acb.cli import main
from acb.config import RunConfig
from acb.interactive_run import RunDraft, SavedConfiguration, _Screen, finish
from acb.resolver import resolve
from acb.task_discovery import discover_tasks
from test_cli_usability import MenuWindow
from test_comparison import run


@pytest.fixture
def starter(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init", "experiment", "--template", "quickstart", "--environment", "docker"])
    capsys.readouterr()
    return tmp_path / "experiment/run.yaml"


def test_packaged_quickstart_resolves_discovers_and_exports_without_execution(starter, monkeypatch, capsys):
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("discovery/export launched a process"))
    main(["tasks", "--config", str(starter), "--json"])
    listing = json.loads(capsys.readouterr().out)
    assert listing["complete"] and listing["tasks"] == [{"id": "smoke", "selected": True}]
    plan = resolve(RunConfig.from_file(starter)).to_dict()
    assert plan["environment"] == "docker" and plan["max_workers"] == 1
    assert plan["harnesses"]["goose"]["timeout"] == 300
    assert RunDraft.create(str(starter.parent / "config")).config.benchmark == "smoke"
    from acb.harbor.dataset import prepare_dataset, verify_manifest
    manifest = prepare_dataset(plan)
    verify_manifest(manifest)
    assert manifest["tasks"][0]["oracle"]
    original = (starter.parent / "tasks/smoke/tests/test.sh").read_bytes()
    main(["init", str(starter.parent), "--template", "quickstart", "--environment", "podman"])
    assert (starter.parent / "tasks/smoke/tests/test.sh").read_bytes() == original
    assert resolve(RunConfig.from_file(starter)).to_dict()["environment"] == "docker"


def test_save_only_never_dispatches_execution(starter, monkeypatch, capsys):
    draft = RunDraft.load(starter)
    draft.set("run_id", "saved-only")
    result = finish(draft, "save_only", starter)
    assert isinstance(result, SavedConfiguration)
    assert yaml.safe_load(starter.read_text())["run_id"] == "saved-only"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("acb.interactive_run.configure", lambda *a: result)
    monkeypatch.setattr("acb.harbor.backend.run_plan", lambda *a, **kw: pytest.fail("save launched a run"))
    main(["run"])
    output = capsys.readouterr().out
    assert "Saved configuration:" in output and "acb run --config" in output
    assert not Path("runs").exists()


def test_editor_reviews_actual_destination_and_keeps_save_errors_visible(starter, monkeypatch):
    draft = RunDraft.load(starter)
    screen = _Screen(None, None)
    actions = iter(["Load YAML file", "Review", "Save configuration", "Save configuration", "Cancel"])
    reviews = []

    def menu(title, options, **kwargs):
        action = next(actions)
        assert action in options
        if title.startswith("Review:"):
            reviews.append((kwargs["details"], screen.error))
        return action

    prompts = iter([str(starter), str(starter.parent / "existing.yaml")])
    occupied = starter.parent / "existing.yaml"
    occupied.write_text("preserve me")
    monkeypatch.setattr(screen, "menu", menu)
    monkeypatch.setattr(screen, "prompt", lambda *a, **kw: next(prompts))
    assert screen.run() is None
    details = "\n".join(reviews[1][0])
    assert str(occupied) in details and "Environment: docker" in details
    assert "Provider endpoint: https://api.openai.com:443" in details
    assert "timeout 300s" in details and "Output directory:" in details
    assert "already exists" in reviews[2][1]
    assert occupied.read_text() == "preserve me"


def test_review_details_can_scroll_to_final_effective_values():
    details = [f"Effective setting {index}" for index in range(40)]
    window = MenuWindow([curses.KEY_NPAGE] * 8 + [curses.KEY_PPAGE, 10], 10)
    screen = _Screen(window, None)
    assert screen.menu("Review", ["Save configuration", "Cancel"], details=details, scroll_details=True) == "Save configuration"
    # In a ten-row terminal, each PageDown exposes another detail line.
    assert "Effective setting 8" in window.frames[8].values()
    assert "Effective setting 7" in window.frames[9].values()
    assert all(any("Enter select" in line for line in frame.values()) for frame in window.frames)


def test_terminal_summaries_preserve_piped_json_and_explicit_formats(starter, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("OPENAI_API_KEY", "DO-NOT-SHOW-KEY")
    main(["resolve", "--config", str(starter)])
    summary = capsys.readouterr().out
    assert "Provider model ID: YOUR_MODEL_ID" in summary and "Environment: docker" in summary
    assert "OPENAI_API_KEY" in summary and "DO-NOT-SHOW-KEY" not in summary
    main(["resolve", "--config", str(starter), "--json"])
    assert json.loads(capsys.readouterr().out)["environment"] == "docker"
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    main(["resolve", "--config", str(starter)])
    assert json.loads(capsys.readouterr().out)["max_workers"] == 1
    main(["resolve", "--config", str(starter), "--text"])
    assert "Concurrent trials (global): 1" in capsys.readouterr().out


def test_prepare_json_keeps_progress_on_stderr(starter, monkeypatch, capsys):
    def prepare(plan):
        print("Preparing fixture assets")
        return plan.to_dict()
    monkeypatch.setattr("acb.preparation.prepare", prepare)
    main(["prepare", "--config", str(starter), "--json"])
    output = capsys.readouterr()
    assert json.loads(output.out)["benchmark"] == "smoke"
    assert "Preparing fixture assets" in output.err


def test_result_summaries_distinguish_unknown_from_zero_and_exports_keep_json(tmp_path, capsys):
    baseline = run(tmp_path / "baseline", {"task": 0}, tokens=100)
    candidate = run(tmp_path / "candidate", {"task": 1}, tokens=50, complete=False)
    main(["report", str(candidate), "--text"])
    summary = capsys.readouterr().out
    assert "task | 1 | incomplete | unavailable" in summary
    main(["compare", str(baseline), str(candidate), "--text"])
    summary = capsys.readouterr().out
    assert "Grade change: better" in summary
    assert "Comparable measurements: 0/1" in summary and "Token change unavailable" in summary
    main(["report", str(baseline), "--json", "--html", "--bundle", str(tmp_path / "report.zip")])
    output = capsys.readouterr()
    assert json.loads(output.out)["harness"] == "pi"
    assert "html report:" in output.err and "report bundle:" in output.err


def discovery_plan(tmp_path, **config):
    return {"benchmark": "rh-swe-bench", "benchmark_config": {
        "dataset": "rounakbende/rh-swe-bench", "revision": "a" * 40, **config},
        "cache_dir": str(tmp_path / "cache"), "offline": False, "subset": ["b"], "limit": 1}


def test_discovery_requires_explicit_download_and_reuses_full_listing(tmp_path, monkeypatch):
    plan = discovery_plan(tmp_path)
    monkeypatch.setattr("acb.task_discovery._remote_tasks", lambda *a: pytest.fail("network used without --download"))
    listing = discover_tasks(plan)
    assert not listing["complete"] and not listing["tasks"]
    monkeypatch.setattr("acb.task_discovery._remote_tasks", lambda *a: [{"id": "b"}, {"id": "a"}])
    listing = discover_tasks(plan, allow_download=True)
    assert listing["complete"]
    assert listing["tasks"] == [{"id": "a", "selected": False}, {"id": "b", "selected": True}]
    monkeypatch.setattr("acb.task_discovery._remote_tasks", lambda *a: pytest.fail("cached discovery used network"))
    assert discover_tasks(plan)["tasks"] == listing["tasks"]
    plan["offline"] = True
    with pytest.raises(ValueError, match="conflicts"):
        discover_tasks(plan, allow_download=True)


def test_discovery_marks_cached_subset_as_partial(tmp_path):
    plan = discovery_plan(tmp_path)
    task = Path(plan["cache_dir"]) / "datasets/rh-swe-bench" / ("a" * 40) / "subset/tasks/b"
    task.mkdir(parents=True)
    (task / "task.toml").touch()
    listing = discover_tasks(plan)
    assert listing["tasks"] == [{"id": "b", "selected": True}]
    assert listing["complete"] is False
    plan["subset"] = None
    assert discover_tasks(plan)["tasks"][0]["selected"] is None


def test_swebench_discovery_honors_exclusions_without_fetching_or_exporting(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps([{"instance_id": name, "repo": repo, "base_commit": "abc", "problem_statement": "Fix"}
                                   for name, repo in [("b", "keep/repo"), ("a", "keep/repo"), ("c", "drop/repo")]]))
    plan = discovery_plan(tmp_path, dataset=str(dataset), exclude_repos=["drop/repo"])
    plan["benchmark"] = "swebench"
    plan["subset"] = None
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("container/export started"))
    listing = discover_tasks(plan)
    assert [item["id"] for item in listing["tasks"]] == ["b", "a"]
    assert [item["id"] for item in listing["tasks"] if item["selected"]] == ["b"]


def test_task_picker_applies_ids_and_clears_to_all(starter, monkeypatch):
    draft = RunDraft.load(starter)
    screen = _Screen(None, None)
    choices = iter(["Browse local/cached tasks", [], "Browse local/cached tasks", ["smoke"]])
    monkeypatch.setattr(screen, "menu", lambda *a, **kw: next(choices))
    screen.edit(draft, "subset")
    assert draft.config.subset is None
    screen.edit(draft, "subset")
    assert draft.config.subset == ["smoke"]


def test_local_task_listing_preserves_executor_selection_with_native_only_exclusions(starter):
    plan = resolve(RunConfig.from_file(starter)).to_dict()
    plan["benchmark_config"]["exclude"] = ["smoke"]
    # Local Harbor selection uses dataset.select(); exclude/exclude_repos are
    # native adapter fields, so discovery must not hide a task that will run.
    assert discover_tasks(plan)["tasks"] == [{"id": "smoke", "selected": True}]


def test_cached_discovery_does_not_reuse_different_dataset_revision(tmp_path, monkeypatch):
    plan = discovery_plan(tmp_path)
    monkeypatch.setattr("acb.task_discovery._remote_tasks", lambda *a: [{"id": "old-task"}])
    discover_tasks(plan, allow_download=True)
    plan["benchmark_config"]["revision"] = "b" * 40
    monkeypatch.setattr("acb.task_discovery._remote_tasks", lambda *a: pytest.fail("default discovery contacted network"))
    listing = discover_tasks(plan)
    assert not listing["complete"] and not listing["tasks"]


def test_scarfbench_discovery_uses_canonical_subset_ids_and_conversion_order(tmp_path):
    for app in ("cart", "encoder"):
        for framework in ("jakarta", "quarkus"):
            (tmp_path / "apps/business_domain" / app / framework).mkdir(parents=True)
    plan = discovery_plan(tmp_path, dataset=None, benchmark_cache_dir=str(tmp_path / "apps"), source="jakarta", target="quarkus")
    plan["benchmark"], plan["subset"] = "scarfbench", None
    listing = discover_tasks(plan)
    assert [task["id"] for task in listing["tasks"]] == [
        "business_domain/cart/jakarta-to-quarkus", "business_domain/encoder/jakarta-to-quarkus"]
    assert [task["id"] for task in listing["tasks"] if task["selected"]] == ["business_domain/cart/jakarta-to-quarkus"]


def test_packaged_setup_task_matches_documented_quickstart_fixture():
    from acb.starters import starter_files
    files = starter_files("quickstart", "podman")
    source = Path(__file__).resolve().parents[1] / "config.example/quickstart"
    for name, contents in files.items():
        if name.startswith("tasks/"):
            assert (source / name).read_bytes() == contents, name


@pytest.mark.parametrize("height", [6, 7, 8, 9])
def test_short_review_keeps_the_selected_action_visible(height):
    window = MenuWindow([ord("j"), 10], height)
    screen = _Screen(window, None)
    assert screen.menu("Review", ["Save configuration", "Cancel"],
                       details=["Effective setting"] * 30, scroll_details=True) == "Cancel"
    assert "> Save configuration" in window.frames[0].values()
    assert "> Cancel" in window.frames[1].values()


def test_new_draft_expands_home_registry_path(starter):
    import os
    registry = "~/" + os.path.relpath(starter.parent / "config", Path.home())
    draft = RunDraft.create(registry)
    assert draft.config.model == "quickstart-model"
    assert draft.config.benchmark == "smoke"
    assert resolve(draft.config).to_dict()["environment"] == "docker"


def test_correcting_model_clears_old_editor_error(starter, monkeypatch):
    screen = _Screen(None, None)
    screen.error = "No models configured"
    monkeypatch.setattr(screen, "menu", lambda *a, **kw: "quickstart-model")
    screen.edit(RunDraft.load(starter), "model")
    assert screen.error == ""


def test_loading_valid_yaml_clears_old_load_error(starter, monkeypatch):
    screen = _Screen(None, None)
    choices = iter(["Load YAML file", "Load YAML file", "Cancel"])
    prompts = iter([str(starter.parent / "missing.yaml"), str(starter)])
    def menu(title, options, **kwargs):
        if title == "Configure run":
            assert screen.error == ""
        return next(choices)
    monkeypatch.setattr(screen, "menu", menu)
    monkeypatch.setattr(screen, "prompt", lambda *a, **kw: next(prompts))
    assert screen.run() is None


def test_report_text_explains_failed_trial_and_points_to_evidence(tmp_path, capsys):
    failed = run(tmp_path / "failed", {"task": None}, complete=False)
    path = failed / "report.json"
    report = json.loads(path.read_text())
    report["evaluations"][0].update(error_phase="environment_setup", exception={
        "exception_message": "Compose build failed:\nunknown flag: --project-name"})
    path.write_text(json.dumps(report))
    (failed / "job.log").write_text("saved failure evidence")
    main(["report", str(failed), "--text"])
    output = capsys.readouterr().out
    assert "task | unavailable | incomplete | unavailable" in output
    assert "Failure: task (environment_setup): Compose build failed: unknown flag" in output
    assert f"Failure evidence: {failed / 'job.log'}" in output
