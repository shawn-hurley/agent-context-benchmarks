"""OpenCode harness adapter.

Runs OpenCode headless (`opencode run <prompt> --format json`) via `podman exec`
inside the SWE-bench eval container -- same container-mode shape as goose and
claude-code (see `ensure_linux_binary()` for how the binary gets in, and
acb/harnesses/_streaming.py for the shared stdout-tailing/heartbeat plumbing).

Binary distribution: OpenCode ships standalone Linux binaries on GitHub Releases
(`opencode-linux-{arm64,x64}.tar.gz`) as single-file archives -- the same
`podman cp` pattern as goose and claude-code, no runtime required.

Provider/model configuration: OpenCode requires the model in `provider/model`
format and uses OPENCODE_CONFIG_CONTENT (inline JSON, no file needed) to
configure provider options. We use explicit model registration for both
API types to avoid /v1/models fetches (which fail on Vertex AI with 404):

- api="openai" (local vLLM/Ollama): a custom `acb` provider declared as
  `@ai-sdk/openai-compatible` with the proxy baseURL and the model explicitly
  registered. Model flag: `acb/<model>`.
- api="anthropic" (Anthropic cloud / Vertex AI): the built-in `anthropic`
  provider with baseURL override and the model explicitly registered. Model
  flag: `anthropic/<model>`.

Both cases explicitly register the model in the provider config, ensuring
OpenCode never attempts to fetch /v1/models from the proxy (which would
fail on Vertex AI backends that don't expose model listings).

`OPENCODE_CONFIG_CONTENT` is set inside run_container() (not
build_container_env()) because the model name is only available at that
point, ensuring the proxy URL and registered model are always consistent.

Permissions: `--auto` auto-approves all tool permissions without prompting --
the OpenCode equivalent of goose's no-permission-mode and claude-code's
`--allowedTools`.

No internet: same system_prompt config knob as goose/claude-code (harnesses.yaml
`system_prompt` key). Because opencode run has no --append-system-prompt flag,
the text is written to ~/.config/opencode/AGENTS.md inside the container before
each run -- opencode's documented global-rules file (opencode.ai/docs/rules).
"""

from __future__ import annotations

import json
import shlex
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from acb.container import container_cp_in, container_exec_capture
from acb.harnesses._streaming import execute
from acb.harnesses.base import HarnessAdapter, HarnessResult

# Pinned for reproducibility -- bump deliberately when a new version is needed.
DEFAULT_VERSION = "1.18.22"

_ARCH_ALIASES = {
    "arm64": "arm64", "aarch64": "arm64",
    "amd64": "x64",   "x86_64": "x64",
}

_RELEASE_URL = (
    "https://github.com/anomalyco/opencode/releases/download/v{version}/"
    "opencode-linux-{arch}.tar.gz"
)

# Lock to ensure thread-safe binary downloads; prevents race conditions when
# multiple instances try to download the same binary concurrently.
_DOWNLOAD_LOCK = threading.Lock()


def ensure_linux_binary(arch: str, cache_dir: Path,
                         version: str = DEFAULT_VERSION) -> Path:
    """Download (once, cached) a Linux `opencode` binary for ``arch``.
    
    Thread-safe: uses a lock to prevent concurrent download race conditions when
    multiple instances try to download the same binary simultaneously. The first
    thread to acquire the lock downloads; others wait and reuse the result.
    """
    oc_arch = _ARCH_ALIASES.get(arch, arch)
    dest_dir = cache_dir / f"opencode-linux-{oc_arch}-{version}"
    dest = dest_dir / "opencode"
    
    # Quick check without lock (common case: already cached)
    if dest.exists():
        return dest
    
    # Acquire lock for download to prevent concurrent race conditions
    with _DOWNLOAD_LOCK:
        # Double-check after acquiring lock: another thread may have finished download
        if dest.exists():
            return dest
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(version=version, arch=oc_arch)
        print(f"[opencode] downloading Linux binary for {oc_arch} from {url} ...", flush=True)
        archive_path = dest_dir / "opencode.tar.gz"
        urllib.request.urlretrieve(url, archive_path)  # noqa: S310
        with tarfile.open(archive_path) as tf:
            member = tf.getmember("opencode")   # single file at tarball root
            tf.extract(member, dest_dir, filter="data")  # noqa: S202
        archive_path.unlink()
        dest.chmod(0o755)
        return dest


