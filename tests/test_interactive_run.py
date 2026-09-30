"""Interactive run decisions without allocating a real terminal."""
from pathlib import Path
import json
import sys

import pytest
import yaml

from acb.cli import main
from acb.interactive_run import RunDraft, _Screen, bundled_workflows, finish


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "models.yaml").write_text(
        "local:\n  api: openai\n  endpoint: localhost:8000\n  tls: false\n")
    return tmp_path


def test_new_run_save_resolves_screen_values(configured):
    draft = RunDraft.create()
    draft.set("run_id", "interactive-smoke")
    draft.set("benchmark", "swebench-lite")
    draft.set("harness", ["goose", "pi"])
    draft.set("model", "local")
    draft.set("subset", ["task-1", "task-2"])
    draft.set("limit", 2)
    draft.set("timeout", 90)
    draft.set("max_workers", 2)
    path = configured / "runs" / "smoke.yaml"
    document = finish(draft, "save", path).to_dict()
    saved = yaml.safe_load(path.read_text())
    assert saved["run_id"] == document["run_id"] == "interactive-smoke"
    assert saved["execution"]["timeout"] == 90
    assert saved["max_workers"] == document["max_workers"] == 2
    assert set(document["harnesses"]) == {"goose", "pi"}
    assert document["subset"] == ["task-1", "task-2"]
    assert document["requested_config"]["source_file"] == str(path)


def test_bundled_workflow_is_selectable_and_saved(configured):
    assert "kantra-controller" in bundled_workflows()
    draft = RunDraft.create()
    draft.set("benchmark", "scarfbench")
    draft.set("workflow", "kantra-controller")
    path = configured / "with-workflow.yaml"
    document = finish(draft, "save", path).to_dict()
    assert yaml.safe_load(path.read_text())["workflow"] == "kantra-controller"
    assert document["workflow"]["name"] == "kantra-controller"
    assert document["requested_config"]["workflow"] == "kantra-controller"


def test_workflow_editor_selects_bundled_custom_and_none(configured, monkeypatch):
    draft = RunDraft.create()
    screen = _Screen(None, None)
    monkeypatch.setattr(screen, "menu", lambda *args, **kwargs: "kantra-controller")
    screen.edit(draft, "workflow")
    assert draft.value("workflow") == "kantra-controller"

    monkeypatch.setattr(screen, "menu", lambda *args, **kwargs: "Enter workflow directory...")
    monkeypatch.setattr(screen, "prompt", lambda *args, **kwargs: "./my-workflow")
    screen.edit(draft, "workflow")
    assert draft.value("workflow") == "./my-workflow"

    monkeypatch.setattr(screen, "menu", lambda *args, **kwargs: "No workflow")
    screen.edit(draft, "workflow")
    assert draft.value("workflow") is None


def test_custom_workflow_path_resolves_relative_to_saved_yaml(configured):
    workflow = configured / "my-workflow"
    workflow.mkdir()
    (workflow / "instruction.md").write_text("Finish the task.\n")
    (workflow / "workflow.yaml").write_text("""version: 1
name: my-workflow
benchmarks: [scarfbench]
harnesses: [goose]
steps:
  - name: finish
    instruction: instruction.md
    timeout_sec: 60
    gate: {type: native}
""")
    draft = RunDraft.create()
    draft.set("benchmark", "scarfbench")
    draft.set("workflow", "./my-workflow")
    path = configured / "custom.yaml"
    document = finish(draft, "save", path).to_dict()
    assert yaml.safe_load(path.read_text())["workflow"] == "./my-workflow"
    assert document["workflow"]["source_dir"] == str(workflow)


