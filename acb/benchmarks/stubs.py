"""Stub adapters for additional benchmarks.

These implement the Benchmark interface enough to slot into the runner; fill in
`load_instances`, `prepare_container`, and `evaluate` per each benchmark's data
format and scoring. The generation + proxy/measurement machinery is unchanged --
only these methods differ per benchmark.
"""

from __future__ import annotations

from pathlib import Path

from acb.benchmarks.base import Benchmark, Instance, Prediction


class LiveCodeBench(Benchmark):
    """https://livecodebench.github.io -- contamination-free competitive coding.

    Tasks are self-contained problems (no repo checkout), so container-mode
    generation here wouldn't need a per-instance SWE-bench-style eval image --
    a single shared scratch image would do. Not implemented yet.
    """

    name = "livecodebench"

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        raise NotImplementedError("wire up LiveCodeBench dataset loading")

    def prepare_container(self, instance: Instance, pod: str, build_dir: Path,
                          arch: str, goose_binary: Path) -> str:
        raise NotImplementedError("build/start a LiveCodeBench scratch container")

    def collect_prediction_container(self, instance, container, model) -> Prediction:
        raise NotImplementedError("read the harness's produced solution from the container")

    def evaluate(self, predictions, run_id, output_dir) -> dict[str, bool]:
        raise NotImplementedError("run LiveCodeBench test cases against outputs")


class ScarfBench(Benchmark):
    """https://scarfbench.info"""

    name = "scarfbench"

    def load_instances(self, subset=None, limit=None) -> list[Instance]:
        raise NotImplementedError("wire up ScarfBench dataset loading")

    def prepare_container(self, instance: Instance, pod: str, build_dir: Path,
                          arch: str, goose_binary: Path) -> str:
        raise NotImplementedError("build/start a ScarfBench container")

    def collect_prediction_container(self, instance, container, model) -> Prediction:
        raise NotImplementedError("read the harness's output from the container")

    def evaluate(self, predictions, run_id, output_dir) -> dict[str, bool]:
        raise NotImplementedError("implement ScarfBench scoring")
