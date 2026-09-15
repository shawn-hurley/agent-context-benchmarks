"""Opt-in tests of real pinned harnesses, offline, with deterministic API responses.

ACB_RTK_LIVE=1 ACB_RTK_BINARY=/path/to/linux/rtk uv run --with pytest python -m pytest tests/test_rtk_native_live.py -v
Set ACB_RTK_IMAGE to a locally available SWE-bench image with the testbed conda env.
Artifacts are retained under runs/rtk-native-smoke; only test-owned containers are removed.
"""
import hashlib
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from acb.harnesses import make_harness
from acb.integrations import IntegrationContext, IntegrationFailure, IntegrationManager
from acb.container import container_cp_in, container_cp_out, container_exec_capture

pytestmark = pytest.mark.skipif(os.environ.get("ACB_RTK_LIVE") != "1", reason="set ACB_RTK_LIVE=1 for isolated Podman compatibility tests")
REPO = Path(__file__).resolve().parents[1]


def podman(*args):
    return subprocess.run(["podman", *args], check=True, text=True, capture_output=True, timeout=60).stdout.strip()


@pytest.mark.parametrize("harness,version", [("goose", "1.50.0"), ("pi", "0.84.3"), ("opencode", "1.18.22"), ("claude-code", "2.1.241")])
def test_real_native_interception_and_model_context(harness, version):
    binary = Path(os.environ["ACB_RTK_BINARY"]).resolve()
    image = os.environ.get("ACB_RTK_IMAGE", "localhost/acb-rtk-smoke:0.48.0")
    arch = podman("image", "inspect", image, "--format", "{{.Architecture}}")
    results = {}
    for enabled in (False, True):
        name = "acb-rtk-smoke-" + uuid.uuid4().hex[:12]
        out = REPO / "runs/rtk-native-smoke" / harness / name
        out.mkdir(parents=True)
        config = {"version": version, "timeout": 90}
        if harness == "claude-code":
            config["launch_profile"] = "isolated-hooks"
        if enabled:
            config["execution_integrations"] = [{"name": "rtk", "version": "0.48.0", "mode": "shell-wrapper" if harness == "goose" else "native", "experimental": True,
                                                  "binary_path": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}]
        lifecycle = IntegrationManager(harness, config)
        podman("run", "-d", "--name", name, "--network", "none", image, "tail", "-f", "/dev/null")
        try:
            adapter = make_harness(harness, config)
            adapter.api = adapter.effective_api("openai")
            adapter.setup_container(name, arch, REPO / "runs/.cache")
            if harness == "claude-code":
                sentinel = out / "CLAUDE.md"
                sentinel.write_text("ACB_UNWANTED_PROJECT_PROMPT_SENTINEL")
                container_cp_in(name, sentinel, "/testbed/CLAUDE.md")
                container_exec_capture(name, ["mkdir", "-p", "/testbed/.claude"])
                settings = out / "unwanted-project-settings.json"
                settings.write_text(json.dumps({"permissions": {"deny": ["Bash(git *)"]}, "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "touch /tmp/acb-unwanted-project-hook"}]}]}}))
                container_cp_in(name, settings, "/testbed/.claude/settings.json")
            env = lifecycle.setup(IntegrationContext(name, arch, harness, version, REPO / "runs/.cache", out / "integrations"))
            adapter.integration_activation = lifecycle.activation
            container_cp_in(name, REPO / "tests/fixtures/rtk_openai_server.py", "/tmp/rtk-openai-server.py")
            podman("exec", "-d", name, "/opt/miniconda3/bin/python", "/tmp/rtk-openai-server.py")
            # Bounded server readiness, without introducing a host network dependency.
            container_exec_capture(name, ["/opt/miniconda3/bin/python", "-c", "import urllib.request,time\nfor _ in range(30):\n try: urllib.request.urlopen('http://127.0.0.1:18080',timeout=1); break\n except OSError: time.sleep(.1)\nelse: raise RuntimeError('fixture server did not start')"])
            env = {**adapter.build_container_env("http://127.0.0.1:18080", "fixture-key"), **env}
            # Disable remote catalog updates for both arms. Plugin code itself has no external imports.
            if harness == "opencode":
                env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
            model = "claude-sonnet-4-5" if harness == "claude-code" else "fixture-model"
            run = adapter.run_container("Execute the fixture commands provided by the API.", name, model, env, out, "fixture")
            assert not run.timed_out and run.exit_code == 0, run.output[-4000:]
            assert lifecycle.finish() == []
            container_cp_out(name, "/tmp/acb-rtk-requests.jsonl", out / "requests.jsonl")
            requests = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
            tool_results = [message for request in requests for message in request.get("messages", []) if message.get("role") == "tool"]
            if harness == "claude-code":
                tool_results = [block for request in requests for message in request.get("messages", []) for block in message.get("content", [])
                                if isinstance(block, dict) and block.get("type") == "tool_result"]
                assert "ACB_UNWANTED_PROJECT_PROMPT_SENTINEL" not in json.dumps(requests)
                container_exec_capture(name, ["test", "!", "-e", "/tmp/acb-unwanted-project-hook"])
                events = [json.loads(line) for line in (out / "transcript.jsonl").read_text().splitlines() if line.startswith("{")]
                init = next(event for event in events if event.get("subtype") == "init")
                assert set(init["tools"]) == {"Bash", "Edit", "Read"}
                assert init["mcp_servers"] == init["plugins"] == init["skills"] == []
            assert any("ACB_UNSUPPORTED" in json.dumps(message) for message in tool_results)
            assert any("ACB_STDERR" in json.dumps(message) for message in tool_results)
            assert container_exec_capture(name, ["cat", "/tmp/acb-rtk-once"]) == "ACB_ONCE\n"
            # First shell result must reach a later provider request in both arms.
            git_outputs = [message["content"] for message in tool_results if message.get("tool_call_id", message.get("tool_use_id")) == "fixture-call-0"]
            assert git_outputs
            length = len(json.dumps(git_outputs[-1]))
            results["rtk" if enabled else "baseline"] = {"output_bytes": length, "artifacts": str(out)}
            if enabled:
                manifest = json.loads((out / "integrations/rtk/manifest.json").read_text())
                activity = manifest["metadata"]["activity"]
                assert activity["agent_tool_verified"]
                if harness != "goose":
                    assert activity["adapter_loaded"]
                assert activity["commands_rewritten"] >= 1 and activity["passthroughs"] >= 2
                assert activity["errors"] == 0
                assert activity["compression_observed"] and activity["estimated_tokens_saved"] > 0
                # Verify the real native hook prevents original execution on a
                # failing rewrite, even when the harness treats it as a tool error.
                failure_out = out / "failure-check"
                failure_out.mkdir()
                broken_binary = failure_out / "rtk"
                broken_binary.write_text('#!/bin/bash\nif [[ "$1" == "--version" ]]; then echo "rtk 0.48.0"; exit 0; fi\nexit 9\n')
                container_cp_in(name, broken_binary, "/opt/acb/rtk/rtk")
                container_exec_capture(name, ["chmod", "+x", "/opt/acb/rtk/rtk"])
                failed_run = adapter.run_container("Execute the fixture commands provided by the API.", name, model, env, failure_out, "failure-fixture")
                assert not failed_run.timed_out
                assert container_exec_capture(name, ["cat", "/tmp/acb-rtk-once"]) == "ACB_ONCE\n"
                with pytest.raises(IntegrationFailure, match="adapter failure"):
                    # Keep deliberately failed collection metadata separate from
                    # the completed successful arm's manifest.
                    deepcopy(lifecycle.entries[0]).collect(replace(lifecycle.contexts["rtk"], artifact_dir=failure_out / "integrations/rtk"))
        finally:
            lifecycle.finish()
            podman("rm", "-f", name)
    assert results["rtk"]["output_bytes"] < results["baseline"]["output_bytes"], results
    (REPO / "runs/rtk-native-smoke" / harness / "latest.json").write_text(json.dumps(results, indent=2) + "\n")
