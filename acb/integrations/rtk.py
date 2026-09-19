"""Pinned RTK installation and reviewed harness execution adapters."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re
import shlex
import tempfile

from acb.container import container_cp_in, container_cp_out, container_exec_capture
from .base import Integration, IntegrationActivation, IntegrationContext, IntegrationFailure

SUPPORTED_NATIVE = {"pi": "0.84.3", "opencode": "1.18.22", "claude-code": "2.1.241"}
CLAUDE_PYTHON = "/opt/miniconda3/bin/python"

WRAPPER = r'''#!/bin/bash
# Goose also probes the login PATH. Only intercept its ordinary -c invocation.
if [[ $# -lt 2 || "$1" != "-c" ]]; then
    exec /bin/bash "$@"
fi
export PATH="${ACB_RTK_BINARY%/*}:$PATH"
original="$2"
shift 2
if [[ "${ACB_RTK_DISABLED:-0}" == "1" ]]; then
    exec /bin/bash -c "$original" "$@"
fi
rewritten="$("${ACB_RTK_BINARY:?}" rewrite "$original")"
rewrite_status=$?
case "$rewrite_status" in
    0|3)
        if [[ -z "$rewritten" ]]; then
            printf 'RTK returned an empty rewrite\n' >&2
            exit 125
        fi
        selected="$rewritten"
        decision=rewrite
        ;;
    1)
        selected="$original"
        decision=passthrough
        ;;
    2)
        decision=deny
        ;;
    *)
        decision=error
        ;;
esac
if [[ -n "${ACB_RTK_DECISIONS:-}" ]]; then
    printf '{"rewrite_status":%s,"decision":"%s"}\n' "$rewrite_status" "$decision" >> "$ACB_RTK_DECISIONS" || exit 125
fi
case "$decision" in
    deny) printf 'RTK permission rule denied this command\n' >&2; exit 126 ;;
    error) printf 'RTK rewrite failed (status %s)\n' "$rewrite_status" >&2; exit 125 ;;
esac
# Status 3 defers approval to the host. Goose authorized this shell call before
# invoking the wrapper; do not introduce a second, noninteractive approval flow.
exec /bin/bash -c "$selected" "$@"
'''


class RTKIntegration(Integration):
    name = "rtk"
    category = "execution_integrations"
    resources = frozenset({"shell"})
    root = "/opt/acb/rtk"

    def validate(self, harness: str, harness_config: dict) -> None:
        allowed = {"name", "version", "mode", "binary_path", "sha256", "experimental", "python_path"}
        if set(self.config) - allowed:
            raise ValueError(f"unknown RTK options: {sorted(set(self.config) - allowed)}")
        mode = self.config.get("mode")
        if not ((harness == "goose" and mode == "shell-wrapper") or (harness in SUPPORTED_NATIVE and mode == "native")):
            raise ValueError("RTK supports Goose shell-wrapper and validated Pi/OpenCode/Claude Code native modes only")
        if self.config.get("experimental") is not True:
            raise ValueError("RTK requires experimental: true")
        if not isinstance(harness_config.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", harness_config["version"]):
            raise ValueError("RTK experiments require an exact harness version")
        if mode == "native" and harness_config["version"] != SUPPORTED_NATIVE[harness]:
            raise ValueError(f"RTK native {harness} requires validated harness version {SUPPORTED_NATIVE[harness]}")
        if harness == "claude-code" and harness_config.get("launch_profile") != "isolated-hooks":
            raise ValueError("Claude Code RTK requires launch_profile: isolated-hooks in baseline and treatment")
        if not isinstance(self.config.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", self.config["version"]):
            raise ValueError("RTK requires an exact release version")
        if mode == "native" and self.config["version"] != "0.48.0":
            raise ValueError("RTK native adapters require validated RTK version 0.48.0")
        checksum = self.config.get("sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise ValueError("RTK requires a binary SHA-256 checksum")
        path = self.config.get("binary_path")
        if not isinstance(path, str) or not Path(path).expanduser().is_file():
            raise ValueError("RTK binary_path must point to a predownloaded target Linux binary")
        self.python_path = self.config.get("python_path", CLAUDE_PYTHON)
        if not isinstance(self.python_path, str) or not self.python_path.startswith("/"):
            raise ValueError("RTK python_path must be an absolute interpreter path")
        self.harness = harness
        self.harness_version = harness_config["version"]

    def install(self, context: IntegrationContext) -> None:
        binary = Path(self.config["binary_path"]).expanduser().resolve()
        # Verify the exact bytes copied, rather than reopening a mutable source.
        data = binary.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.config["sha256"].lower():
            raise ValueError("RTK binary checksum mismatch")
        if (context.harness, context.harness_version) != (self.harness, self.harness_version):
            raise ValueError("RTK context does not match validated harness/version")
        expected_machine = {"arm64": 183, "aarch64": 183, "x86_64": 62, "amd64": 62, "x64": 62}.get(context.arch)
        if expected_machine is None or len(data) < 20 or data[:6] != b"\x7fELF\x02\x01" or int.from_bytes(data[18:20], "little") != expected_machine:
            raise ValueError("RTK binary must be a target-architecture Linux ELF executable")
        container_exec_capture(context.container, ["mkdir", "-p", self.root + "/shell", self.root + "/native", self.root + "/evidence"])
        asset_hashes = {}
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "rtk"
            staged.write_bytes(data)
            container_cp_in(context.container, staged, self.root + "/rtk")
            wrapper = Path(tmp) / "bash"
            wrapper.write_text(WRAPPER)
            container_cp_in(context.container, wrapper, self.root + "/shell/bash")
            if self.config["mode"] == "native":
                names = ("claude_hook.py",) if self.harness == "claude-code" else ("common.mjs", self.harness + ".ts")
                for name in names:
                    asset = files("acb.integrations").joinpath("assets", "rtk", name).read_bytes()
                    staged_asset = Path(tmp) / name
                    staged_asset.write_bytes(asset)
                    container_cp_in(context.container, staged_asset, self.root + "/native/" + name)
                    asset_hashes[name] = hashlib.sha256(asset).hexdigest()
                if self.harness == "claude-code":
                    command = shlex.quote(self.python_path) + " " + self.root + "/native/claude_hook.py"
                    hooks = {event: [{**({"matcher": "Bash"} if event != "SessionStart" else {}),
                                      "hooks": [{"type": "command", "command": command, "timeout": 5}]}]
                             for event in ("SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure")}
                    settings = json.dumps({"hooks": hooks}, sort_keys=True).encode()
                    staged_settings = Path(tmp) / "claude-settings.json"
                    staged_settings.write_bytes(settings)
                    container_cp_in(context.container, staged_settings, self.root + "/native/claude-settings.json")
                    asset_hashes["claude-settings.json"] = hashlib.sha256(settings).hexdigest()
        container_exec_capture(context.container, ["chmod", "+x", self.root + "/rtk", self.root + "/shell/bash"])
        version = container_exec_capture(context.container, [self.root + "/rtk", "--version"]).strip()
        if version != "rtk " + self.config["version"]:
            raise ValueError(f"RTK version mismatch: {version!r}")
        if self.config["mode"] == "native":
            harness_binary = "claude" if self.harness == "claude-code" else self.harness
            actual_version = container_exec_capture(context.container, ["/usr/local/bin/" + harness_binary, "--version"]).strip()
            if not re.search(r"(?<![\d.])" + re.escape(self.harness_version) + r"(?![\d.])", actual_version):
                raise ValueError(f"installed {self.harness} version mismatch: {actual_version!r}")
            if self.harness == "opencode":
                runtime = json.loads(container_exec_capture(context.container, ["cat", "/root/.config/opencode/acb-runtime-manifest.json"], log_output=False))
                lock = files("acb.harnesses").joinpath("assets", "opencode-plugin-runtime-1.18.22.lock.json").read_bytes()
                if runtime.get("version") != self.harness_version or runtime.get("lock_sha256") != hashlib.sha256(lock).hexdigest():
                    raise ValueError("OpenCode plugin runtime lock/version mismatch")
                self.metadata["plugin_runtime"] = runtime
            if self.harness == "claude-code":
                container_exec_capture(context.container, [self.python_path, "-c", "import json,selectors,subprocess,hashlib"])
                container_exec_capture(context.container, ["/bin/bash", "-c",
                    'if test -e /usr/local/bin/rtk || test -L /usr/local/bin/rtk; then '
                    'test "$(readlink /usr/local/bin/rtk)" = /opt/acb/rtk/rtk; '
                    'else ln -s /opt/acb/rtk/rtk /usr/local/bin/rtk; fi'])
                self.metadata.update(launch_profile="isolated-hooks", hook_interpreter=self.python_path,
                                     rtk_path_activation="/usr/local/bin/rtk")
        container_exec_capture(context.container, ["/bin/bash", "-n", self.root + "/shell/bash"])
        self.metadata.update(version=self.config["version"], sha256=digest,
                             wrapper_sha256=hashlib.sha256(WRAPPER.encode()).hexdigest(),
                             mode=self.config["mode"], experimental=True,
                             adapter_revision=1, asset_sha256=asset_hashes,
                             interception_scope="Goose developer shell calls only" if self.harness == "goose" else f"{self.harness} bash tool only")

    def activate(self, context: IntegrationContext) -> IntegrationActivation | dict[str, str]:
        env = {"ACB_RTK_BINARY": self.root + "/rtk",
                "ACB_RTK_DECISIONS": self.root + "/evidence/decisions.jsonl",
                "RTK_DB_PATH": self.root + "/evidence/tracking.db"}
        if self.harness == "goose":
            return {**env, "GOOSE_SHELL": self.root + "/shell/bash"}
        env.update(ACB_RTK_VERSION=self.config["version"],
                   ACB_RTK_FAILURE=self.root + "/evidence/failure.json",
                   ACB_RTK_LOADED=self.root + "/evidence/loaded.json")
        path = self.root + "/native/" + self.harness + ".ts"
        if self.harness == "claude-code":
            path = self.root + "/native/claude-settings.json"
            env["ACB_RTK_REWRITE_DIR"] = self.root + "/native"
        self.metadata["activation_asset"] = path
        return IntegrationActivation(env=env, pi_extensions=(path,) if self.harness == "pi" else (),
                                     opencode_plugins=(path,) if self.harness == "opencode" else (),
                                     claude_settings=(path,) if self.harness == "claude-code" else ())

    def verify(self, context: IntegrationContext) -> dict:
        # Exercise the installed wrapper, using separate tracking from generation.
        activation = self.activate(context)
        env = dict(activation.env if isinstance(activation, IntegrationActivation) else activation)
        env["GOOSE_SHELL"] = self.root + "/shell/bash"
        env["ACB_RTK_DECISIONS"] = self.root + "/preflight-decisions.jsonl"
        env["RTK_DB_PATH"] = self.root + "/preflight.db"
        # Rewritten command spells `rtk`, so make its directory available on PATH.
        script = "export PATH=" + shlex.quote(self.root) + ':"$PATH"; exec ' + shlex.quote(env["GOOSE_SHELL"]) + " -c 'printf acb-preflight'"
        if self.harness == "claude-code":
            script = "cd " + shlex.quote(self.root + "/native") + "; " + script
        cmd = ["env", *[f"{k}={v}" for k, v in env.items()], "/bin/bash", "-c", script]
        output = container_exec_capture(context.container, cmd)
        # Tasks need not contain Git. Exercise a rewrite using the adapter's own
        # directory, without installing tools or modifying the task workspace.
        status_script = 'export PATH=' + shlex.quote(self.root) + ':"$PATH"; cd ' + shlex.quote(self.root) + '; exec ' + shlex.quote(env["GOOSE_SHELL"]) + " -c 'ls'"
        compact_output = container_exec_capture(context.container, ["env", *[f"{k}={v}" for k, v in env.items()], "/bin/bash", "-c", status_script])
        evidence = container_exec_capture(context.container, ["cat", env["ACB_RTK_DECISIONS"]])
        records = [json.loads(line) for line in evidence.splitlines() if line]
        if not records or records[-1]["decision"] != "rewrite":
            raise RuntimeError("RTK preflight did not rewrite ls")
        return {"installed_wrapper": self.harness == "goose", "installed_binary": True,
                "adapter_loaded": False, "rewrite_observed": True,
                "agent_tool_verified": False, "preflight_output": output.strip(),
                "preflight_ls": compact_output.strip()}

    def collect(self, context: IntegrationContext) -> None:
        context.artifact_dir.mkdir(parents=True, exist_ok=True)
        # Copy the tracking database and decision log even if gain export fails.
        container_cp_out(context.container, self.root + "/evidence", context.artifact_dir / "evidence")
        evidence_dir = context.artifact_dir / "evidence"
        decisions_path = evidence_dir / "decisions.jsonl"
        decisions = [json.loads(line) for line in decisions_path.read_text().splitlines() if line] if decisions_path.exists() else []
        rewrites = [item for item in decisions if item.get("decision") == "rewrite"]
        result_ids = {item.get("tool_call_id") for item in decisions if item.get("phase") == "result"}
        native = self.config["mode"] == "native"
        agent_verified = any(item.get("tool_call_id") in result_ids for item in rewrites) if native else bool(rewrites)
        loaded_path = evidence_dir / "loaded.json"
        loaded = json.loads(loaded_path.read_text()) if loaded_path.exists() else None
        self.metadata["activity"] = {
            "adapter_loaded": bool(loaded) if native else None,
            "agent_tool_verified": agent_verified,
            "shell_calls_seen": sum(item.get("phase") == "decision" or "phase" not in item for item in decisions),
            "commands_rewritten": len(rewrites),
            "results_observed": len(result_ids),
            "passthroughs": sum(item.get("decision") in ("passthrough", "already_rtk") for item in decisions),
            "denials": sum(item.get("decision") == "deny" for item in decisions),
            "errors": sum(item.get("decision") == "error" for item in decisions),
        }
        failures = []
        attempted_ids = {item.get("tool_call_id") for item in decisions if item.get("phase") == "attempt"}
        decided_ids = {item.get("tool_call_id") for item in decisions if item.get("phase") == "decision"}
        if attempted_ids - decided_ids:
            failures.append("RTK hook did not complete a command decision")
        if (evidence_dir / "failure.json").exists() or self.metadata["activity"]["errors"]:
            failures.append("RTK recorded an adapter failure during generation")
        if native and loaded != {"harness": self.harness, "version": self.config["version"]}:
            failures.append("RTK native adapter did not load with the expected harness/version")
        try:
            output = container_exec_capture(context.container,
                ["env", "RTK_DB_PATH=" + self.root + "/evidence/tracking.db",
                 self.root + "/rtk", "gain", "--all", "--format", "json"], log_output=False)
            parsed = json.loads(output)
            (context.artifact_dir / "gain.json").write_text(json.dumps(parsed, indent=2) + "\n")
        except Exception as exc:
            if failures:
                raise IntegrationFailure("; ".join(failures) + f"; gain export failed: {exc}") from exc
            raise
        self.metadata["activity"]["rtk_gain"] = parsed
        saved = parsed.get("summary", {}).get("total_saved", 0)
        tracked = parsed.get("summary", {}).get("total_commands", 0)
        agent_verified = agent_verified and tracked > 0 and (not native or bool(loaded))
        self.metadata["activity"].update(agent_tool_verified=agent_verified,
                                        rtk_commands_tracked=tracked,
                                        estimated_tokens_saved=saved,
                                        compression_observed=agent_verified and saved > 0)
        if failures:
            raise IntegrationFailure("; ".join(failures))
