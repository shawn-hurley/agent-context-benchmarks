"""Goose harness adapter.

Runs Goose headless (`goose run -t <prompt> --no-session`) via `podman exec`
inside the SWE-bench eval container, pointed at the (also containerized)
proxy. The only harness with a container-mode port today -- see
`ensure_linux_binary()` below for how a Linux build gets into the image.

NOTE: verified against goose 1.46.0 (real binary, not just docs). Goose does
*not* honor the generic ANTHROPIC_BASE_URL/OPENAI_BASE_URL convention used by
other harnesses -- its provider system reads its own env vars per-provider:

* openai provider    -> OPENAI_HOST (base URL) + OPENAI_API_KEY
* anthropic provider -> ANTHROPIC_HOST (base URL) + ANTHROPIC_API_KEY

and which provider it talks to is selected via GOOSE_PROVIDER / --provider,
not inferred from which env vars are set. This adapter targets the `openai`
provider (matches locally-served OpenAI-compatible models, e.g. vLLM/Ollama,
which is what Praxis's cross-API-translation limitation restricts us to for
local runs -- see acb/proxy/praxis.py). Wiring up the `anthropic` provider
would mean setting api = "anthropic" and swapping OPENAI_HOST for
ANTHROPIC_HOST below.

Progress visibility: `--no-session` means goose persists no transcript of its
own (that's normally what its sqlite session store is for); combined with
generation often taking minutes, a plain `subprocess.run(capture_output=True)`
would leave the console silent the whole time. Instead this adapter:

* uses `--output-format stream-json` so goose emits one JSON event per
  message/tool-call/tool-result as they happen instead of a single blob at
  the end (verified event shapes: `{"type": "message", "message": {"role":
  ..., "content": [{"type": "text"|"toolRequest"|"toolResponse", ...}]}}`
  and a final `{"type": "complete", ...}`),
* streams stdout live via Popen and writes every raw line to
  `<out_dir>/goose/<instance_id>/transcript.jsonl` as a durable record,
* prints a heartbeat roughly every HEARTBEAT_INTERVAL seconds summarizing the
  most recent activity, instead of either per-line spam or total silence.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

from acb.harnesses.base import HarnessAdapter, HarnessResult

HEARTBEAT_INTERVAL = 10  # seconds
ACTIVITY_TEXT_TAIL = 160  # chars of rolling assistant-text preview to keep

# goose ships static-ish Linux release binaries here (verified against 1.46/
# 1.47); used to get goose into a SWE-bench testbed container, which has no
# package manager entry for it.
_LINUX_RELEASE_URL = (
    "https://github.com/aaif-goose/goose/releases/download/stable/"
    "goose-{arch}-unknown-linux-gnu.tar.bz2"
)
_ARCH_ALIASES = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64", "x86_64": "x86_64"}


def ensure_linux_binary(arch: str, cache_dir: Path) -> Path:
    """Download (once, cached) a Linux goose binary for ``arch``; return its path."""
    goose_arch = _ARCH_ALIASES.get(arch, arch)
    dest_dir = cache_dir / f"goose-{goose_arch}-unknown-linux-gnu"
    dest = dest_dir / "goose"
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = _LINUX_RELEASE_URL.format(arch=goose_arch)
    print(f"[goose] downloading Linux binary for {goose_arch} from {url} ...", flush=True)
    archive_path = dest_dir / "goose.tar.bz2"
    urllib.request.urlretrieve(url, archive_path)  # noqa: S310
    with tarfile.open(archive_path) as tf:
        tf.extractall(dest_dir)  # noqa: S202
    archive_path.unlink()
    dest.chmod(0o755)
    return dest


def _describe_event(obj: dict) -> tuple[str | None, bool]:
    """Extract a short activity description from a parsed stream-json event.

    Returns (description, is_text_delta). is_text_delta is True when
    `description` is a fragment of assistant text that should be accumulated
    into a rolling preview rather than shown verbatim (goose streams text
    token-by-token, so individual fragments like " to" aren't informative on
    their own). Returns (None, False) for event shapes we don't recognize.
    """
    etype = obj.get("type")
    if etype == "complete":
        return "finalizing response", False
    if etype != "message":
        return None, False
    message = obj.get("message") or {}
    role = message.get("role")
    for item in message.get("content") or []:
        itype = item.get("type")
        if itype == "toolRequest":
            name = ((item.get("toolCall") or {}).get("value") or {}).get("name")
            return (f"running tool: {name}" if name else "running tool"), False
        if itype == "toolResponse":
            result = (item.get("toolResult") or {}).get("value") or {}
            return ("tool call failed" if result.get("isError") else "tool call finished"), False
        if itype == "text" and role == "assistant":
            return item.get("text") or "", True
    return None, False


class _RunState:
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


def _read_stream(proc: subprocess.Popen, transcript_path: Path,
                  state: _RunState, buffer: list[str]) -> None:
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
            description, is_text_delta = _describe_event(obj)
            if is_text_delta:
                state.note_text(description or "")
            elif description is not None:
                state.note_event(description)
            else:
                state.note_event("processing...")


class Goose(HarnessAdapter):
    name = "goose"
    api = "openai"

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str,
                       binary: str = "/usr/local/bin/goose") -> HarnessResult:
        """Exec the harness inside a running container via `podman exec`.

        `podman exec` doesn't take a Python env dict for the exec'd process
        (only for the `podman` CLI invocation itself); env vars are passed as
        repeated `-e KEY=VALUE` flags instead.
        
        Injects goose configuration to disable internet search before execution.
        """
        # Inject goose config to disable fetch extension (internet search)
        self._inject_goose_config(container)

        goose_argv = [
            binary, "run",
            "-t", prompt,
            "--provider", "openai",
            "--model", model,
            "--no-session",
            "--output-format", "stream-json",
        ]
        if system_prompt := self.config.get("system_prompt"):
            goose_argv += ["--system", system_prompt]
        if max_reps := self.config.get("max_tool_repetitions"):
            goose_argv += ["--max-tool-repetitions", str(max_reps)]

        exec_cmd = ["podman", "exec", "-i"]
        for key, value in env.items():
            exec_cmd += ["-e", f"{key}={value}"]
        # SWE-bench's own eval.sh activates the instance's conda env
        # (`source /opt/miniconda3/bin/activate && conda activate testbed`)
        # before running anything -- verified this container does not do the
        # same for us by default: `podman exec <container> python3` resolves
        # to the *base* conda env's Python (3.11, nothing installed) rather
        # than testbed's (3.9, where the repo and its test deps actually
        # live), because `podman exec` runs the given argv directly rather
        # than through a login/interactive shell that would source
        # `/root/.bashrc` (which the image sets up to auto-activate
        # testbed). Sourcing the conda activation script explicitly here
        # means goose itself -- and therefore every shell-tool child process
        # it spawns -- inherits the correct environment, without depending
        # on shell-invocation semantics (`-l` sources login files, not
        # `.bashrc`; `.bashrc` itself is normally only sourced for
        # interactive shells) that could silently stop applying.
        activate = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed"
        inner = " ".join(shlex.quote(a) for a in goose_argv)
        exec_cmd += [
            "--workdir", "/testbed", container,
            "bash", "-c", f"{activate} && exec {inner}",
        ]
        transcript_path = Path(out_dir) / "goose" / instance_id / "transcript.jsonl"
        label = f"[goose:{instance_id}]"
        timeout = self.config.get("timeout", 1800)
        return self._execute(exec_cmd, env=None, cwd=None,
                             transcript_path=transcript_path, label=label, timeout=timeout)

    def build_container_env(self, base_url: str, api_key: str) -> dict[str, str]:
        """Minimal env for `run_container` -- no host env inherited (irrelevant/huge)."""
        return {
            "HOME": "/root",
            "OPENAI_HOST": base_url,
            "OPENAI_API_KEY": api_key,
            "GOOSE_PROVIDER": "openai",
            "GOOSE_TELEMETRY_ENABLED": "false",
            "GOOSE_MODE": "auto",  # Auto mode with config-based tool restrictions
        }

    @staticmethod
    def _inject_goose_config(container: str) -> None:
        """Inject goose configuration file into container to disable internet search.
        
        Copies the goose-container-config.yaml from the harness directory into the
        container at ~/.config/goose/config.yaml to ensure the fetch extension
        (internet search) is disabled.
        """
        config_src = Path(__file__).parent.parent / "goose-container-config.yaml"
        config_dst = "/root/.config/goose/config.yaml"
        
        if not config_src.exists():
            print(f"[goose] warning: config file not found at {config_src}, skipping injection",
                  flush=True)
            return
        
        try:
            # Ensure target directory exists in container
            subprocess.run(
                ["podman", "exec", "-u", "root", container, "mkdir", "-p", "/root/.config/goose"],
                check=True,
                capture_output=True,
            )
            # Copy config file into container
            subprocess.run(
                ["podman", "cp", str(config_src), f"{container}:{config_dst}"],
                check=True,
                capture_output=True,
            )
            print(f"[goose] injected config to {container}:{config_dst}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"[goose] warning: failed to inject config: {e}", flush=True)

    @staticmethod
    def _execute(cmd: list[str], env: dict[str, str] | None, cwd: str | None,
                 transcript_path: Path, label: str, timeout: int) -> HarnessResult:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        state = _RunState()
        buffer: list[str] = []
        reader = threading.Thread(
            target=_read_stream, args=(proc, transcript_path, state, buffer),
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
