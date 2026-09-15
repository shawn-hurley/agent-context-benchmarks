"""Claude hook JSON protocol over pinned RTK rewrite; Python stdlib only."""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time


def record(event):
    with Path(os.environ["ACB_RTK_DECISIONS"]).open("a") as log:
        log.write(json.dumps(event) + "\n")


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def run(args):
    # This profile omits project permission settings. RTK's raw rewrite command
    # otherwise reads those settings from cwd. Only execution retains the native
    # tool's cwd; rewriting runs in our isolated directory with isolated settings.
    child = subprocess.Popen([os.environ["ACB_RTK_BINARY"], *args],
                             cwd=os.environ["ACB_RTK_REWRITE_DIR"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    chunks = []
    size = 0
    deadline = time.monotonic() + 2
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("RTK rewrite timed out")
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 1024 * 1024:
                    raise ValueError("RTK output exceeded limit")
                chunks.append(chunk)
        code = child.wait(timeout=max(0, deadline - time.monotonic()))
        return code, b"".join(chunks).decode("utf-8")
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        child.stdout.close()


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


def handle(event):
    name = event["hook_event_name"]
    if name == "SessionStart":
        if os.environ.get("RTK_DISABLED") == "1" or os.environ.get("ACB_RTK_DISABLED") == "1":
            raise ValueError("RTK cannot be disabled in a required treatment")
        code, version = run(["--version"])
        if code != 0 or version.strip() != "rtk " + os.environ["ACB_RTK_VERSION"]:
            raise ValueError("RTK executable version mismatch")
        Path(os.environ["ACB_RTK_LOADED"]).write_text(json.dumps({"harness": "claude-code", "version": os.environ["ACB_RTK_VERSION"]}) + "\n")
        return {}
    if event.get("tool_name") != "Bash":
        return {}
    call_id = event["session_id"] + ":" + event["tool_use_id"]
    if name in ("PostToolUse", "PostToolUseFailure"):
        output = json.dumps(event.get("tool_response", event.get("error", "")), sort_keys=True)
        record({"phase": "result", "tool_call_id": call_id, "output_sha256": fingerprint(output),
                "output_bytes": len(output.encode()), "is_error": name == "PostToolUseFailure"})
        return {}
    if name != "PreToolUse":
        return {}
    record({"phase": "attempt", "tool_call_id": call_id})
    inputs = event["tool_input"]
    command = inputs["command"]
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Shell command must be a nonempty string")
    decision = {"phase": "decision", "tool_call_id": call_id, "original_sha256": fingerprint(command)}
    if re.match(r"^\s*rtk(?:\s|$)", command) or command.lstrip().startswith(os.environ["ACB_RTK_BINARY"] + " "):
        record({**decision, "decision": "already_rtk"})
        return {}
    code, selected = run(["rewrite", command])
    decision["rewrite_status"] = code
    if code == 1 and not selected.strip():
        record({**decision, "decision": "passthrough"})
        return {}
    if code == 2:
        record({**decision, "decision": "deny"})
        return deny("RTK permission rule denied the command")
    selected = selected.strip()
    if code not in (0, 3) or not selected:
        raise ValueError(f"Invalid RTK rewrite response (status {code})")
    record({**decision, "decision": "rewrite", "rewritten_sha256": fingerprint(selected)})
    # No auto-allow: Claude evaluates the updated input under its native rules.
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**inputs, "command": selected}}}


def main():
    event = {}
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Hook input exceeded limit")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Hook input must be a JSON object")
        event = parsed
        response = handle(event)
    except Exception as error:
        reason = "Required RTK integration failed: " + str(error)
        Path(os.environ["ACB_RTK_FAILURE"]).write_text(json.dumps({"error": reason}) + "\n")
        record({"phase": "decision", "decision": "error",
                "tool_call_id": str(event.get("session_id")) + ":" + str(event.get("tool_use_id"))})
        response = deny(reason) if event.get("hook_event_name") == "PreToolUse" else {"continue": False, "stopReason": reason}
    print(json.dumps(response))


if __name__ == "__main__":
    main()
