"""Claude Code harness adapter.

Runs Claude Code headless (`claude -p <prompt> --output-format stream-json`)
via `podman exec` inside the SWE-bench eval container, pointed at the (also
containerized) proxy -- same container-mode shape as acb/harnesses/goose.py
(see `ensure_linux_binary()` below for how the binary gets into the image,
and acb/harnesses/_streaming.py for the shared stdout-tailing/heartbeat
plumbing both harnesses use).

Container binary (verified against the real, current
@anthropic-ai/claude-code npm packages -- 2.1.233 installed locally, 2.1.241
on the registry -- not just docs): despite this repo's own earlier
assumption (previously in acb/harnesses/stubs.py/README.md/DESIGN.md, now
corrected), the `claude` CLI is *not* a Node.js package requiring a Node
runtime in the image. Current releases ship a standalone, per-platform
native executable via optional npm dependencies
(`@anthropic-ai/claude-code-linux-arm64` / `-linux-x64`, glibc; `-musl`
variants also exist for Alpine-based images, not used here since SWE-bench's
task images are Ubuntu/Debian-based) -- confirmed by downloading the real
linux-arm64 tarball from the public npm registry and inspecting it: a single
~340MB dynamically-linked (glibc) ELF executable at `package/claude`, no
node_modules alongside it, no separate runtime needed. Container-mode
support here is therefore structurally the same shape as goose's: download
once (per arch, per pinned version), cache, `podman cp` in.

Auth/routing: unlike goose (which has its own per-provider env var scheme --
see that module's docstring), claude-code *does* honor the generic
ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY convention HarnessAdapter's default
`build_container_env()` (api="anthropic") already sets -- confirmed against
Anthropic's own docs: "ANTHROPIC_BASE_URL: Override the API endpoint to
route requests through a proxy or gateway." No override needed for routing;
`build_container_env()` below only adds telemetry/autoupdate/nonessential-
traffic opt-outs on top (mirrors goose's own GOOSE_TELEMETRY_ENABLED=false).

No internet: mirrors acb/harnesses/goose.py's three-layer approach (see
GOOSE_INTERNET_DISABLE.md) but simpler here -- claude-code has a direct CLI
flag for the tool-restriction half goose needed a config file for
(`--allowedTools`, see below), plus the harness's own `system_prompt` config
(harnesses.yaml), passed via `--append-system-prompt`, carrying the same
no-internet reminder goose's does.

`--bare` (recommended by Anthropic's own docs for scripted/SDK use) skips
hooks/skills/custom-commands/plugins/MCP-servers/auto-memory/CLAUDE.md
auto-discovery and never reads OAuth credentials or the system keychain --
useful defaults for a fresh, ephemeral container that has none of those
anyway and must authenticate via the proxy's placeholder API key only.
Confirmed live (real `claude -p ... --bare --verbose --output-format
stream-json`, real binary, not just docs): the `system/init` event's own
`tools` field is exactly `["Bash","Edit","Read"]` in bare mode.

Permissions: NOT `--permission-mode bypassPermissions` /
`--dangerously-skip-permissions`, despite Anthropic's own docs describing
that mode as "Recommended... for sandboxes with no internet access" (which
this container is). Confirmed live (real 2.1.224 binary) that this
immediately fails with `--dangerously-skip-permissions cannot be used with
root/sudo privileges for security reasons` -- SWE-bench's eval images, like
virtually every plain Docker/Podman container, run as root, and there is no
override flag; Anthropic's own dev-container docs confirm this is by design
("The CLI rejects this flag when launched as root, so confirm remoteUser is
set to a non-root account" -- not an option available to us here). Instead,
`--allowedTools "Bash,Edit,Read"` (matching `--bare` mode's own tool set
exactly, confirmed live above) is the standard, officially-documented
pattern for headless/`-p` use -- every headless example in Anthropic's own
docs uses `--allowedTools`, not a bypass flag -- and has no root
restriction: the default "manual" permission mode auto-approves exactly the
tools listed and silently denies (rather than hanging on) anything else in
a non-interactive `-p` session.

`--verbose` is required together with `-p`/`--print` and `--output-format
stream-json` -- confirmed live: without it, the real CLI exits immediately
with `Error: When using --print, --output-format=stream-json requires
--verbose` before ever making a request.

Progress visibility: same shared plumbing as goose (acb/harnesses/
_streaming.py), but Claude Code's stream-json events are whole messages,
not token-by-token deltas (`--include-partial-messages`, which would add
token-level streaming, is deliberately not used here -- keeps transcripts
smaller; one heartbeat-worthy description per assistant message/tool call is
enough). Event shapes below fully verified live end to end -- a real
container-mode run (real 2.1.241 Linux binary, `podman exec`, real
multi-turn tool-calling session against a local model through Praxis-ai's
translation, 10 real turns, real Bash/Read tool calls and results):

* `{"type": "system", "subtype": "init", "tools": [...], "model": ...,
  "permissionMode": ..., ...}` -- first event. `tools` confirmed exactly
  `["Bash", "Edit", "Read"]` in `--bare` mode.
* `{"type": "system", "subtype": "thinking_tokens", "estimated_tokens": N,
  ...}` -- an undocumented event, fired repeatedly while a request is being
  prepared; despite the name this is a local, client-side estimate (seen
  firing before any network byte reached a deliberately-unreachable test
  endpoint in earlier testing), not a signal the model is actually
  responding.
* `{"type": "assistant", "message": {"role": "assistant", "content": [...]}}`
  -- content blocks `{"type": "text", "text": ...}` /
  `{"type": "tool_use", "name": ..., "input": ...}`.
* `{"type": "user", "message": {"content": [{"type": "tool_result",
  "is_error": ..., "content": ...}]}}` -- tool results come back as `user`
  messages, confirmed live for real Bash/Read results (and separately, in
  earlier testing, for the unrelated "[Request interrupted by user]" case,
  which uses this same envelope).

One more real quirk, live-verified: the CLI's stdout isn't *purely*
stream-json -- when the model name isn't one it recognizes as a real
Claude model (true for any local/third-party model, e.g. this one), it
prints a plain, non-JSON warning line straight into the same stream:
`[claude-code:unrecognized_model] {"model": "...", "query_source": "sdk"}`.
Harmless here: acb/harnesses/_streaming.py's `read_stream()` already
tolerates a line that fails `json.loads` (treats it as opaque "processing"
activity, same as it always did for goose's own startup banner), so this
doesn't break anything -- but it's why `transcript.jsonl` isn't reliably
"one JSON object per line" for local-model runs, if anything downstream
ever assumes that.
* final `{"type": "result", "subtype": ..., "is_error": ..., "result": ...,
  "usage": {"input_tokens": ..., "output_tokens": ...,
  "cache_creation_input_tokens": ..., "cache_read_input_tokens": ...},
  "total_cost_usd": ..., "duration_ms": ..., "num_turns": ..., "session_id":
  ..., "stop_reason": ...}` -- confirmed live for both a real successful
  completion (`is_error: false`, `stop_reason: "end_turn"`, `num_turns: 10`,
  a nonzero `total_cost_usd`) and an earlier `is_error: true`/
  `total_cost_usd: 0` failure case. Note this event's own `usage.
  input_tokens` was `0` even in the successful run (only `output_tokens`
  was nonzero) -- not reliable as a token-accounting source on its own,
  which is exactly why usage.jsonl comes from Praxis (acb/proxy/praxis.py),
  not this event or the transcript.
"""

