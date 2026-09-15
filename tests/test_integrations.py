from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from acb.integrations import Integration, IntegrationContext, IntegrationManager, ModelEndpoint
from acb.integrations import manager
from acb.integrations.rtk import WRAPPER


@pytest.fixture
def context(tmp_path):
    return IntegrationContext("container", "arm64", "goose", "1.50.0", tmp_path, tmp_path / "artifacts")


def test_middleware_routes_and_stops_in_dependency_order(monkeypatch, context):
    calls = []

    class Middleware(Integration):
        category = "model_middleware"

        def validate(self, *args):
            pass

        def install(self, ctx):
            pass

        def verify(self, ctx):
            return {"agent_tool_verified": False}

        def start(self, ctx, upstream):
            calls.append(("start", self.name, upstream.base_url))
            return replace(upstream, base_url="http://" + self.name)

        def health_check(self, ctx):
            calls.append(("health", self.name))

        def collect(self, ctx):
            calls.append(("collect", self.name))

        def stop(self, ctx):
            calls.append(("stop", self.name))

    class A(Middleware):
        name = "a"

    class B(Middleware):
        name = "b"

    monkeypatch.setattr(manager, "REGISTRY", {"a": A, "b": B})
    lifecycle = IntegrationManager("goose", {"model_middleware": [{"name": "a"}, {"name": "b"}]})
    lifecycle.setup(context)
    endpoint = lifecycle.start(ModelEndpoint("http://praxis", "secret", "anthropic"))
    assert endpoint.base_url == "http://a"
    assert endpoint.api_key == "secret"
    assert calls[:4] == [("start", "b", "http://praxis"), ("health", "b"),
                         ("start", "a", "http://b"), ("health", "a")]
    assert lifecycle.finish() == []
    assert calls[4:] == [("collect", "a"), ("stop", "a"), ("collect", "b"), ("stop", "b")]
    assert lifecycle.finish() == []
    assert len(calls) == 8
    assert "secret" not in (context.artifact_dir / "a/manifest.json").read_text()


