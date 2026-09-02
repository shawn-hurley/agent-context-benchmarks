"""Pi (pi.dev) harness adapter.

Runs pi headless (`pi --mode json --no-session`) via `podman exec` inside the
SWE-bench eval container -- same container-mode shape as goose, claude-code,
and opencode.

Binary distribution: despite the earlier assumption that pi ships only as an
npm package (@earendil-works/pi-coding-agent), GitHub Releases also publish
standalone Linux binaries:
    pi-linux-arm64.tar.gz / pi-linux-x64.tar.gz

The tarball layout is a `pi/` directory containing:
  pi/pi                   -- the main executable (100MB ELF, Node.js SEA)
  pi/node_modules/        -- native addons (e.g. @mariozechner/clipboard)
  pi/photon_rs_bg.wasm    -- WASM for image rendering
  pi/export-html/         -- HTML export assets

The whole `pi/` directory is needed because the binary loads native addons
from `node_modules/` at runtime. Container-mode copies the entire directory
to `/opt/pi/` in the container and symlinks `/usr/local/bin/pi → /opt/pi/pi`.

Provider/model configuration: pi reads `PI_CODING_AGENT_DIR/models.json` for
provider/model overrides. We set `PI_CODING_AGENT_DIR=/tmp/pi-agent` via env
var and write models.json into the container in `run_container()` (not
`build_container_env()`, because the model name is only available at run time).

Two provider strategies, matching the approach used for opencode:

- api="openai" (local vLLM/Ollama): override the built-in `openai` provider's
  baseUrl to the proxy and register the model. Request body sends the exact
  model ID that the backend expects. baseUrl includes `/v1` since pi's
  openai-completions API appends `/chat/completions` directly.
  Invocation: pi --mode json --no-session --provider openai --model <id>

- api="anthropic" (Anthropic cloud): override the built-in `anthropic`
  provider's baseUrl to the proxy. The Anthropic SDK appends /v1/messages,
  so baseUrl is the proxy root (no /v1 suffix). The real Anthropic models are
  already in pi's built-in catalog.
  Invocation: pi --mode json --no-session --provider anthropic --model <id>

In both cases OPENAI_API_KEY / ANTHROPIC_API_KEY in the container env are
picked up automatically by the built-in provider's key resolution.

Permissions/context: --no-context-files skips AGENTS.md discovery from the
working directory (/testbed). PI_CODING_AGENT_DIR points at a fresh /tmp/pi-agent
with only models.json, so no global AGENTS.md, extensions, or skills are loaded.
Non-interactive --mode json does not show a trust prompt (per pi docs).

Progress visibility: pi's --mode json emits JSON lines (see pi.dev/docs/latest/json).
Observed event shapes verified against a real run (pi 0.84.3, --mode json):
  {"type": "session", "version": 3, "id": "...", "cwd": "..."}
  {"type": "agent_start"}
  {"type": "turn_start"}
  {"type": "message_update", "usage": {...}, "assistantMessageEvent": {...}}
  {"type": "tool_execution_start", "toolCallId": "...", "toolName": "...", ...}
  {"type": "tool_execution_end", "toolCallId": "...", "toolName": "...", ...}
  {"type": "turn_end", ...}
  {"type": "agent_end", ...}
"""

from __future__ import annotations

import json
import shlex
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from acb.container import container_cp_in, container_exec_capture
from acb.harnesses._cache import binary_cache_lock
from acb.harnesses._streaming import execute
from acb.harnesses.base import HarnessAdapter, HarnessResult

if TYPE_CHECKING:
    from acb.ui import ProgressTracker

# Pinned for reproducibility.
DEFAULT_VERSION = "0.84.3"

_ARCH_ALIASES = {
    "arm64": "arm64", "aarch64": "arm64",
    "amd64": "x64",   "x86_64": "x64",
}

_RELEASE_URL = (
    "https://github.com/earendil-works/pi/releases/download/v{version}/"
    "pi-linux-{arch}.tar.gz"
)

