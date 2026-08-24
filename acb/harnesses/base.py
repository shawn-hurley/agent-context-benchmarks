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
ships a static-ish Linux binary and is the only harness implemented today;
`claude-code`/`opencode`/`pi` are stubs in acb/harnesses/stubs.py pending
their own container port.
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

    @abstractmethod
    def run_container(self, prompt: str, container: str, model: str, env: dict[str, str],
                       out_dir: Path, instance_id: str) -> HarnessResult:
        """Run the harness via `podman exec` inside the already-running `container`."""
