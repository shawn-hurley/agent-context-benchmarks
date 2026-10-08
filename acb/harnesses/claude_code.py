"""Isolated Claude Code adapter with explicit tools, settings, MCP and hook delivery."""

from __future__ import annotations

from acb.transport import EnvironmentTransport

import json
import logging
import os
import shlex
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from acb.downloads import download_file
from acb.container import container_cp_in, container_exec_capture
from acb.transport import command as environment_command
from acb.harnesses._cache import binary_cache_lock, harness_cache_ready, staged_harness_cache
from acb.harnesses._streaming import execute
from acb.harnesses.base import HarnessAdapter, HarnessResult

if TYPE_CHECKING:
    from acb.ui import ProgressTracker

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


def ensure_linux_binary(
    arch: str,
    cache_dir: Path,
    version: str = DEFAULT_VERSION,
    tracker: ProgressTracker | None = None,
    tracker_key: str | None = None,
) -> Path:
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
    
    Args:
        arch: Target architecture
        cache_dir: Cache directory for binary
        version: Binary version to download
        tracker: Optional progress tracker for display updates
        tracker_key: Optional tracker key for activity updates
    """
    npm_arch = _ARCH_ALIASES.get(arch, arch)
    cache_key = f"claude-code-linux-{npm_arch}-{version}"
    dest_dir = cache_dir / cache_key
    dest = dest_dir / "claude"
    
    # Quick check without lock (common case: already cached)
    if harness_cache_ready(dest_dir, "claude"):
        return dest
    
    # Acquire lock for download to prevent concurrent race conditions
    with _DOWNLOAD_LOCK, binary_cache_lock(cache_dir, cache_key):
        # Double-check after acquiring lock: another thread may have finished download
        if harness_cache_ready(dest_dir, "claude"):
            return dest
        
        url = _REGISTRY_TARBALL_URL.format(npm_arch=npm_arch, version=version)
        if tracker and tracker_key:
            tracker.update_activity(tracker_key, f"setup: downloading claude-code binary ({npm_arch})")
        with staged_harness_cache(dest_dir, "claude", url) as staging:
            archive_path = staging / "claude-code.tgz"
            download_file(url, archive_path)  # noqa: S310
            with tarfile.open(archive_path) as tf:
                member = tf.getmember("package/claude")
                member.name = "claude"  # extract flat into staging, not package/claude
                tf.extract(member, staging, filter="data")  # noqa: S202
            archive_path.unlink()
            (staging / "claude").chmod(0o755)
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
        if              itype == "text" and etype == "assistant":
            text = item.get("text") or ""
            preview = text.strip()
            return (f"thinking: {preview[:160]}" if preview else "thinking..."), False
    return None, False


def _generate_skill_hints_claude_code(skills: list[dict]) -> str:
    """Generate MANDATORY skill loading instructions for Claude-code.
    
    Creates imperative instructions requiring skill loading before any code
    analysis. Matches Goose's proven .goosehints approach with two-layer enforcement.
    
    Args:
        skills: List of skill configurations
        
    Returns:
        Markdown content with mandatory skill loading instructions
    """
    if not skills:
        return ""
    
    skill_list = "\n".join([
        f"- **{s.get('name', 'unknown')}** at `/root/.claude/skills/{s.get('name', 'unknown')}/SKILL.md`: {s.get('description', 'Required for this task')}"
        for s in skills
    ])
    
    read_commands = "\n".join([
        f'Read /root/.claude/skills/{s.get("name", "unknown")}/SKILL.md'
        for s in skills
    ])
    
    return f"""# ⚠️ CRITICAL: SKILL LOADING REQUIRED

## STEP 1: Load All Skills (MANDATORY - DO THIS FIRST)

**YOU MUST load the following {len(skills)} skill(s) as your ABSOLUTE FIRST ACTION before any code analysis:**

{skill_list}

**Use the Read tool to load each skill:**

```
{read_commands}
```

**DO NOT use find, grep, bash, or analyze UNTIL you have loaded all skills above.**

---

## STEP 2: Follow Skill Instructions (MANDATORY)

After loading each skill, you will receive detailed instructions with sections like:
- "⚠️ CRITICAL" 
- "REQUIRED: Always Follow This Exact Sequence"
- "YOU MUST" statements

**These instructions are NOT suggestions or guidance - they are MANDATORY procedures that you MUST follow exactly as written.**

**YOU MUST:**
1. Read the entire skill content after loading
2. Identify the "REQUIRED: Always Follow This Exact Sequence" section
3. Execute EVERY step in that sequence IN ORDER
4. Use ONLY the tools and methods the skill specifies
5. Do NOT use forbidden tools (like find, grep, bash) until the skill says you can

**Critical:** If the skill says "DO NOT use X UNTIL AFTER you...", then using X before completing those steps = automatic task failure, even if your code changes are correct.

---

## Why This Two-Layer Enforcement Exists