_CONTAINER_DIR = "/opt/pi"
_CONTAINER_BINARY = f"{_CONTAINER_DIR}/pi"
_CONTAINER_AGENT_DIR = "/tmp/pi-agent"

# Lock to ensure thread-safe binary downloads; prevents race conditions when
# multiple instances try to download the same binary concurrently.
_DOWNLOAD_LOCK = threading.Lock()


def ensure_linux_files(
    arch: str,
    cache_dir: Path,
    version: str = DEFAULT_VERSION,
    tracker: ProgressTracker | None = None,
    tracker_key: str | None = None,
) -> Path:
    """Download (once, cached) the pi Linux release directory for ``arch``.

    Returns the path to the extracted `pi/` directory (which contains the
    `pi` binary and `node_modules/`). The whole directory is needed because
    the binary loads native addons from node_modules/ at runtime.
    
    Thread-safe: uses a lock to prevent concurrent download race conditions when
    multiple instances try to download the same binary simultaneously. The first
    thread to acquire the lock downloads; others wait and reuse the result.
    
    Args:
        arch: Target architecture
        cache_dir: Cache directory for files
        version: Version to download
        tracker: Optional progress tracker for display updates
        tracker_key: Optional tracker key for activity updates
    """
    pi_arch = _ARCH_ALIASES.get(arch, arch)
    cache_key = f"pi-linux-{pi_arch}-{version}"
    dest_dir = cache_dir / cache_key
    pi_subdir = dest_dir / "pi"  # tarball extracts to pi/ subdirectory
    
    # Quick check without lock (common case: already cached)
    if pi_subdir.exists() and (pi_subdir / "pi").exists():
        return pi_subdir
    
    # Acquire lock for download to prevent concurrent race conditions
    with _DOWNLOAD_LOCK, binary_cache_lock(cache_dir, cache_key):
        # Double-check after acquiring lock: another thread may have finished download
        if pi_subdir.exists() and (pi_subdir / "pi").exists():
            return pi_subdir
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(version=version, arch=pi_arch)
        if tracker and tracker_key:
            tracker.update_activity(tracker_key, f"setup: downloading pi binary ({pi_arch})")
        archive_path = dest_dir / "pi.tar.gz"
        urllib.request.urlretrieve(url, archive_path)  # noqa: S310
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest_dir, filter="data")  # extracts pi/ subdirectory
        archive_path.unlink()
        (pi_subdir / "pi").chmod(0o755)
        return pi_subdir


def _describe_event(obj: dict) -> tuple[str | None, bool]:
    """Extract a short activity description from a pi --mode json event.

    Event types verified live against a real pi 0.84.3 container-mode run
    (31 turns, 30 tool calls, 7179 events):
    * {"type": "session", ...}               -- first line (header, not an event)
    * {"type": "agent_start"}
    * {"type": "turn_start"}
    * {"type": "message_start", ...}
    * {"type": "message_update", "usage": {...}, "assistantMessageEvent":
         {"type": "text_delta", "contentIndex": 0, "delta": "..."}}
    * {"type": "message_end", ...}
    * {"type": "tool_execution_start", "toolCallId": "...", "toolName": "...", "args": {...}}
    * {"type": "tool_execution_update", "toolCallId": "...", "toolName": "...", ...}
    * {"type": "tool_execution_end",   "toolCallId": "...", "toolName": "...",
         "result": ..., "isError": bool}
    * {"type": "turn_end", ...}
    * {"type": "agent_end", ...}
    * {"type": "agent_settled"}              -- emitted after agent_end

    message_update events carry text deltas (is_text_delta=True) for the
    rolling assistant-text preview in the heartbeat.
    """
    etype = obj.get("type")
    if etype == "message_update":
        evt = obj.get("assistantMessageEvent") or {}
        if evt.get("type") == "text_delta":
            return evt.get("delta") or "", True
        # toolcall_start / argument streaming within a message
        tool = evt.get("toolName") or evt.get("id")
        if tool:
            return f"running tool: {tool}", False
        return None, False
    if etype == "tool_execution_start":
        name = obj.get("toolName")
        return (f"running tool: {name}" if name else "running tool"), False
    if etype == "tool_execution_update":
        name = obj.get("toolName")
        return (f"tool working: {name}" if name else "tool working"), False
    if etype == "tool_execution_end":
        name = obj.get("toolName")
        is_err = obj.get("isError")
        label = f"tool {'failed' if is_err else 'finished'}"
        return (f"{label}: {name}" if name else label), False
    if etype in ("turn_end", "agent_end", "agent_settled"):
        return "finalizing response", False
    if etype in ("agent_start", "turn_start"):
        return "starting turn", False
    # session header, message_start, message_end: no meaningful description
    return None, False


