"""Agent harness interface.

A HarnessAdapter knows how to invoke one CLI agent (claude-code, goose,
opencode, pi) headlessly *inside a running container*, pointed at the proxy's
base_url so every LLM call is measured. It returns the agent's raw output; the
Benchmark turns the mutated container's checkout into a Prediction.

Generation is container-only (see acb/runner.py, acb/benchmarks/swebench.py):
the harness runs inside the same SWE-bench eval image evaluation will grade
the patch in, so its dev environment matches evaluation exactly, rather than
whatever happens to be on the machine running `acb`. A harness needs a Linux
build (or an image with its runtime baked in) to support this. All four
harnesses ship standalone binaries: `goose` (static-ish Linux binary),
`claude-code` (standalone per-arch native executable), `opencode` (standalone
binary from GitHub Releases), and `pi` (standalone binary with node_modules).
See their respective modules in acb/harnesses/ for implementation details.

Staging a harness's own runtime into the container (e.g. `podman cp`-ing in
a binary) is the harness's job, not the benchmark's -- see
`setup_container()` below. This used to be baked into
`Benchmark.prepare_container()`'s signature as a goose-specific
`goose_binary: Path` param; that coupling didn't survive a second
container-mode harness (claude-code needs a different binary entirely) and
has been pulled out into this per-harness hook instead.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HarnessResult:
    output: str
    exit_code: int
    timed_out: bool = False


class HarnessAdapter(ABC):
    name: str = "base"
    # which proxy API surface this harness speaks; drives base_url env var choice
    api: str = "anthropic"  # or "openai"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def build_container_env(self, base_url: str, api_key: str) -> dict[str, str]:
        """Env passed to the harness process inside the container (as
        `podman exec -e KEY=VALUE` flags, not a full env dict) -- deliberately
        minimal rather than inheriting the host's environment, which is huge
        and irrelevant inside the container. Override per harness (see
        acb/harnesses/goose.py for a provider that needs extra vars).
        """
        env = {"HOME": "/root"}
        if self.api == "anthropic":
            env["ANTHROPIC_BASE_URL"] = base_url
            env["ANTHROPIC_API_KEY"] = api_key
        else:
            env["OPENAI_BASE_URL"] = base_url
            env["OPENAI_API_KEY"] = api_key
        return env

    def effective_api(self, model_api: str) -> str:
        """Return the API surface this harness will speak for the given model backend API.

        Default: match the model's API, so no proxy translation is needed and
        the harness connects to the backend natively. Override in harnesses
        that are locked to one API regardless of the backend -- e.g.
        ClaudeCode always speaks Anthropic Messages, so its override always
        returns "anthropic" and praxis's existing anthropic→openai filter
        handles OpenAI-speaking backends transparently.

        The runner stores the resolved value on the instance (harness.api)
        before constructing the proxy and calling build_container_env(), so
        both see the right API without needing a separate argument.
        """
        return model_api

    def setup_container(self, container: str, arch: str, cache_dir: Path) -> None:
        """Stage anything this harness needs into `container` before
        `run_container()` execs it (e.g. `podman cp`-ing in a per-arch
        binary this harness ships as). Called once per instance, after the
        benchmark's own `prepare_container()` returns the container name and
        before `run_container()` runs.

        `cache_dir` is the output-dir-level cache directory (runs/.cache by
        default), shared across all run ids, harnesses, and instances. Binary
        downloads are cached here and must be safe under concurrent runs.

        Default no-op: a harness with nothing beyond what the benchmark's
        image already provides doesn't need to override this. See
        `acb/harnesses/goose.py`'s `Goose.setup_container()` (downloads+
        caches a per-arch Linux binary, then copies it in) or
        `acb/harnesses/claude_code.py`'s equivalent for worked examples.
        """
        return None

    def setup_skills(self, container: str, arch: str, cache_dir: Path) -> None:
        """Setup skills from harness config.

        Called after setup_container() to install skills (per agentskills.io
        standard) to harness-specific directories. Skills are optional -- if
        none are configured, this is a no-op.

        Args:
            container: Podman container ID
            arch: Target architecture (arm64 or amd64)
            cache_dir: Cache directory for downloaded skills

        Raises:
            RuntimeError: If a required skill fails to install
        """
        from acb.skills import SkillInstaller

        skills = self.config.get("skills", [])
        if not skills:
            return

        installer = SkillInstaller(cache_dir / "skills")
        for skill_cfg in skills:
            result = installer.install_skill(
                skill_cfg,
                container,
                arch,
                harness_name=self.name,
                tracker=getattr(self, "_tracker", None),
                tracker_key=getattr(self, "_tracker_key", None),
            )

            # Log results
            for log in result.logs:
                import logging

                logger = logging.getLogger(__name__)
                logger.info(f"[{skill_cfg.get('name')}] {log}")

            # Handle failure
            if not result.success:
                required = skill_cfg.get("required", True)
                if required:
                    raise RuntimeError(
                        f"Failed to install required skill '{skill_cfg.get('name')}': "
                        f"{result.error}"
                    )
                else:
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Failed to install optional skill '{skill_cfg.get('name')}': "
                        f"{result.error}"
                    )

    def setup_mcp_servers(self, container: str, arch: str, cache_dir: Path) -> None:
        """Setup MCP servers from harness config.

        Called after setup_skills() to configure MCP servers. MCP servers are
        optional -- if none are configured, this is a no-op.

        Args:
            container: Podman container ID
            arch: Target architecture (arm64 or amd64)
            cache_dir: Cache directory

        Raises:
            NotImplementedError: If subclass doesn't override _write_mcp_config()
        """
        mcp_servers = self.config.get("mcp_servers", [])
        if not mcp_servers:
            return

        self._write_mcp_config(container, mcp_servers)

    @abstractmethod
    def _write_mcp_config(self, container: str, servers: list[dict]) -> None:
        """Write MCP server configuration to container in harness-specific format.

        Subclasses must implement this to write MCP configs appropriate for the
        harness. For harnesses that don't support MCP, this can be a no-op.

        Args:
            container: Podman container ID
            servers: List of MCP server configurations from harnesses.yaml
        """
        # Default: no-op for harnesses without MCP support
        pass

    @abstractmethod
    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str) -> HarnessResult:
        """Run the harness via `podman exec` inside the already-running `container`."""