def test_new_run_can_review_workflow_relative_to_future_save_path(configured, monkeypatch):
    destination = configured / "runs" / "run.yaml"
    workflow = destination.parent / "my-workflow"
    workflow.mkdir(parents=True)
    (workflow / "instruction.md").write_text("Finish the task.\n")
    (workflow / "workflow.yaml").write_text("""version: 1
name: my-workflow
benchmarks: [scarfbench]
harnesses: [goose]
steps:
  - name: finish
    instruction: instruction.md
    timeout_sec: 60
    gate: {type: native}
""")
    draft = RunDraft.create()
    draft.set("benchmark", "scarfbench")
    draft.set("workflow", "my-workflow")
    monkeypatch.setattr(RunDraft, "create", classmethod(lambda cls, config_dir=None: draft))
    screen = _Screen(None, None)
    actions = iter(["Create new run", "Review", "Save and run"])

    def choose(title, options, **kwargs):
        action = next(actions)
        assert action in options
        return action

    monkeypatch.setattr(screen, "menu", choose)
    monkeypatch.setattr(screen, "prompt", lambda *args, **kwargs: str(destination))
    document = screen.run().to_dict()
    assert document["workflow"]["source_dir"] == str(workflow)
    assert yaml.safe_load(destination.read_text())["workflow"] == "my-workflow"


def test_workflow_validation_and_unsaved_edit(configured):
    path = configured / "loaded.yaml"
    path.write_text("""# keep this comment
run_id: workflow-test
benchmark: scarfbench
harness: goose
model: local
workflow: kantra-controller
""")
    original = path.read_bytes()
    draft = RunDraft.load(path)
    draft.set("workflow", None)
    document = finish(draft, "unsaved").to_dict()
    assert document["workflow"] is None
    assert document["requested_config"]["workflow"] is None
    assert path.read_bytes() == original
    finish(draft, "save", path)
    assert "# keep this comment" in path.read_text()
    assert "workflow" not in yaml.safe_load(path.read_text())

    draft.set("workflow", "kantra-controller")
    draft.set("benchmark", "swebench-lite")
    with pytest.raises(ValueError, match="does not support benchmarks"):
        finish(draft, "unsaved")
    draft.set("workflow", "./missing-workflow")
    with pytest.raises(FileNotFoundError, match="workflow definition not found"):
        finish(draft, "unsaved")


def test_loaded_edit_preserves_comments_and_advanced_fields(configured):
    path = configured / "existing.yaml"
    path.write_text("""# owner's note
schema_version: 2
run_id: before # review this
benchmark: swebench-lite
harness: goose
model: local
overrides:
  harness:
    system_prompt: Keep the special prompt
# execution comment
execution:
  offline: true # advanced switch
  timeout: 180
# final note
""")
    draft = RunDraft.load(path)
    draft.set("run_id", "after")
    draft.set("timeout", 120)
    plan = finish(draft, "save", path).to_dict()
    text = path.read_text()
    assert "# owner's note" in text
    assert "# execution comment" in text
    assert "offline: true # advanced switch" in text
    assert "# final note" in text
    assert "system_prompt: Keep the special prompt" in text
    assert yaml.safe_load(text)["execution"]["timeout"] == 120
    assert plan["requested_config"]["overrides"]["harness"]["system_prompt"] == "Keep the special prompt"


def test_save_as_preserves_source_comments_and_nested_execution(configured):
    source = configured / "source.yaml"
    source.write_text("""# keep this note
run_id: before
benchmark: swebench-lite
harness: goose
model: local
execution:
    offline: true # keep this too
    max_workers: 4
""")
    original = source.read_text()
    draft = RunDraft.load(source)
    draft.set("timeout", 75)
    draft.set("max_workers", 2)
    destination = configured / "copies" / "edited.yaml"
    document = finish(draft, "save", destination).to_dict()
    saved = destination.read_text()
    assert source.read_text() == original
    assert "# keep this note" in saved
    assert "offline: true # keep this too" in saved
    assert yaml.safe_load(saved)["execution"] == {"offline": True, "max_workers": 2, "timeout": 75}
    assert document["max_workers"] == 2


