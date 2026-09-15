"""Claude hook protocol, permission preservation, and launch-profile parity."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from acb.harnesses import claude_code
from acb.integrations import IntegrationActivation, IntegrationContext, IntegrationFailure, IntegrationManager
from acb.integrations import rtk


@pytest.fixture
def hook(tmp_path):
    binary = tmp_path / "rtk"
    binary.write_text('''#!/bin/bash
if [[ "$1" == "--version" ]]; then echo "rtk 0.48.0"; exit 0; fi
case "$2" in
 supported) echo 'rtk git status'; exit "${FAKE_STATUS:-3}" ;;
 denied) exit 2 ;;
 broken) exit 9 ;;
 empty) exit 0 ;;
 malformed) echo unexpected; exit 1 ;;
 slow) sleep 10 ;;
 oversized) head -c 1100000 /dev/zero ;;
 *) exit 1 ;;
esac
''')
    binary.chmod(0o755)
    env = {**os.environ, "ACB_RTK_BINARY": str(binary), "ACB_RTK_VERSION": "0.48.0",
           "ACB_RTK_DECISIONS": str(tmp_path / "decisions.jsonl"),
           "ACB_RTK_FAILURE": str(tmp_path / "failure.json"),
           "ACB_RTK_LOADED": str(tmp_path / "loaded.json"),
           "ACB_RTK_REWRITE_DIR": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path)}
    def invoke(command="supported", event="PreToolUse", raw=None, **overrides):
        payload = {"hook_event_name": event, "session_id": "session", "tool_use_id": "call",
                   "tool_name": "Bash", "tool_input": {"command": command, "timeout": 12000,
                   "description": "Preserve this", "run_in_background": False},
                   "tool_response": {"stdout": "compact", "stderr": "error", "exit_code": 7}}
        completed = subprocess.run([sys.executable, str(Path(rtk.__file__).parent / "assets/rtk/claude_hook.py")],
                                   input=json.dumps(payload) if raw is None else raw,
                                   env={**env, **overrides}, text=True, capture_output=True, timeout=8)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    return invoke, tmp_path


@pytest.mark.parametrize("status", ["0", "3"])
def test_updated_input_preserves_fields_without_permission_override(hook, status):
    invoke, root = hook
    output = invoke(FAKE_STATUS=status)["hookSpecificOutput"]
    assert output == {"hookEventName": "PreToolUse", "updatedInput": {
        "command": "rtk git status", "timeout": 12000, "description": "Preserve this", "run_in_background": False}}
    invoke(event="PostToolUseFailure")
    records = [json.loads(line) for line in (root / "decisions.jsonl").read_text().splitlines()]
    assert records[1]["rewrite_status"] == int(status)
    assert records[1]["tool_call_id"] == records[2]["tool_call_id"] == "session:call"
    assert records[2]["is_error"]
    assert not (root / "failure.json").exists()


@pytest.mark.parametrize("command", ["unsupported", "rtk git status", 'printf "%s" "$VALUE" | cat > "a file"; cat <<EOF\ntext\nEOF'])
def test_passthrough_and_existing_rtk(hook, command):
    invoke, root = hook
    assert invoke(command) == {}
    records = [json.loads(line) for line in (root / "decisions.jsonl").read_text().splitlines()]
    assert records[-1]["decision"] in ("passthrough", "already_rtk")
    assert not (root / "failure.json").exists()


def test_permission_denial_is_not_adapter_failure(hook):
    invoke, root = hook
    assert invoke("denied")["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not (root / "failure.json").exists()


@pytest.mark.parametrize("command", ["broken", "empty", "malformed", "slow", "oversized", "", 123])
def test_rewrite_failures_block_and_persist(hook, command):
    invoke, root = hook
    assert invoke(command)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (root / "failure.json").exists()


def test_session_start_load_and_missing_binary(hook):
    invoke, root = hook
    assert invoke(event="SessionStart") == {}
    assert json.loads((root / "loaded.json").read_text()) == {"harness": "claude-code", "version": "0.48.0"}
    assert invoke(event="SessionStart", ACB_RTK_BINARY=str(root / "missing"))["continue"] is False
    assert (root / "failure.json").exists()


@pytest.mark.parametrize("raw", ["{", "[]", "null"])
def test_invalid_json_stops_session(hook, raw):
    invoke, root = hook
    assert invoke(raw=raw)["continue"] is False
    assert (root / "failure.json").exists()


def test_interrupted_hook_is_collection_failure(monkeypatch, tmp_path):
    integration = rtk.RTKIntegration({"version": "0.48.0", "mode": "native"})
    integration.harness = "claude-code"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "loaded.json").write_text('{"harness":"claude-code","version":"0.48.0"}')
    (evidence / "decisions.jsonl").write_text('{"phase":"attempt","tool_call_id":"unfinished"}\n')
    monkeypatch.setattr(rtk, "container_cp_out", lambda *args: None)
    monkeypatch.setattr(rtk, "container_exec_capture", lambda *args, **kwargs: '{"summary":{"total_commands":0,"total_saved":0}}')
    with pytest.raises(IntegrationFailure, match="did not complete"):
        integration.collect(IntegrationContext("container", "arm64", "claude-code", "2.1.241", tmp_path, tmp_path))


def test_native_requires_validated_profile_and_version(tmp_path):
    binary = tmp_path / "rtk"
    binary.write_bytes(b"fake")
    integration = {"name": "rtk", "version": "0.48.0", "mode": "native", "experimental": True,
                   "binary_path": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    config = {"version": "2.1.241", "execution_integrations": [integration]}
    with pytest.raises(ValueError, match="launch_profile"):
        IntegrationManager("claude-code", config)
    lifecycle = IntegrationManager("claude-code", {**config, "launch_profile": "isolated-hooks"})
    assert lifecycle.entries[0].activate(None).claude_settings == ("/opt/acb/rtk/native/claude-settings.json",)
    with pytest.raises(ValueError, match="validated harness version"):
        IntegrationManager("claude-code", {**config, "launch_profile": "isolated-hooks", "version": "2.1.242"})


@pytest.mark.parametrize("enabled", [False, True])
def test_isolated_launch_parity(monkeypatch, tmp_path, enabled):
    adapter = claude_code.ClaudeCode({"launch_profile": "isolated-hooks", "system_prompt": "Keep this instruction"})
    if enabled:
        adapter.integration_activation = IntegrationActivation(claude_settings=("/opt/acb/rtk/native/claude-settings.json",))
    captured = []
    monkeypatch.setattr(claude_code, "execute", lambda command, **kwargs: captured.extend(command))
    env = adapter.build_container_env("http://praxis:8080", "fixture-key")
    adapter.run_container("Fix this", "container", "local-model", env, tmp_path, "fixture")
    argv = shlex.split(captured[-1].split("&& exec ", 1)[1])
    assert "--bare" not in argv
    assert argv[argv.index("--settings") + 1] == ("/opt/acb/rtk/native/claude-settings.json" if enabled else "{}")
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--tools") + 1] == "Bash,Edit,Read"
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "Keep this instruction" in argv
    assert env["ANTHROPIC_BASE_URL"] == "http://praxis:8080"
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_default_profile_remains_bare(monkeypatch, tmp_path):
    adapter = claude_code.ClaudeCode()
    captured = []
    monkeypatch.setattr(claude_code, "execute", lambda command, **kwargs: captured.extend(command))
    adapter.run_container("Fix this", "container", "local-model", {}, tmp_path, "fixture")
    assert "--bare" in shlex.split(captured[-1])
    assert "--settings" not in shlex.split(captured[-1])
    adapter.integration_activation = IntegrationActivation(claude_settings=("/opt/acb/rtk/native/claude-settings.json",))
    with pytest.raises(ValueError, match="launch_profile"):
        adapter.run_container("Fix this", "container", "local-model", {}, tmp_path, "fixture")