class Pi(HarnessAdapter):
    name = "pi"
    api = "anthropic"  # default; runner overrides via effective_api()

    def effective_api(self, model_api: str) -> str:
        """Pi supports both anthropic and openai providers natively."""
        return model_api

    def setup_container(self, container: str, arch: str, cache_dir: Path) -> None:
        """Download (once, cached) the pi release and copy into `container`.

        Copies the entire `pi/` directory (binary + node_modules) to
        /opt/pi/ inside the container, then symlinks /usr/local/bin/pi.
        The whole directory is needed because the binary loads native addons
        from node_modules/ at runtime (e.g. @mariozechner/clipboard).
        """
        pi_dir = ensure_linux_files(
            arch, cache_dir,
            tracker=getattr(self, '_tracker', None),
            tracker_key=getattr(self, '_tracker_key', None)
        )
        # podman cp semantics: if container_path doesn't exist, creates it as
        # a copy of host_path. Each container is fresh so /opt/pi won't exist.
        container_cp_in(container, pi_dir, _CONTAINER_DIR)
        container_exec_capture(container, ["chmod", "+x", _CONTAINER_BINARY])
        container_exec_capture(container, [
            "ln", "-sf", _CONTAINER_BINARY, "/usr/local/bin/pi",
        ])

    def build_container_env(self, base_url: str, api_key: str) -> dict[str, str]:
        """Stash base_url for run_container() and return the env vars.

        models.json (with the provider baseUrl and registered model) is
        written inside run_container() rather than here, because the model
        name is only known at run time. PI_CODING_AGENT_DIR points at the
        directory run_container() will populate.
        """
        self._base_url = base_url  # stashed for run_container()'s models.json
        key_var = "ANTHROPIC_API_KEY" if self.api == "anthropic" else "OPENAI_API_KEY"
        return {
            "HOME": "/root",
            "PI_CODING_AGENT_DIR": _CONTAINER_AGENT_DIR,
            "PI_OFFLINE": "1",           # disable startup update checks
            "PI_SKIP_VERSION_CHECK": "1",
            key_var: api_key,
        }

    def run_container(self, prompt: str, container: str, model: str,
                       env: dict[str, str], out_dir: Path,
                       instance_id: str,
                       binary: str = "/usr/local/bin/pi") -> HarnessResult:
        """Exec pi inside a running container via `podman exec`.

        Writes models.json into PI_CODING_AGENT_DIR inside the container
        before invoking pi so the provider baseUrl and model are registered.
        """
        # Pre-flight: verify binary is present and executable before spending
        # time on the full run. container_exec_capture raises RuntimeError on
        # non-zero exit, giving a clear diagnostic instead of a silent 0-byte
        # transcript when setup_container() failed or the binary is missing.
        try:
            container_exec_capture(container, ["test", "-x", binary])
        except RuntimeError:
            raise RuntimeError(
                f"pi binary not found or not executable at {binary} in "
                f"container {container}. setup_container() may have failed "
                f"silently -- check that the whole pi/ directory was copied "
                f"into /opt/pi/ and the symlink /usr/local/bin/pi -> "
                f"{_CONTAINER_BINARY} was created."
            )

        base_url = getattr(self, "_base_url", "")

        if self.api == "anthropic":
            # Override built-in anthropic provider's endpoint to the proxy.
            # Anthropic SDK appends /v1/messages, so no /v1 suffix here.
            provider_config = {"baseUrl": base_url}
            
            # Vertex AI models have different constraints than direct Anthropic API.
            if model.startswith("google-vertex-anthropic/"):
                # Vertex AI doesn't support output_config.effort (adaptive thinking).
                provider_config["compat"] = {"forceAdaptiveThinking": False}
                
                # Vertex AI has lower max_tokens limits than direct Anthropic API.
                # Haiku: 64k (vs 128k direct), Sonnet: 64k (vs 128k direct)
                # Override the model to set the correct maxTokens limit.
                provider_config["models"] = [{
                    "id": model,
                    "maxTokens": 64000,
                }]
            
            models_json = json.dumps({
                "providers": {
                    "anthropic": provider_config,
                }
            })
        else:
            # Override built-in openai provider's endpoint and register the
            # local model. pi's openai-completions API appends /chat/completions
            # directly, so include /v1 in the baseUrl.
            openai_base_url = base_url.rstrip("/") + "/v1"
            models_json = json.dumps({
                "providers": {
                    "openai": {
                        "baseUrl": openai_base_url,
                        "api": "openai-completions",
                        "models": [{"id": model}],
                    }
                }
            })

        # Inject models.json into the container via a host temp file + podman cp
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                          delete=False) as f:
            f.write(models_json)
            tmp_path = Path(f.name)
        try:
            container_exec_capture(container, ["mkdir", "-p", _CONTAINER_AGENT_DIR])
            container_cp_in(container, tmp_path,
                             f"{_CONTAINER_AGENT_DIR}/models.json")
        finally:
            tmp_path.unlink(missing_ok=True)

        pi_argv = [
            binary,
            "--mode", "json",
            "--no-session",
            "--no-context-files",   # skip AGENTS.md from /testbed
            "--exclude-tools", "edit",  # model sends edits as a JSON string
            # (not a JSON array), so every edit call fails with "edits.0 must
            # be object". Excluding the tool prevents the failed-edit spiral
            # and forces the model to use bash / write for file modifications.
            "--provider", self.api,
            "--model", model,
            "--",                   # stop option parsing
            prompt,
        ]
        if system_prompt := self.config.get("system_prompt"):
            pi_argv = (pi_argv[:pi_argv.index("--")] +
                       ["--append-system-prompt", system_prompt] +
                       pi_argv[pi_argv.index("--"):])

        exec_cmd = ["podman", "exec", "-i", "-t"]
        for key, value in env.items():
            exec_cmd += ["-e", f"{key}={value}"]
        # Same conda activation as the other harnesses: podman exec doesn't
        # source /root/.bashrc, so testbed conda env must be activated
        # explicitly for pi's bash tool to run in the right environment.
        #
        # -t allocates a pseudo-TTY inside the container. Pi is a TUI-first
        # Node.js SEA that -- even in --mode json -- initialises its terminal
        # subsystem at startup. Without a TTY, it has no /dev/tty to write to
        # and may exit silently (code 0) before emitting any JSON events.
        # The PTY keeps /dev/tty available so startup proceeds normally;
        # --mode json then routes all agent events to stdout (fd 1) as clean
        # JSON lines regardless. Verified live: 31-turn run, 30 tool calls.
        activate = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed"
        inner = " ".join(shlex.quote(a) for a in pi_argv)
        exec_cmd += [
            "--workdir", "/testbed", container,
            "bash", "-c", f"{activate} && exec {inner}",
        ]
        # out_dir is now the per-instance directory (instances/{test_id}/)
        transcript_path = Path(out_dir) / "transcript.jsonl"
        label = f"[pi:{instance_id}]"
        timeout = self.config.get("timeout", 1800)
        return execute(exec_cmd, env=None, cwd=None, transcript_path=transcript_path,
                       label=label, timeout=timeout, describe_event=_describe_event,
                       tracker=getattr(self, '_tracker', None),
                       tracker_key=getattr(self, '_tracker_key', None))
