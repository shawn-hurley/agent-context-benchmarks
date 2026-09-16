"""Container launchers must support Java benchmarks without SWE-bench conda."""
import importlib

import pytest


@pytest.mark.parametrize("module_name,class_name", [
    ("goose", "Goose"), ("pi", "Pi"),
    ("opencode", "OpenCode"), ("claude_code", "ClaudeCode"),
])
@pytest.mark.parametrize("config,workdir,conda", [
    ({}, "/testbed", True),
    ({"workdir": "/work", "conda_env": None}, "/work", False),
])
def test_container_launch_environment(monkeypatch, tmp_path, module_name, class_name,
                                      config, workdir, conda):
    module = importlib.import_module("acb.harnesses." + module_name)
    adapter = getattr(module, class_name)(config)
    monkeypatch.setattr(module, "container_exec_capture", lambda *args, **kwargs: "")
    monkeypatch.setattr(module, "container_cp_in", lambda *args, **kwargs: None)
    commands = []
    monkeypatch.setattr(module, "execute", lambda command, **kwargs: commands.append(command))
    env = adapter.build_container_env("http://praxis:8080", "fixture-key")
    adapter.run_container("Migrate the application", "fixture-container", "fixture-model",
                          env, tmp_path, "cart")
    command = commands[0]
    assert command[command.index("--workdir") + 1] == workdir
    assert ("conda activate testbed" in command[-1]) is conda
    if not conda:
        assert "miniconda" not in command[-1]
    assert "exec " in command[-1]