Skills provide optimized workflows tested specifically for this benchmark. The system prompt (STEP 1) tells you to load skills. The skill content (STEP 2) tells you HOW to use it. Both layers are mandatory. Skipping either layer = automatic task failure.
"""


class ClaudeCode(HarnessAdapter):
    name = "claude-code"
    api = "anthropic"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        if self.config.get("launch_profile", "isolated-hooks") != "isolated-hooks":
            raise ValueError("Claude Code always uses isolated-hooks")

    def effective_api(self, model_api: str) -> str:
        """Always speaks Anthropic Messages -- locked regardless of model backend.

        When the model backend speaks OpenAI (e.g. a local vLLM server),
        praxis's existing anthropic→openai translation filter handles the
        conversion transparently; no change needed here.
        """
        return "anthropic"

    def setup_container(self, container: EnvironmentTransport, arch: str, cache_dir: Path) -> None:
        """Download (once, cached) this arch's claude Linux binary and copy
        it into `container` at /usr/local/bin/claude, before run_container()
        execs it. Same pattern as Goose.setup_container().
        
        cache_dir is shared across all runs under the output directory."""
        binary = ensure_linux_binary(
            arch, cache_dir,
            version=self.config.get("version", DEFAULT_VERSION),
            tracker=getattr(self, '_tracker', None),
            tracker_key=getattr(self, '_tracker_key', None)
        )
        container_cp_in(container, binary, "/usr/local/bin/claude")
        container_exec_capture(container, ["chmod", "+x", "/usr/local/bin/claude"])

    def run_container(self, prompt: str, container: EnvironmentTransport, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str,
                       binary: str = "/usr/local/bin/claude") -> HarnessResult:
        """Execute the harness through its Harbor environment transport.

        Commands carry explicit environment variables and working directories.
        """
        claude_argv = [
            binary,
            "-p", prompt,
            "--verbose",  # required together with -p + --output-format stream-json
            "--output-format", "stream-json",
            "--model", model,
            # Root agents use explicit headless tool permissions. Project hooks,
            # settings and discovered tools remain disabled by the launch flags.
            "--allowedTools", "Bash,Edit,Read",
            "--no-session-persistence",
        ]
        settings = self.integration_activation.claude_settings
        claude_argv += ["--settings", settings[0] if settings else "{}",
                        "--setting-sources", "", "--tools", "Bash,Edit,Read",
                        "--disable-slash-commands"]
        if self.config.get("mcp_servers"):
            claude_argv += ["--strict-mcp-config", "--mcp-config", "/tmp/acb-claude-mcp.json"]
            # MCP tools need explicit permission in non-interactive runs. Limit
            # that permission to the configured servers; retain built-in policy.
            allowed = claude_argv.index("--allowedTools") + 1
            claude_argv[allowed] += ''.join(',mcp__' + server['name'] for server in self.config['mcp_servers'])
        else:
            claude_argv += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        # Build combined prompt: system_prompt + skill hints
        combined_prompt = ""
        system_prompt = self.config.get("system_prompt", "")
        skills = self.config.get("skills", [])

        if system_prompt:
            combined_prompt += system_prompt

        if skills:
            if combined_prompt:  # Add separator if we have system_prompt
                combined_prompt += "\n\n---\n\n"
            combined_prompt += _generate_skill_hints_claude_code(skills)

        # Append combined prompt via CLI flag
        if combined_prompt:
            claude_argv += ["--append-system-prompt", combined_prompt]
        if max_budget := self.config.get("max_budget_usd"):
            claude_argv += ["--max-budget-usd", str(max_budget)]

        # Same conda-activation need as goose's run_container() (see that
        # module's own comment for the full rationale): noninteractive execution
        # doesn't source `/root/.bashrc`, so the testbed's conda env (where
        # the repo and its test deps actually live) has to be activated
        # explicitly before exec'ing claude.
        workdir = self.config.get("workdir", "/testbed")
        conda_env = self.config.get("conda_env", "testbed")
        preamble = ("source /opt/miniconda3/etc/profile.d/conda.sh && conda activate "
                    + shlex.quote(conda_env) + " && ") if conda_env else ""
        inner = " ".join(shlex.quote(a) for a in claude_argv)
        exec_cmd = environment_command(
            container, ["bash", "-c", f"{preamble}exec {inner}"], env, workdir,
        )
        # out_dir is the trial artifact directory
        transcript_path = Path(out_dir) / "transcript.jsonl"
        timeout = self.config.get("timeout", 1800)
        return execute(exec_cmd, transcript_path=transcript_path,
                       timeout=timeout, describe_event=_describe_event,
                       tracker=getattr(self, '_tracker', None),
                       tracker_key=getattr(self, '_tracker_key', None))

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
        env.update(CLAUDE_CONFIG_DIR="/tmp/acb-claude-agent",
                   CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
                   CLAUDE_CODE_DISABLE_CLAUDE_MDS="1")
        return env

    def _write_mcp_config(self, container: EnvironmentTransport, servers: list[dict]) -> None:
        """Write the explicit MCP config passed by run_container().

        Args:
            container: Harbor environment transport
            servers: List of MCP server configurations from harnesses.yaml
        """
        if not servers:
            return

        from acb.mcp import MCPServerManager
        import logging

        logger = logging.getLogger(__name__)

        mcp_mgr = MCPServerManager()
        config_data = mcp_mgr.generate_config(servers, "claude-code")

        # Write config to temp file as JSON
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f, indent=2)
            tmp_path = Path(f.name)

        try:
            # Copy config file to container
            container_cp_in(
                container,
                tmp_path,
                "/tmp/acb-claude-mcp.json"
            )
            logger.info("MCP config written to /tmp/acb-claude-mcp.json")
        finally:
            tmp_path.unlink(missing_ok=True)