from __future__ import annotations

import shlex
import tarfile
import threading
import urllib.request
from pathlib import Path

from acb.container import container_cp_in, container_exec_capture
from acb.harnesses._streaming import execute
from acb.harnesses.base import HarnessAdapter, HarnessResult

# Pinned rather than tracking "latest" -- reproducible benchmark runs
# shouldn't silently pick up a new CLI version (different default tool
# behavior, system prompt, etc.) between runs. Verified this exact version
# exists for both archs on the public npm registry (HTTP 200 on
# registry.npmjs.org/@anthropic-ai/claude-code-linux-{arch}/2.1.241) and
# that linux-arm64's tarball extracts to a real, dynamically-linked (glibc)
# ELF executable at package/claude (not a stub or install script).
DEFAULT_VERSION = "2.1.241"

# npm's per-platform optional-dependency package naming
# (@anthropic-ai/claude-code-linux-{arch}) uses "arm64"/"x64", not
# aarch64/x86_64 like goose's GitHub release assets -- a different
# convention from acb/harnesses/goose.py's _ARCH_ALIASES, kept separate
# rather than shared since there's no real overlap.
_ARCH_ALIASES = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}

_REGISTRY_TARBALL_URL = (
    "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-{npm_arch}/-/"
    "claude-code-linux-{npm_arch}-{version}.tgz"
)