def test_partial_start_failure_still_collects_and_stops(monkeypatch, context):
    calls = []

    class Broken(Integration):
        name = "broken"
        category = "model_middleware"

        def validate(self, *args):
            pass

        def install(self, ctx):
            pass

        def verify(self, ctx):
            return {}

        def start(self, ctx, endpoint):
            raise RuntimeError("startup failed")

        def collect(self, ctx):
            calls.append("collect")
            raise RuntimeError("export failed")

        def stop(self, ctx):
            calls.append("stop")

    monkeypatch.setattr(manager, "REGISTRY", {"broken": Broken})
    lifecycle = IntegrationManager("goose", {"model_middleware": [{"name": "broken"}]})
    lifecycle.setup(context)
    with pytest.raises(RuntimeError, match="startup failed"):
        lifecycle.start(ModelEndpoint("http://praxis", "key", "openai"))
    assert lifecycle.finish() == ["broken collect: export failed"]
    assert calls == ["collect", "stop"]
    manifest = json.loads((context.artifact_dir / "broken/manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"] == "startup failed"


def test_environment_and_resource_conflicts(monkeypatch, context):
    class Shell(Integration):
        name = "shell"
        category = "execution_integrations"
        resources = frozenset({"shell"})

        def validate(self, *args):
            pass

        def install(self, ctx):
            pass

        def activate(self, ctx):
            return {"OPENAI_BASE_URL": "http://bypass"}

    class Other(Shell):
        name = "other"

    monkeypatch.setattr(manager, "REGISTRY", {"shell": Shell, "other": Other})
    with pytest.raises(ValueError, match="resource conflict"):
        IntegrationManager("goose", {"execution_integrations": [{"name": "shell"}, {"name": "other"}]})
    lifecycle = IntegrationManager("goose", {"execution_integrations": [{"name": "shell"}]})
    with pytest.raises(ValueError, match="environment conflict"):
        lifecycle.setup(context)
    lifecycle.finish()


@pytest.mark.parametrize("config", [
    {"execution_integrations": {}},
    {"execution_integrations": [{"name": "unknown"}]},
    {"model_middleware": [{"name": "rtk"}]},
    {"execution_integrations": [{"name": "rtk", "mode": "native"}]},
])
def test_invalid_configuration_fails_early(config):
    with pytest.raises(ValueError):
        IntegrationManager("goose", config)


@pytest.fixture
def shell(tmp_path):
    wrapper = tmp_path / "bash"
    wrapper.write_text(WRAPPER)
    wrapper.chmod(0o755)
    binary = tmp_path / "rtk"
    binary.write_text('''#!/bin/bash
case "$2" in
    supported) printf 'printf "compact\\n"'; exit 3 ;;
    denied) exit 2 ;;
    broken) exit 9 ;;
    empty) exit 0 ;;
    *) exit 1 ;;
esac
''')
    binary.chmod(0o755)
    env = {**os.environ, "ACB_RTK_BINARY": str(binary),
           "ACB_RTK_DECISIONS": str(tmp_path / "decisions.jsonl"), "TEST_VALUE": "hello world"}

    def run(command, *extra):
        return subprocess.run([str(wrapper), "-c", command, *extra], env=env, cwd=tmp_path,
                              capture_output=True, text=True, timeout=5)
    return run, tmp_path, wrapper, env


def test_wrapper_rewrite_and_passthrough_preserve_streams_and_status(shell):
    run, tmp_path, _, _ = shell
    optimized = run("supported")
    assert optimized.returncode == 0
    assert optimized.stdout == "compact\n"
    result = run('printf "%s\\n" "$TEST_VALUE"; printf "failure\\n" >&2; exit 7')
    assert result.returncode == 7
    assert result.stdout == "hello world\n"
    assert result.stderr == "failure\n"
    assert [json.loads(line)["decision"] for line in (tmp_path / "decisions.jsonl").read_text().splitlines()] == ["rewrite", "passthrough"]


def test_wrapper_preserves_cwd_arguments_heredocs_and_redirects(shell):
    run, tmp_path, _, _ = shell
    result = run('cat <<EOF > result.txt\n$TEST_VALUE\nEOF\ncat result.txt; printf "%s:%s" "$0" "$1"', "name", "arg with spaces")
    assert result.returncode == 0
    assert result.stdout == "hello world\nname:arg with spaces"
    assert (tmp_path / "result.txt").read_text() == "hello world\n"


@pytest.mark.parametrize("command,code", [("denied", 126), ("broken", 125), ("empty", 125)])
def test_wrapper_never_executes_original_after_rewrite_failure(shell, command, code):
    run, _, _, _ = shell
    result = run(command)
    assert result.returncode == code
    assert result.stdout == ""


def test_wrapper_forwards_non_command_shell_invocations(shell):
    _, _, wrapper, env = shell
    result = subprocess.run([str(wrapper), "--version"], env=env, text=True, capture_output=True)
    assert result.returncode == 0
    assert "bash" in result.stdout


def test_rtk_checksum_failure_does_not_copy_to_container(monkeypatch, context):
    from acb.integrations import rtk
    binary = context.cache_dir / "rtk"
    binary.write_bytes(b"wrong binary")
    config = {"version": "0.42.0", "mode": "shell-wrapper", "binary_path": str(binary),
              "sha256": "0" * 64, "experimental": True}
    integration = rtk.RTKIntegration(config)
    integration.validate("goose", {"version": "1.50.0"})
    copied = []
    monkeypatch.setattr(rtk, "container_cp_in", lambda *args: copied.append(args))
    with pytest.raises(ValueError, match="checksum mismatch"):
        integration.install(context)
    assert copied == []


def test_report_includes_activation_evidence(context):
    from types import SimpleNamespace
    from acb.report import build_report
    manifest_dir = context.cache_dir / "instances/example/integrations/rtk"
    manifest_dir.mkdir(parents=True)
    manifest = {"name": "rtk", "verification": {"agent_tool_verified": False}}
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest))
    cfg = SimpleNamespace(run_id="test", benchmark="test", harness="goose", model="test", proxy="praxis")
    report = build_report(context.cache_dir / "usage.jsonl", {}, context.cache_dir, cfg)
    assert json.loads(report.read_text())["integrations"] == {"example": [manifest]}
