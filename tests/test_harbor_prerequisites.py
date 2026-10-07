"""Fail before downloads/trials when the selected Compose frontend cannot run."""
import subprocess
from types import SimpleNamespace

import pytest

from acb.cli import main
from acb.harbor import environment
from harbor.environments.docker.runtime import ContainerRuntime


@pytest.mark.parametrize("engine,compose,expected", [
    ("docker", ("docker", "compose"), [["docker", "compose", "version"], ["docker", "info"], ["docker", "compose", "ls"]]),
    ("podman", ("podman", "compose"), [["podman", "compose", "version"], ["podman", "info"], ["podman", "compose", "ls"]]),
    ("podman", ("podman-compose", "--in-pod=false"), [["podman-compose", "--in-pod=false", "--version"], ["podman", "info"]]),
])
def test_checks_selected_frontend_and_connection_without_starting_containers(monkeypatch, engine, compose, expected):
    runtime = ContainerRuntime(engine=(engine,), compose=compose)
    provider = environment.ACBDockerEnvironment if engine == "docker" else environment.ACBPodmanEnvironment
    monkeypatch.setattr(provider, "runtime", classmethod(lambda cls: runtime))
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/bin/" + name)
    calls = []
    def run(argv, **kwargs):
        assert kwargs == {"capture_output": True, "text": True, "timeout": 10}
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="ready", stderr="")
    monkeypatch.setattr(environment.subprocess, "run", run)
    environment.check_compose(engine)
    assert calls == expected


def test_missing_engine_names_installation_and_never_launches_a_process(monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    monkeypatch.setattr(environment.subprocess, "run", lambda *a, **kw: pytest.fail("missing engine launched a process"))
    with pytest.raises(RuntimeError, match="docker is not installed or not on PATH"):
        environment.check_compose("docker")


@pytest.mark.parametrize("failure", ["version", "info", "ls", "missing", "timeout"])
def test_failed_frontend_or_connection_provides_command_and_setup_hint(monkeypatch, failure):
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/bin/" + name)
    def run(argv, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("compose executable missing")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return SimpleNamespace(returncode=1 if argv[-1] == failure else 0, stdout="", stderr="fixture provider failure")
    monkeypatch.setattr(environment.subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        environment.check_compose("docker")
    assert "Container prerequisite check failed:" in str(error.value)
    assert "docker compose" in str(error.value)
    assert "Install or enable the Docker Compose plugin" in str(error.value)
    assert "docs/quick-start.md" in str(error.value)


def test_podman_runtime_selection_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/bin/" + name)
    def unavailable(cls):
        raise RuntimeError("Podman requires podman-compose or a Compose V2 compatible frontend")
    monkeypatch.setattr(environment.ACBPodmanEnvironment, "runtime", classmethod(unavailable))
    with pytest.raises(RuntimeError, match="Install podman-compose or configure"):
        environment.check_compose("podman")


@pytest.mark.parametrize("command", [["prepare"], ["run"], ["run", "--control", "oracle"], ["run", "--control", "nop"]])
def test_cli_stops_before_cache_downloads_or_trial_output(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    main(["init", "experiment", "--template", "quickstart", "--environment", "docker"])
    capsys.readouterr()
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(environment.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="", stderr="docker: unknown command: docker compose"))
    monkeypatch.setattr("acb.harbor.backend._worker", lambda *a, **kw: pytest.fail("preflight failure reached worker"))
    with pytest.raises(SystemExit) as error:
        main([*command, "--config", "experiment/run.yaml"])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "'docker compose version' exited 1" in output.err
    assert "Install or enable the Docker Compose plugin" in output.err
    assert "Traceback" not in output.err
    assert not (tmp_path / "experiment/.cache").exists()
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("command", ["resolve", "tasks"])
def test_configuration_and_discovery_remain_available_without_compose(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    main(["init", "experiment", "--template", "quickstart"])
    capsys.readouterr()
    monkeypatch.setattr(environment.subprocess, "run", lambda *a, **kw: pytest.fail("read-only command checked container setup"))
    main([command, "--config", "experiment/run.yaml", "--json"])
    assert '"benchmark": "smoke"' in capsys.readouterr().out
