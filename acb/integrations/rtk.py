"""Experimental Goose integration using RTK's subprocess rewrite protocol."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
import tempfile

from acb.container import container_cp_in, container_cp_out, container_exec_capture
from .base import Integration, IntegrationContext

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
        allowed = {"name", "version", "mode", "binary_path", "sha256", "experimental"}
        if set(self.config) - allowed:
            raise ValueError(f"unknown RTK options: {sorted(set(self.config) - allowed)}")
        if harness != "goose" or self.config.get("mode") != "shell-wrapper":
            raise ValueError("RTK currently supports Goose shell-wrapper only; native adapters await validation")
        if self.config.get("experimental") is not True:
            raise ValueError("RTK shell-wrapper requires experimental: true until agent compatibility is validated")
        if not isinstance(harness_config.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", harness_config["version"]):
            raise ValueError("RTK experiments require an exact Goose version")
        if not isinstance(self.config.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", self.config["version"]):
            raise ValueError("RTK requires an exact release version")
        checksum = self.config.get("sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise ValueError("RTK requires a binary SHA-256 checksum")
        path = self.config.get("binary_path")
        if not isinstance(path, str) or not Path(path).expanduser().is_file():
            raise ValueError("RTK binary_path must point to a predownloaded target Linux binary")

    def install(self, context: IntegrationContext) -> None:
        binary = Path(self.config["binary_path"]).expanduser().resolve()
        # Verify the exact bytes copied, rather than reopening a mutable source.
        data = binary.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.config["sha256"].lower():
            raise ValueError("RTK binary checksum mismatch")
        container_exec_capture(context.container, ["mkdir", "-p", self.root + "/shell", self.root + "/evidence"])
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "rtk"
            staged.write_bytes(data)
            container_cp_in(context.container, staged, self.root + "/rtk")
            wrapper = Path(tmp) / "bash"
            wrapper.write_text(WRAPPER)
            container_cp_in(context.container, wrapper, self.root + "/shell/bash")
        container_exec_capture(context.container, ["chmod", "+x", self.root + "/rtk", self.root + "/shell/bash"])
        version = container_exec_capture(context.container, [self.root + "/rtk", "--version"]).strip()
        if version != "rtk " + self.config["version"]:
            raise ValueError(f"RTK version mismatch: {version!r}")
        container_exec_capture(context.container, ["/bin/bash", "-n", self.root + "/shell/bash"])
        self.metadata.update(version=self.config["version"], sha256=digest,
                             wrapper_sha256=hashlib.sha256(WRAPPER.encode()).hexdigest(),
                             mode="shell-wrapper", experimental=True,
                             interception_scope="Goose developer shell calls only")

    def activate(self, context: IntegrationContext) -> dict[str, str]:
        return {"GOOSE_SHELL": self.root + "/shell/bash",
                "ACB_RTK_BINARY": self.root + "/rtk",
                "ACB_RTK_DECISIONS": self.root + "/evidence/decisions.jsonl",
                "RTK_DB_PATH": self.root + "/evidence/tracking.db"}

    def verify(self, context: IntegrationContext) -> dict:
        # Exercise the installed wrapper, using separate tracking from generation.
        env = self.activate(context)
        env["ACB_RTK_DECISIONS"] = self.root + "/preflight-decisions.jsonl"
        env["RTK_DB_PATH"] = self.root + "/preflight.db"
        # Rewritten command spells `rtk`, so make its directory available on PATH.
        script = "export PATH=" + shlex.quote(self.root) + ':"$PATH"; exec ' + shlex.quote(env["GOOSE_SHELL"]) + " -c 'git --version'"
        cmd = ["env", *[f"{k}={v}" for k, v in env.items()], "/bin/bash", "-c", script]
        output = container_exec_capture(context.container, cmd)
        # git --version may pass through; use a temporary repository for status.
        status_script = 'export PATH=' + shlex.quote(self.root) + ':"$PATH"; repo=$(mktemp -d); git -C "$repo" init -q; cd "$repo"; exec ' + shlex.quote(env["GOOSE_SHELL"]) + " -c 'git status'"
        compact_output = container_exec_capture(context.container, ["env", *[f"{k}={v}" for k, v in env.items()], "/bin/bash", "-c", status_script])
        evidence = container_exec_capture(context.container, ["cat", env["ACB_RTK_DECISIONS"]])
        records = [json.loads(line) for line in evidence.splitlines() if line]
        if not records or records[-1]["decision"] != "rewrite":
            raise RuntimeError("RTK preflight did not rewrite git status")
        return {"installed_wrapper": True, "rewrite_observed": True,
                "agent_tool_verified": False, "preflight_output": output.strip(),
                "preflight_git_status": compact_output.strip()}

    def collect(self, context: IntegrationContext) -> None:
        context.artifact_dir.mkdir(parents=True, exist_ok=True)
        # Copy the tracking database and decision log even if gain export fails.
        container_cp_out(context.container, self.root + "/evidence", context.artifact_dir / "evidence")
        output = container_exec_capture(context.container,
            ["env", "RTK_DB_PATH=" + self.root + "/evidence/tracking.db",
             self.root + "/rtk", "gain", "--all", "--format", "json"], log_output=False)
        parsed = json.loads(output)
        (context.artifact_dir / "gain.json").write_text(json.dumps(parsed, indent=2) + "\n")
