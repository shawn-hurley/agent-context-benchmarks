"""Exercise user journeys and error paths without containers or model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from acb.cli import main
from acb.config import RunConfig
from acb.interactive_run import _Screen


@pytest.mark.parametrize("flags", [
    ["--model", "candidate"], ["--limit", "1"], ["--max-workers", "2"],
    ["--benchmark", "scarfbench"], ["--harness", "pi"],
    ["--run-id", "new-name"], ["--proxy", "praxis"],
])
def test_config_selection_conflicts_stop_before_execution(monkeypatch, capsys, flags):
    monkeypatch.setattr("acb.harbor.backend.run", lambda *a, **kw: pytest.fail("execution started"))
    with pytest.raises(SystemExit) as error:
        main(["run", "--config", "missing.yaml", *flags])
    assert error.value.code == 2
    assert "edit those settings in the YAML" in capsys.readouterr().err


@pytest.mark.parametrize("flags, message", [
    (["--model", "local"], "--benchmark --harness --run-id"),
    (["--control", "oracle"], "use --config YAML"),
    (["--limit", "0"], "must be a positive integer"),
    (["--max-workers", "-1"], "must be a positive integer"),
])
def test_explicit_run_errors_are_actionable(capsys, flags, message):
    if "must be" in message:
        flags = ["--benchmark", "harbor", "--harness", "pi", "--model", "local", "--run-id", "test", *flags]
    with pytest.raises(SystemExit) as error:
        main(["run", *flags])
    assert error.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("entrypoint", [["-m", "acb.cli"], ["-c", "from acb.cli import main; main()"]])
def test_missing_config_errors_match_across_entrypoints(tmp_path, entrypoint):
    result = subprocess.run([sys.executable, *entrypoint, "resolve", "--config", str(tmp_path / "missing.yaml")],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "run config not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert "acb.log" not in result.stderr


@pytest.mark.parametrize("kind", ["missing", "empty", "broken-json", "array", "broken-child"])
def test_report_errors_preserve_exports(tmp_path, capsys, kind):
    root = tmp_path / "run"
    if kind != "missing":
        root.mkdir()
    if kind in {"broken-json", "array"}:
        (root / "report.json").write_text("{" if kind == "broken-json" else "[]")
    if kind == "broken-child":
        (root / "pi").mkdir()
        (root / "pi/report.json").write_text("[]")
    html = tmp_path / "report.html"
    html.write_text("keep this export")
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"keep this bundle")
    with pytest.raises(SystemExit) as error:
        main(["report", str(root), "--html", str(html), "--bundle", str(archive)])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out == "" and str(root) in output.err
    assert html.read_text() == "keep this export"
    assert archive.read_bytes() == b"keep this bundle"


def test_report_recovers_harness_results_without_suite_overview(tmp_path, capsys):
    directory = tmp_path / "run/pi"
    directory.mkdir(parents=True)
    report = {"harness": "pi", "instances": 0, "evaluations": []}
    (directory / "report.json").write_text(json.dumps(report))
    main(["report", str(directory.parent)])
    assert json.loads(capsys.readouterr().out) == report


def test_compare_requires_candidate_and_validates_all_inputs(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["compare", str(tmp_path)])
    assert error.value.code == 2
    assert "CANDIDATE_RUN" in capsys.readouterr().err
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "report.json").write_text('{"harness":"pi","instances":0}')
    with pytest.raises(SystemExit) as error:
        main(["compare", str(baseline), str(tmp_path / "missing")])
    assert error.value.code == 1
    assert capsys.readouterr().out == ""


def test_empty_models_keeps_json_output_and_explains_next_step(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["list", "models", "--config-dir", str(tmp_path)])
    output = capsys.readouterr()
    assert json.loads(output.out) == {}
    assert "models.yaml" in output.err and "--config-dir" in output.err


def test_noninteractive_clean_requires_explicit_confirmation(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "report.zip"
    artifact.write_text("keep")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        main(["clean", "--output-dir", str(tmp_path)])
    assert error.value.code == 1 and artifact.read_text() == "keep"
    output = capsys.readouterr()
    assert "item(s)" in output.out
    assert "--dry-run" in output.err and "--yes" in output.err


def test_init_resolves_its_own_registries_from_elsewhere(tmp_path, monkeypatch, capsys):
    project = tmp_path / "experiment"
    (project / ".acb").mkdir(parents=True)
    (project / ".acb/models.yaml").write_text("unrelated: {}\n")
    main(["init", str(project)])
    assert f"acb resolve --config {project / 'run.yaml'}" in capsys.readouterr().out
    registry = project / "config/models.yaml"
    registry.write_text(yaml.safe_dump({"local-model": {"model": "actual-model", "api": "openai", "endpoint": "localhost:8000"}}))
    monkeypatch.chdir(tmp_path)
    main(["resolve", "--config", str(project / "run.yaml")])
    assert json.loads(capsys.readouterr().out)["model"]["name"] == "actual-model"
    before = registry.read_bytes()
    main(["init", str(project)])
    assert registry.read_bytes() == before


def test_home_paths_work_for_config_and_list(tmp_path, capsys):
    directory = "~/" + os.path.relpath(tmp_path, Path.home())
    (tmp_path / "run.yaml").write_text("run_id: home\nbenchmark: harbor\nharness: pi\nmodel: local\n")
    assert RunConfig.from_file(f"{directory}/run.yaml").run_id == "home"
    (tmp_path / "models.yaml").write_text("local: {api: openai, endpoint: localhost:8000}\n")
    main(["list", "models", "--config-dir", directory])
    assert "local" in json.loads(capsys.readouterr().out)


class MenuWindow:
    """Capture what is actually visible, with arrow keys and terminal resizing."""
    def __init__(self, keys, height):
        self.keys = iter(keys)
        self.height = height
        self.frames = []

    def erase(self):
        self.lines = {}

    def getmaxyx(self):
        return self.height, 80

    def addnstr(self, y, x, text, limit):
        assert 0 <= y < self.height
        self.lines[y] = text[:limit]

    def refresh(self):
        self.frames.append(dict(self.lines))

    def getch(self):
        key = next(self.keys)
        if isinstance(key, tuple):
            self.height, key = key
        return key


@pytest.mark.parametrize("height", [10, 24])
def test_menu_scrolls_to_every_choice_and_keeps_error_and_navigation_visible(height):
    options = [f"Option {i}" for i in range(35)]
    window = MenuWindow([*[ord("j")] * 34, (10, -1), 10], height)
    screen = _Screen(window, None)
    screen.error = "Fix this value"
    assert screen.menu("Choose", options, details=["Details"] * 8) == options[-1]
    for index, frame in enumerate(window.frames):
        selected = min(index, 34)
        assert f"> Option {selected}" in frame.values()
        assert "Fix this value" in frame.values()
        assert any("Esc back" in line for line in frame.values())


def test_verbose_preserves_traceback_for_diagnostics():
    with pytest.raises(FileNotFoundError, match="run config not found"):
        main(["run", "--config", "/tmp/acb-ux-nonexistent.yaml", "--verbose"])
