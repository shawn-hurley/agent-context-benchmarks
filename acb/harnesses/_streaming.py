"""Shared subprocess-streaming/heartbeat plumbing for container-mode harnesses.

Both goose and claude-code run headless via `podman exec`, emit one JSON
event per line (`--output-format stream-json`), and can run for minutes with
no other output in between -- this module owns the common "tail stdout,
persist every line as a durable transcript, print a periodic heartbeat
summarizing the most recent activity instead of either per-line spam or
total silence" behavior. Each harness only needs to supply its own
``describe_event()`` -- their event shapes differ entirely (goose's message/
toolRequest/toolResponse vs Claude Code's assistant/user/tool_use/
tool_result) -- see acb/harnesses/goose.py and acb/harnesses/claude_code.py
for each's own, and acb/runner.py for where `run_container()` (which calls
`execute()` below) fits into the wider per-instance flow.

Extracted from acb/harnesses/goose.py (the original, and until claude-code,
only container-mode harness) when claude-code needed the identical pattern;
behavior for goose is unchanged by this move.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from acb.harnesses.base import HarnessResult

HEARTBEAT_INTERVAL = 10  # seconds
ACTIVITY_TEXT_TAIL = 160  # chars of rolling assistant-text preview to keep

# Given one parsed stream-json event, return (description, is_text_delta).
# is_text_delta is True when `description` is a fragment of assistant text
# that should be accumulated into a rolling preview rather than shown
# verbatim (goose streams text token-by-token, so individual fragments like
# " to" aren't informative on their own; claude-code's non-partial-message
# events are always whole messages, so its own describe_event always
# returns False here). Returns (None, False) for event shapes a harness
# doesn't recognize.
DescribeEvent = Callable[[dict], tuple[str | None, bool]]


class RunState:
    """Activity state shared between the reader thread and the heartbeat loop.

    Plain attribute assignment is safe across threads under the GIL for this
    use case (single reader, single reader-of-the-latest-value) -- no lock
    needed for a "best effort, latest wins" heartbeat.
    """

    def __init__(self) -> None:
        self.last_activity = "starting..."
        self._text_accum = ""

    def note_text(self, delta: str) -> None:
        self._text_accum = (self._text_accum + delta)[-ACTIVITY_TEXT_TAIL:]
        preview = self._text_accum.strip()
        self.last_activity = f"thinking: {preview}" if preview else "thinking..."

    def note_event(self, description: str) -> None:
        self._text_accum = ""
        self.last_activity = description


def read_stream(proc: subprocess.Popen, transcript_path: Path,
                 state: RunState, buffer: list[str], describe_event: DescribeEvent) -> None:
    """Tail the harness's stdout: persist every line, update `state` live."""
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    with transcript_path.open("w", encoding="utf-8") as tf:
        for line in proc.stdout:  # type: ignore[union-attr]
            buffer.append(line)
            tf.write(line)
            tf.flush()
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                # startup banner / non-JSON noise -- not an error, just opaque
                state.note_event("processing...")
                continue
            if not isinstance(obj, dict):
                state.note_event("processing...")
                continue
            description, is_text_delta = describe_event(obj)
            if is_text_delta:
                state.note_text(description or "")
            elif description is not None:
                state.note_event(description)
            else:
                state.note_event("processing...")


def execute(cmd: list[str], env: dict[str, str] | None, cwd: str | None,
            transcript_path: Path, label: str, timeout: int,
            describe_event: DescribeEvent) -> HarnessResult:
    """Run `cmd` to completion, streaming its stdout live with a heartbeat.

    Shared by every container-mode harness's `run_container()`.
    """
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    state = RunState()
    buffer: list[str] = []
    reader = threading.Thread(
        target=read_stream, args=(proc, transcript_path, state, buffer, describe_event),
        daemon=True,
    )
    reader.start()

    start = time.monotonic()
    timed_out = False
    while True:
        try:
            proc.wait(timeout=HEARTBEAT_INTERVAL)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                proc.kill()
                proc.wait()
                timed_out = True
                break
            print(f"{label} still working ({elapsed:.0f}s elapsed) "
                  f"-- last: {state.last_activity}", flush=True)

    reader.join(timeout=5)
    output = "".join(buffer)
    if timed_out:
        print(f"{label} timed out after {timeout}s", flush=True)
        return HarnessResult(output=output, exit_code=-1, timed_out=True)
    print(f"{label} finished (exit code {proc.returncode})", flush=True)
    return HarnessResult(output=output, exit_code=proc.returncode)