def test_flow_style_execution_retains_advanced_values(configured):
    path = configured / "flow.yaml"
    path.write_text("run_id: flow\nbenchmark: swebench-lite\nharness: goose\nmodel: local\n"
                    "execution: {offline: true, timeout: 180, max_workers: 4}\n")
    draft = RunDraft.load(path)
    draft.set("timeout", 60)
    draft.set("max_workers", 3)
    finish(draft, "save", path)
    assert yaml.safe_load(path.read_text())["execution"] == {
        "offline": True, "timeout": 60, "max_workers": 3}


def test_changed_source_is_not_overwritten(configured):
    path = configured / "changed.yaml"
    path.write_text("run_id: before\nbenchmark: swebench-lite\nharness: goose\nmodel: local\n")
    draft = RunDraft.load(path)
    draft.set("run_id", "after")
    path.write_text(path.read_text() + "# concurrent edit\n")
    with pytest.raises(ValueError, match="changed while the editor was open"):
        finish(draft, "save", path)
    assert "# concurrent edit" in path.read_text()


def test_unsaved_edit_runs_current_values_without_writing(configured):
    path = configured / "existing.yaml"
    path.write_text("run_id: before\nbenchmark: swebench-lite\nharness: goose\nmodel: local\n")
    original = path.read_bytes()
    draft = RunDraft.load(path)
    draft.set("run_id", "after")
    draft.set("harness", ["goose", "pi"])
    document = finish(draft, "unsaved").to_dict()
    assert path.read_bytes() == original
    assert document["run_id"] == "after"
    assert set(document["harnesses"]) == {"goose", "pi"}
    assert document["interactive_run"] == "unsaved"
    assert document["requested_config"]["interactive_run"] == "unsaved"
    assert document["requested_config"]["source_file"] == str(path)
    from acb.provenance import save_configuration
    evidence = configured / "evidence"
    evidence.mkdir()
    save_configuration(evidence, document, effective_run_id="after")
    assert json.loads((evidence / "requested.json").read_text())["run_id"] == "after"
    assert json.loads((evidence / "resolved.json").read_text())["interactive_run"] == "unsaved"


def test_invalid_and_cancel_never_save(configured):
    draft = RunDraft.create()
    draft.set("model", "not-configured")
    path = configured / "invalid.yaml"
    with pytest.raises(ValueError, match="model"):
        finish(draft, "save", path)
    assert not path.exists()
    assert finish(draft, "cancel") is None


def test_cli_interactive_choices_and_direct_paths(configured, monkeypatch):
    from acb import interactive_run
    from acb.harbor import backend
    calls = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(interactive_run, "configure", lambda config_dir: finish(RunDraft.create(), "unsaved"))
    monkeypatch.setattr(backend, "run_plan", lambda plan, **kwargs: calls.append(("interactive", plan.to_dict())))
    monkeypatch.setattr(backend, "run", lambda cfg, **kwargs: calls.append(("direct", cfg)))
    main(["run"])
    assert calls[-1][0] == "interactive"
    assert calls[-1][1]["interactive_run"] == "unsaved"

    path = configured / "saved.yaml"
    path.write_text("run_id: direct\nbenchmark: swebench-lite\nharness: goose\nmodel: local\n")
    main(["run", "--config", str(path)])
    assert calls[-1][0] == "direct"
    main(["run", "--benchmark", "swebench-lite", "--harness", "goose",
          "--model", "local", "--run-id", "flags"])
    assert calls[-1][0] == "direct"
    assert calls[-1][1].run_id == "flags"


def test_cli_bare_run_requires_tty(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit, match="interactive terminal"):
        main(["run"])


def test_cli_cancel_starts_nothing(configured, monkeypatch):
    from acb import interactive_run
    from acb.harbor import backend
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(interactive_run, "configure", lambda config_dir: None)
    monkeypatch.setattr(backend, "run_plan", lambda *args, **kwargs: pytest.fail("run started"))
    main(["run"])
