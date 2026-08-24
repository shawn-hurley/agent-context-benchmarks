"""Stub adapters for the harnesses not yet wired up for container-mode generation.

Generation is container-only (see acb/harnesses/base.py, acb/runner.py): each
harness needs `run_container()` (exec inside the running SWE-bench eval
container via `podman exec`) and, if needed, a `build_container_env()`
override. `goose` is the only one implemented today (acb/harnesses/goose.py) --
it ships a single static-ish Linux binary that's trivial to `podman cp` into
the container.

The others need more than a binary copy:
* claude-code -> the `claude` CLI is a Node.js package
  (`@anthropic-ai/claude-code`), not a standalone binary -- needs either
  Node.js baked into (or copied alongside a portable Node build into) the
  container image, then `podman exec ... claude -p <prompt> ...`.
* opencode    -> `opencode run <prompt>`; check whether it ships standalone
  Linux binaries the way goose does before assuming a Node/bun runtime is
  needed too.
* pi          -> project-specific CLI; same investigation needed.
"""

from __future__ import annotations

from pathlib import Path

from acb.harnesses.base import HarnessAdapter, HarnessResult


class ClaudeCode(HarnessAdapter):
    name = "claude-code"
    api = "anthropic"

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str) -> HarnessResult:
        raise NotImplementedError(
            "claude-code container-mode generation not implemented: the `claude` "
            "CLI needs a Node.js runtime in the image, unlike goose's static binary"
        )


class OpenCode(HarnessAdapter):
    name = "opencode"
    api = "openai"

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str) -> HarnessResult:
        raise NotImplementedError("wire up `opencode run` invocation via podman exec")


class Pi(HarnessAdapter):
    name = "pi"
    api = "anthropic"

    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str) -> HarnessResult:
        raise NotImplementedError("wire up pi invocation via podman exec")
