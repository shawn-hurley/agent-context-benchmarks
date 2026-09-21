"""Benchmark task selection and native prediction records.

Harbor owns environments and execution. Adapters enumerate source instances;
acb.harbor.benchmark_tasks exports them and the custom verifier grades outputs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