def _describe_event(obj: dict) -> tuple[str | None, bool]:
    """Extract a short activity description from a parsed --format json event.

    Verified against real opencode 1.18.22 --format json output. Events are
    whole objects (not token-level deltas), so is_text_delta is always False.

    Observed event shapes (real run, acb container mode):
    * {"type": "step_start", "part": {...}}
    * {"type": "text", "part": {"type": "text", "text": "..."}}
    * {"type": "tool_use", "part": {"tool": "<name>", "state": {"input": {...}, "output": ...}}}
    * {"type": "step_finish", "part": {"reason": "tool-calls" | "stop"}}
    """
    etype = obj.get("type")
    part = obj.get("part") or {}
    if etype == "text":
        text = part.get("text") or obj.get("text") or ""
        preview = str(text).strip()
        return (f"thinking: {preview[:160]}" if preview else "thinking..."), False
    if etype == "tool_use":
        name = part.get("tool") or part.get("toolName")
        state = part.get("state") or {}
        status = state.get("status", "")
        if status == "completed":
            return (f"tool finished: {name}" if name else "tool call finished"), False
        return (f"running tool: {name}" if name else "running tool"), False
    if etype == "step_finish":
        reason = part.get("reason", "")
        return ("finalizing response" if reason == "stop" else "step complete"), False
    if etype == "step_start":
        return "starting step", False
    if etype == "error":
        err = obj.get("error") or {}
        msg = (err.get("data") or {}).get("message") or str(err)
        return f"error: {msg[:120]}", False
    return None, False


