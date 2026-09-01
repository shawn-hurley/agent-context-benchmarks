"""Agent harness interface.

A HarnessAdapter knows how to invoke one CLI agent (claude-code, goose,
opencode, pi) headlessly *inside a running container*, pointed at the proxy's
base_url so every LLM call is measured. It returns the agent's raw output; the
Benchmark turns the mutated container's checkout into a Prediction.

Generation is container-only (see acb/runner.py, acb/benchmarks/swebench.py):
the harness runs inside the same SWE-bench eval image evaluation will grade
the patch in, so its dev environment matches evaluation exactly, rather than
whatever happens to be on the machine running `acb`. A harness needs a Linux
build (or an image with its runtime baked in) to support this -- `goose`
ships a static-ish Linux binary; `claude-code` ships a standalone per-arch
native binary too (see acb/harnesses/claude_code.py). `opencode`/`pi` are
still stubs in acb/harnesses/stubs.py pending their own container port.

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

        `cache_dir` is the run-level cache directory (runs/<run_id>/.cache) shared
        across all harnesses and instances in the run. Binary downloads are cached
        here to avoid concurrent download race conditions when max_workers > 1.

        Default no-op: a harness with nothing beyond what the benchmark's
        image already provides doesn't need to override this. See
        `acb/harnesses/goose.py`'s `Goose.setup_container()` (downloads+
        caches a per-arch Linux binary, then copies it in) or
        `acb/harnesses/claude_code.py`'s equivalent for worked examples.
        """
        return None

    @abstractmethod
    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str) -> HarnessResult:
        """Run the harness via `podman exec` inside the already-running `container`."""
