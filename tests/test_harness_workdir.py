from conftest import TransportDouble
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
    adapter.run_container("Migrate the application", TransportDouble(), "fixture-model",
                          env, tmp_path, "cart")
    command = commands[0]
    assert command.workdir == workdir
    assert ("conda activate testbed" in command.argv[-1]) is conda
    if not conda:
        assert "miniconda" not in command.argv[-1]
    assert "exec " in command.argv[-1]