# Lock to ensure thread-safe binary downloads; prevents race conditions when
# multiple instances try to download the same binary concurrently.
_DOWNLOAD_LOCK = threading.Lock()


def ensure_linux_binary(arch: str, cache_dir: Path, version: str = DEFAULT_VERSION) -> Path:
    """Download (once, cached) a Linux `claude` binary for ``arch``; return its path.

    No `npm`/`node` needed on the host -- the platform package is a plain
    tarball on the public npm registry, fetched the same way
    acb/harnesses/goose.py fetches goose's GitHub release asset. The tarball
    layout is a fixed `package/claude` (verified: `tar -tzf` on the real
    linux-arm64 2.1.241 tarball lists exactly `package/claude`,
    `package/package.json`, `package/LICENSE.md`, `package/README.md`) --
    no `npm install`/extraction-via-npm needed, just untar and take the one
    binary.
    
    Thread-safe: uses a lock to prevent concurrent download race conditions when
    multiple instances try to download the same binary simultaneously. The first
    thread to acquire the lock downloads; others wait and reuse the result.
    """
    npm_arch = _ARCH_ALIASES.get(arch, arch)
    dest_dir = cache_dir / f"claude-code-linux-{npm_arch}-{version}"
    dest = dest_dir / "claude"
    
    # Quick check without lock (common case: already cached)
    if dest.exists():
        return dest
    
    # Acquire lock for download to prevent concurrent race conditions
    with _DOWNLOAD_LOCK:
        # Double-check after acquiring lock: another thread may have finished download
        if dest.exists():
            return dest
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = _REGISTRY_TARBALL_URL.format(npm_arch=npm_arch, version=version)
        print(f"[claude-code] downloading Linux binary for {npm_arch} from {url} ...", flush=True)
        archive_path = dest_dir / "claude-code.tgz"
        urllib.request.urlretrieve(url, archive_path)  # noqa: S310
        with tarfile.open(archive_path) as tf:
            member = tf.getmember("package/claude")
            member.name = "claude"  # extract flat into dest_dir, not package/claude
            tf.extract(member, dest_dir, filter="data")  # noqa: S202
        archive_path.unlink()
        dest.chmod(0o755)
        return dest


def _describe_event(obj: dict) -> tuple[str | None, bool]:
    """Extract a short activity description from a parsed stream-json event.

    Returns (description, is_text_delta) -- see acb/harnesses/_streaming.py's
    `DescribeEvent` for the contract. Claude Code's non-partial-message
    events are always whole messages (no `--include-partial-messages`), so
    this never returns a text delta (always False) -- each description is
    shown verbatim as a one-shot event rather than accumulated.
    """
    etype = obj.get("type")
    if etype == "system":
        subtype = obj.get("subtype")
        if subtype == "init":
            return "session started", False
        if subtype == "thinking_tokens":
            # Local/client-side estimate, not a real network signal (see
            # module docstring) -- still worth a heartbeat line so a long
            # gap before the first real event doesn't look like a hang.
            tokens = obj.get("estimated_tokens")
            return (f"preparing request (~{tokens} tokens)" if tokens is not None
                    else "preparing request"), False
        if subtype == "api_retry":
            return f"API retry (attempt {obj.get('attempt')})", False
        return f"system: {subtype}" if subtype else "processing...", False
    if etype == "result":
        return "finalizing response", False
    if etype not in ("assistant", "user"):
        return None, False
    message = obj.get("message") or {}
    for item in message.get("content") or []:
        itype = item.get("type")
        if itype == "tool_use":
            name = item.get("name")
            return (f"running tool: {name}" if name else "running tool"), False
        if itype == "tool_result":
            return ("tool call failed" if item.get("is_error") else "tool call finished"), False
        if itype == "text" and etype == "assistant":
            text = item.get("text") or ""
            preview = text.strip()
            return (f"thinking: {preview[:160]}" if preview else "thinking..."), False
    return None, False


