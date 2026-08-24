"""Benchmark interface.

A Benchmark abstracts a task suite so the runner is identical across SWE-bench,
LiveCodeBench, ScarfBench, etc. It knows how to enumerate tasks, prepare a
*container* for the harness to edit code in, phrase the prompt, and score the
harness's output.

Generation is container-only (see acb/runner.py): the harness always runs
inside a container the Benchmark prepares, not a plain host directory, so its
dev environment matches whatever evaluation will grade the patch in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Instance:
    """A single benchmark task."""

    instance_id: str
    prompt: str
    # optional metadata used by evaluation / workspace prep
    repo: str | None = None
    base_commit: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Prediction:
    """A harness's answer for one instance, in the benchmark's expected shape."""

    instance_id: str
    model_name_or_path: str
    # SWE-bench-style patch; other benchmarks may use `output` instead.
    model_patch: str | None = None
    output: str | None = None
    error: str | None = None


class Benchmark(ABC):
    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def load_instances(self, subset: list[str] | None = None, limit: int | None = None) -> list[Instance]:
        ...

    @abstractmethod
    def prepare_container(self, instance: Instance, pod: str, build_dir: Path,
                          arch: str, goose_binary: Path) -> str:
        """Materialize a running container (attached to `pod`) the harness
        will edit code in; return its name.

        The `build_dir`/`arch`/`goose_binary` params are runner-computed
        orchestration details (image build scratch space, target
        architecture, the harness's Linux binary) rather than a fully
        benchmark-agnostic signature -- there's only one real implementation
        (SWEBench) today, so this leans on its concrete needs rather than a
        speculative generic shape. Revisit if/when a second container-mode
        benchmark exists.
        """

    @abstractmethod
    def collect_prediction_container(self, instance: Instance, container: str, model: str) -> Prediction:
        """Turn the harness's post-run container state into a Prediction."""

    @abstractmethod
    def evaluate(self, predictions: list[Prediction], run_id: str, output_dir: Path) -> dict[str, bool]:
        """Score predictions; return {instance_id: resolved}."""
