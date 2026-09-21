"""Unimplemented benchmark dataset adapters. Execution belongs to Harbor."""

from __future__ import annotations


from acb.benchmarks.base import Benchmark, Instance


class LiveCodeBench(Benchmark):
    """https://livecodebench.github.io -- contamination-free competitive coding.

    Tasks are self-contained problems (no repo checkout), so container-mode
    generation here wouldn't need a per-instance SWE-bench-style eval image --
    a single shared scratch image would do. Not implemented yet.
    """

    name = "livecodebench"

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        raise NotImplementedError("wire up LiveCodeBench dataset loading")