class ClaudeCode(HarnessAdapter):
    name = "claude-code"
    api = "anthropic"

    def effective_api(self, model_api: str) -> str:
        """Always speaks Anthropic Messages -- locked regardless of model backend.

        When the model backend speaks OpenAI (e.g. a local vLLM server),
        praxis's existing anthropic→openai translation filter handles the
        conversion transparently; no change needed here.
        """
        return "anthropic"

    def setup_container(self, container: str, arch: str, cache_dir: Path) -> None:
        """Download (once, cached) this arch's claude Linux binary and copy
        it into `container` at /usr/local/bin/claude, before run_container()
        execs it. Same pattern as Goose.setup_container().
        
        cache_dir is the run-level cache directory, shared across all harnesses
        and instances to avoid redundant binary downloads."""
        binary = ensure_linux_binary(arch, cache_dir)
        container_cp_in(container, binary, "/usr/local/bin/claude")
        container_exec_capture(container, ["chmod", "+x", "/usr/local/bin/claude"])

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str,
                       binary: str = "/usr/local/bin/claude") -> HarnessResult:
        """Exec the harness inside a running container via `podman exec`.

        `podman exec` doesn't take a Python env dict for the exec'd process
        (only for the `podman` CLI invocation itself); env vars are passed as
        repeated `-e KEY=VALUE` flags instead -- same convention as
        acb/harnesses/goose.py's `run_container()`.
        """
        claude_argv = [
            binary,
            "-p", prompt,
            "--bare",
            "--verbose",  # required together with -p + --output-format stream-json
            "--output-format", "stream-json",
            "--model", model,
            # NOT --permission-mode bypassPermissions / --dangerously-skip-
            # permissions: confirmed live (real 2.1.224 binary) that this
            # container -- like virtually every plain Docker/Podman
            # container, SWE-bench's own eval images included -- runs as
            # root, and the CLI hard-refuses those modes as root ("cannot be
            # used with root/sudo privileges for security reasons"), no
            # override flag exists (confirmed against Anthropic's own dev-
            # container docs: "The CLI rejects this flag when launched as
            # root, so confirm remoteUser is set to a non-root account" --
            # i.e. the documented fix is "don't run as root", not available
            # to us here). `--allowedTools` is the standard, official
            # pattern for exactly this instead (every headless example in
            # Anthropic's own docs uses it, not a bypass flag) -- default
            # "manual" permission mode auto-approves exactly the tools
            # listed and silently denies (doesn't hang on) anything else in
            # a non-interactive `-p` session, with no root restriction.
            # Scoped to --bare mode's own tool set (confirmed live via a
            # real system/init event: `"tools":["Bash","Edit","Read"]`) --
            # nothing to additionally deny (WebSearch/WebFetch aren't in
            # that set at all), so no separate --disallowedTools needed.
            "--allowedTools", "Bash,Edit,Read",
            "--no-session-persistence",
        ]
        if system_prompt := self.config.get("system_prompt"):
            claude_argv += ["--append-system-prompt", system_prompt]
        if max_budget := self.config.get("max_budget_usd"):
            claude_argv += ["--max-budget-usd", str(max_budget)]

        exec_cmd = ["podman", "exec", "-i"]
        for key, value in env.items():
            exec_cmd += ["-e", f"{key}={value}"]
        # Same conda-activation need as goose's run_container() (see that
        # module's own comment for the full rationale): `podman exec`
        # doesn't source `/root/.bashrc`, so the testbed's conda env (where
        # the repo and its test deps actually live) has to be activated
        # explicitly before exec'ing claude.
        activate = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed"
        inner = " ".join(shlex.quote(a) for a in claude_argv)
        exec_cmd += [
            "--workdir", "/testbed", container,
            "bash", "-c", f"{activate} && exec {inner}",
        ]
        # out_dir is now the per-instance directory (instances/{test_id}/)
        transcript_path = Path(out_dir) / "transcript.jsonl"
        label = f"[claude-code:{instance_id}]"
        timeout = self.config.get("timeout", 1800)
        return execute(exec_cmd, env=None, cwd=None, transcript_path=transcript_path,
                       label=label, timeout=timeout, describe_event=_describe_event)

    def build_container_env(self, base_url: str, api_key: str) -> dict[str, str]:
        """Base anthropic env (ANTHROPIC_BASE_URL/API_KEY, from
        HarnessAdapter's default) plus opt-outs for background network
        traffic a fresh, ephemeral, no-internet-by-policy container has no
        use for -- mirrors goose's own GOOSE_TELEMETRY_ENABLED=false.
        """
        env = super().build_container_env(base_url, api_key)
        env.update({
            "DISABLE_TELEMETRY": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        })
        return env
