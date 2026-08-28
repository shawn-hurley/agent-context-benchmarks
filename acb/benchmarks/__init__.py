from __future__ import annotations

from acb.benchmarks.base import Benchmark, Instance, Prediction
from acb.benchmarks.scarfbench import ScarfBench
from acb.benchmarks.stubs import LiveCodeBench
from acb.benchmarks.swebench import SWEBench

_BENCHMARKS: dict[str, type[Benchmark]] = {
    "swebench": SWEBench,
    "livecodebench": LiveCodeBench,
    "scarfbench": ScarfBench,
}


def make_benchmark(name: str, config: dict | None = None) -> Benchmark:
    if name not in _BENCHMARKS:
        raise KeyError(f"unknown benchmark {name!r}; have {list(_BENCHMARKS)}")
    return _BENCHMARKS[name](config=config)


__all__ = ["Benchmark", "Instance", "Prediction", "make_benchmark"]