class OpenCode(HarnessAdapter):
    name = "opencode"
    api = "openai"  # default; runner overrides via effective_api()

    def effective_api(self, model_api: str) -> str:
        """OpenCode supports both anthropic and openai providers natively."""
        return model_api

    def setup_container(self, container: str, arch: str, cache_dir: Path) -> None:
        """Download (once, cached) this arch's opencode Linux binary and copy
        it into `container` at /usr/local/bin/opencode.
        
        cache_dir is the run-level cache directory, shared across all harnesses
        and instances to avoid redundant binary downloads.
        """
        binary = ensure_linux_binary(arch, cache_dir)
        container_cp_in(container, binary, "/usr/local/bin/opencode")
        container_exec_capture(container, ["chmod", "+x", "/usr/local/bin/opencode"])

    def build_container_env(self, base_url: str, api_key: str) -> dict[str, str]:
        """Stash base_url for run_container() and return the API key env var.

        OPENCODE_CONFIG_CONTENT is set inside run_container() rather than here
        because it needs the model name (only known at run time) to register
        the model in the custom provider config.
        """
        self._base_url = base_url  # stashed for run_container()'s config build
        key_var = "ANTHROPIC_API_KEY" if self.api == "anthropic" else "OPENAI_API_KEY"
        return {
            "HOME": "/root",
            key_var: api_key,
        }

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str,
                       binary: str = "/usr/local/bin/opencode") -> HarnessResult:
        """Exec opencode inside a running container via `podman exec`.

        Builds OPENCODE_CONFIG_CONTENT here (not in build_container_env) so
        the model name can be registered in the provider config alongside the
        proxy baseURL, ensuring the model ID in the request body matches what
        the backend (e.g. vLLM) expects.
        """
        # Pre-flight: verify binary is present and executable before spending
        # time on the full run. container_exec_capture raises RuntimeError on
        # non-zero exit, giving a clear diagnostic instead of a silent 0-byte
        # transcript when setup_container() failed or the binary is missing.
        try:
            container_exec_capture(container, ["test", "-x", binary])
        except RuntimeError:
            raise RuntimeError(
                f"opencode binary not found or not executable at {binary} in "
                f"container {container}. setup_container() may have failed "
                f"silently. Check that the binary was copied in correctly."
            )

        base_url = getattr(self, "_base_url", "")

        if self.api == "anthropic":
            # Use built-in anthropic provider with explicit model registration.
            # The Anthropic SDK appends /messages to baseURL (not /v1/messages),
            # so we must include /v1 in the baseURL to get the full path
            # <base_url>/v1/messages -- matching praxis's router (path_prefix: /v1/).
            # Registering the model explicitly avoids opencode attempting to
            # fetch /v1/models (which fails on Vertex AI backends with 404).
            anthropic_base_url = base_url.rstrip("/") + "/v1"
            config = json.dumps({
                "provider": {
                    "anthropic": {
                        "options": {"baseURL": anthropic_base_url},
                        "models": {model: {}},
                    }
                }
            })
            opencode_model = f"anthropic/{model}"
        else:
            # Custom openai-compatible provider pointing at the proxy.
            # @ai-sdk/openai-compatible appends /chat/completions to baseURL
            # directly (not /v1/chat/completions), so we include /v1 in the
            # baseURL so the full path is <base_url>/v1/chat/completions --
            # matching praxis's router (path_prefix: /v1/).
            # Registering the model explicitly avoids opencode rejecting it as
            # unknown before making the request.
            openai_base_url = base_url.rstrip("/") + "/v1"
            config = json.dumps({
                "provider": {
                    "acb": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "ACB Proxy",
                        "options": {"baseURL": openai_base_url},
                        "models": {model: {}},
                    }
                }
            })
            opencode_model = f"acb/{model}"

        env = {**env, "OPENCODE_CONFIG_CONTENT": config,
               "OPENCODE_DISABLE_AUTOUPDATE": "1"}

        opencode_argv = [
            binary, "run", prompt,
            "--model", opencode_model,
            "--format", "json",
            "--auto",   # auto-approve all tool permissions
            "--log-level", "DEBUG",
        ]
        # opencode has no --append-system-prompt / --system CLI flag (verified
        # against 1.18.22 --help). Global rules are delivered instead via
        # ~/.config/opencode/AGENTS.md, which opencode loads automatically as
        # system-level context for every session (documented at
        # opencode.ai/docs/rules -- "Global" type, takes precedence over
        # project-local AGENTS.md). Writing it here, just before exec, mirrors
        # the pattern goose uses for its container config injection.
        if system_prompt := self.config.get("system_prompt"):
            container_exec_capture(container, ["mkdir", "-p", "/root/.config/opencode"])
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                f.write(system_prompt)
                tmp_path = Path(f.name)
            try:
                container_cp_in(container, tmp_path,
                                "/root/.config/opencode/AGENTS.md")
            finally:
                tmp_path.unlink(missing_ok=True)

        exec_cmd = ["podman", "exec", "-i", "-t"]
        for key, value in env.items():
            exec_cmd += ["-e", f"{key}={value}"]
        # Same conda activation as goose/claude-code: podman exec doesn't
        # source /root/.bashrc, so the testbed conda env must be activated
        # explicitly so opencode's bash tool runs in the right environment.
        #
        # -t allocates a pseudo-TTY inside the container. opencode (like pi)
        # is a TUI-first Node.js application that -- even in --format json
        # mode -- initialises its terminal subsystem at startup. Without a
        # TTY, it has no /dev/tty to write to, and may exit silently (code 0)
        # before emitting any JSON. The PTY keeps /dev/tty available so
        # startup proceeds; --format json then routes all agent events to
        # stdout (fd 1) as clean JSON lines regardless.
        activate = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed"
        inner = " ".join(shlex.quote(a) for a in opencode_argv)
        exec_cmd += [
            "--workdir", "/testbed", container,
            "bash", "-c", f"{activate} && exec {inner}",
        ]
        # out_dir is now the per-instance directory (instances/{test_id}/)
        transcript_path = Path(out_dir) / "transcript.jsonl"
        label = f"[opencode:{instance_id}]"
        timeout = self.config.get("timeout", 1800)
        return execute(exec_cmd, env=None, cwd=None, transcript_path=transcript_path,
                       label=label, timeout=timeout, describe_event=_describe_event)
