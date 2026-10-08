from conftest import TransportDouble
"""Native protocol tests plus final harness configuration composition."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from acb.integrations import IntegrationActivation, IntegrationContext, IntegrationFailure, IntegrationManager
from acb.integrations import manager, rtk

ASSETS = Path(rtk.__file__).parent / "assets/rtk"


@pytest.fixture
def native_config(tmp_path):
    binary = tmp_path / "rtk"
    binary.write_bytes(b"\x7fELF\x02\x01" + b"\0" * 12 + (183).to_bytes(2, "little"))
    return {"name": "rtk", "version": "0.48.0", "mode": "native", "experimental": True,
            "binary_path": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}


@pytest.mark.parametrize("harness,version", [("pi", "0.84.3"), ("opencode", "1.18.22")])
def test_native_configuration_and_activation(native_config, tmp_path, harness, version):
    lifecycle = IntegrationManager(harness, {"version": version, "execution_integrations": [native_config]})
    context = IntegrationContext("container", "arm64", harness, version, tmp_path, tmp_path)
    activation = lifecycle.entries[0].activate(context)
    assert activation.env["RTK_DB_PATH"].startswith("/opt/acb/rtk/evidence/")
    assert activation.pi_extensions == (("/opt/acb/rtk/native/pi.ts",) if harness == "pi" else ())
    assert activation.opencode_plugins == (("/opt/acb/rtk/native/opencode.ts",) if harness == "opencode" else ())
    with pytest.raises(ValueError, match="validated harness version"):
        IntegrationManager(harness, {"version": "9.9.9", "execution_integrations": [native_config]})


def test_wrong_architecture_rejected_before_container_copy(native_config, tmp_path, monkeypatch):
    integration = rtk.RTKIntegration(native_config)
    integration.validate("pi", {"version": "0.84.3"})
    monkeypatch.setattr(rtk, "container_cp_in", lambda *args: pytest.fail("must not copy wrong architecture"))
    with pytest.raises(ValueError, match="target-architecture"):
        integration.install(IntegrationContext("container", "x86_64", "pi", "0.84.3", tmp_path, tmp_path))


@pytest.mark.parametrize("harness", ["pi", "opencode"])
def test_harness_preserves_routing_and_composes_native_activation(monkeypatch, tmp_path, harness):
    from acb.harnesses import pi, opencode
    module = pi if harness == "pi" else opencode
    adapter = module.Pi({"system_prompt": "Keep this instruction"}) if harness == "pi" else module.OpenCode({"system_prompt": "Keep this instruction"})
    adapter.api = "openai"
    adapter.integration_activation = IntegrationActivation(pi_extensions=("/opt/acb/rtk/native/pi.ts",) if harness == "pi" else (),
                                                         opencode_plugins=("/opt/acb/rtk/native/opencode.ts",) if harness == "opencode" else ())
    copied = {}
    captured = []
    monkeypatch.setattr(module, "container_exec_capture", lambda *args: "")
    monkeypatch.setattr(module, "container_cp_in", lambda container, source, target: copied.update({target: Path(source).read_text()}))
    monkeypatch.setattr(module, "execute", lambda command, **kwargs: captured.append(command))
    if harness == "opencode":
        adapter._mcp_config = {"mcp": {"fixture": {"type": "local", "command": ["fixture"], "enabled": False}}}
    env = adapter.build_container_env("http://praxis:8080", "fixture-key")
    adapter.run_container("Fix this", TransportDouble(), "fixture-model", env, tmp_path, "example")
    assert "Keep this instruction" in (captured[-1].argv[-1] if harness == "pi" else copied["/root/.config/opencode/AGENTS.md"])
    if harness == "pi":
        assert "-e /opt/acb/rtk/native/pi.ts" in captured[-1].argv[-1]
        assert captured[-1].argv[-1].index("-e /opt") < captured[-1].argv[-1].index("-- '" if "-- '" in captured[-1].argv[-1] else "-- Fix")
        config = json.loads(copied["/tmp/pi-agent/models.json"])
        assert config["providers"]["openai"]["baseUrl"] == "http://praxis:8080/v1"
    else:
        inline = "OPENCODE_CONFIG_CONTENT=" + captured[-1].env["OPENCODE_CONFIG_CONTENT"]
        config = json.loads(inline.split("=", 1)[1])
        assert config["provider"]["acb"]["options"]["baseURL"] == "http://praxis:8080/v1"
        assert config["plugin"] == ["file:///opt/acb/rtk/native/opencode.ts"]
        assert config["mcp"] == adapter._mcp_config["mcp"]


@pytest.mark.parametrize("harness,version", [("pi", "0.84.3"), ("opencode", "1.18.22")])
def test_harness_installer_receives_configured_version(monkeypatch, tmp_path, harness, version):
    from acb.harnesses import pi, opencode
    module = pi if harness == "pi" else opencode
    adapter = module.Pi({"version": version}) if harness == "pi" else module.OpenCode({"version": version})
    calls = []
    def ensure(arch, cache_dir, **kwargs):
        calls.append(kwargs["version"])
        return tmp_path
    monkeypatch.setattr(module, "ensure_linux_files" if harness == "pi" else "ensure_linux_binary", ensure)
    monkeypatch.setattr(module, "container_cp_in", lambda *args: None)
    monkeypatch.setattr(module, "container_exec_capture", lambda *args: "")
    if harness == "opencode":
        from acb.harnesses import opencode_runtime
        monkeypatch.setattr(opencode_runtime, "ensure_plugin_runtime", lambda cache_dir: tmp_path)
    adapter.setup_container("container", "arm64", tmp_path)
    assert calls == [version]


@pytest.fixture
def node_adapter(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for native adapter protocol tests")
    fake = tmp_path / "rtk"
    fake.write_text('''#!/bin/bash
if [[ "$1" == "--version" ]]; then printf 'rtk 0.48.0\\n'; exit 0; fi
case "$2" in
    supported) printf 'printf "compact\\n"'; exit "${FAKE_STATUS:-3}" ;;
    denied) exit 2 ;;
    broken) exit 9 ;;
    empty) exit 0 ;;
    malformed) printf 'unexpected'; exit 1 ;;
    slow) sleep 10 ;;
    *) exit 1 ;;
esac
''')
    fake.chmod(0o755)
    env = {**os.environ, "ACB_RTK_BINARY": str(fake), "ACB_RTK_VERSION": "0.48.0",
           "ACB_RTK_DECISIONS": str(tmp_path / "decisions.jsonl"), "ACB_RTK_FAILURE": str(tmp_path / "failure.json"),
           "ACB_RTK_LOADED": str(tmp_path / "loaded.json"), "RTK_DB_PATH": str(tmp_path / "tracking.db")}
    def run(body, **extra_env):
        source = f'import {{initialize, rewrite, recordResult}} from {json.dumps((ASSETS / "common.mjs").as_uri())};\n' + body
        return subprocess.run([node, "--input-type=module", "-e", source], cwd=tmp_path, env={**env, **extra_env},
                              text=True, capture_output=True, timeout=8)
    return run, tmp_path


@pytest.mark.parametrize("status", ["0", "3"])
def test_native_rewrite_status_and_result_evidence(node_adapter, status):
    run, tmp_path = node_adapter
    result = run('await initialize("pi"); const command = await rewrite("supported", "call-1"); recordResult("call-1", "compact\\n"); console.log(command);', FAKE_STATUS=status)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'printf "compact\n"'
    records = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert records[0]["rewrite_status"] == int(status)
    assert records[0]["tool_call_id"] == records[1]["tool_call_id"] == "call-1"
    assert records[1]["output_bytes"] == 8
    assert not (tmp_path / "failure.json").exists()


@pytest.mark.parametrize("command", ["broken", "empty", "malformed", "slow"])
def test_native_errors_never_pass_through_and_leave_failure_marker(node_adapter, command):
    run, tmp_path = node_adapter
    result = run(f'await initialize("opencode"); await rewrite({json.dumps(command)}, "call");')
    assert result.returncode != 0
    assert result.stdout == ""
    assert (tmp_path / "failure.json").exists()
    assert json.loads((tmp_path / "decisions.jsonl").read_text())["decision"] == "error"


def test_native_passthrough_already_rtk_and_whole_command(node_adapter):
    run, tmp_path = node_adapter
    command = 'printf "%s" "$VALUE" | cat > "a file"; cat <<EOF\nquoted\nEOF'
    result = run(f'await initialize("pi"); console.log(JSON.stringify([await rewrite({json.dumps(command)}, "one"), await rewrite("rtk git status", "two")]));')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [command, "rtk git status"]
    assert [json.loads(line)["decision"] for line in (tmp_path / "decisions.jsonl").read_text().splitlines()] == ["passthrough", "already_rtk"]


def test_native_denial_is_blocked_without_becoming_an_adapter_error(node_adapter):
    run, tmp_path = node_adapter
    result = run('await initialize("pi"); await rewrite("denied", "call");')
    assert result.returncode != 0 and result.stdout == ""
    assert json.loads((tmp_path / "decisions.jsonl").read_text())["decision"] == "deny"
    assert not (tmp_path / "failure.json").exists()


def test_native_cancellation_records_failure(node_adapter):
    run, tmp_path = node_adapter
    result = run('await initialize("pi"); const controller = new AbortController(); setTimeout(() => controller.abort(), 100); await rewrite("slow", "call", {signal: controller.signal});')
    assert result.returncode != 0
    assert (tmp_path / "failure.json").exists()


@pytest.mark.parametrize("harness", ["pi", "opencode"])
def test_native_factories_mutate_only_bash_command_and_block_errors(node_adapter, harness):
    run, tmp_path = node_adapter
    # Node's type stripper lets tests exercise the shipped TypeScript assets directly.
    module = json.dumps((ASSETS / (harness + ".ts")).as_uri())
    source = f'''import adapter from {module};
const handlers = {{}};
const args = {{command: "supported", timeout: 123, description: "Keep this"}};
let failure;
'''
    if harness == "pi":
        source += '''await adapter({on: (name, fn) => handlers[name] = fn});
await handlers.tool_call({toolName:"read", input:{path:"a"}}, {});
await handlers.tool_call({toolName:"bash", toolCallId:"one", input:args}, {cwd:process.cwd()});
await handlers.tool_result({toolName:"bash", toolCallId:"one", content:"compact"});
failure = await handlers.tool_call({toolName:"bash", toolCallId:"two", input:{command:"broken"}}, {});
if (!failure.block || !failure.terminate) throw new Error("Pi must block the broken rewrite");
'''
    else:
        source += '''Object.assign(handlers, await adapter({directory:process.cwd()}));
await handlers["tool.execute.before"]({tool:"read"}, {args:{path:"a"}});
await handlers["tool.execute.before"]({tool:"bash", sessionID:"session", callID:"one"}, {args});
await handlers["tool.execute.after"]({tool:"bash", sessionID:"session", callID:"one"}, {output:"compact"});
try { await handlers["tool.execute.before"]({tool:"bash", sessionID:"session", callID:"two"}, {args:{command:"broken"}}); }
catch (error) { failure = error; }
if (!failure) throw new Error("OpenCode must throw for the broken rewrite");
'''
    source += 'console.log(JSON.stringify(args));'
    result = run(source, NODE_OPTIONS="--experimental-strip-types")
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args == {"command": 'printf "compact\n"', "timeout": 123, "description": "Keep this"}
    records = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert [item.get("phase") for item in records] == ["decision", "result", "decision"]
    assert (tmp_path / "failure.json").exists()


@pytest.mark.parametrize("loaded,failed", [(True, False), (False, False), (True, True)])
def test_collection_requires_loading_and_detects_recovered_adapter_failures(monkeypatch, native_config, tmp_path, loaded, failed):
    lifecycle = IntegrationManager("pi", {"version": "0.84.3", "execution_integrations": [native_config]})
    integration = lifecycle.entries[0]
    context = IntegrationContext("container", "arm64", "pi", "0.84.3", tmp_path, tmp_path / "artifacts")
    lifecycle.contexts["rtk"] = context
    lifecycle.attempted.append(integration)
    lifecycle.states["rtk"]["status"] = "ready"
    evidence = context.artifact_dir / "evidence"
    evidence.mkdir(parents=True)
    if loaded:
        (evidence / "loaded.json").write_text(json.dumps({"harness": "pi", "version": "0.48.0"}))
    if failed:
        (evidence / "failure.json").write_text('{}')
    (evidence / "decisions.jsonl").write_text('\n'.join(json.dumps(item) for item in [
        {"phase": "decision", "decision": "rewrite", "tool_call_id": "one"},
        {"phase": "result", "tool_call_id": "one"},
    ]))
    monkeypatch.setattr(rtk, "container_cp_out", lambda *args: None)
    monkeypatch.setattr(rtk, "container_exec_capture", lambda *args, **kwargs: '{"summary":{"total_commands":1,"total_tokens_saved":0}}')
    errors = lifecycle.finish()
    manifest = json.loads((context.artifact_dir / "manifest.json").read_text())
    assert bool(errors) == (failed or not loaded)
    assert manifest["status"] == ("failed" if errors else "finished")
    assert manifest["metadata"]["activity"]["agent_tool_verified"] is loaded
