"""Token cost estimation: USD per token, applied to recorded usage.

Kept out of acb/report.py's core metrics pipeline deliberately -- cost rates
change over time and config/costs.yaml is the source of truth for "current"
pricing, so cost is a pure re-aggregation over usage.jsonl computed whenever
it's needed (e.g. by acb/html_report.py), the same "never re-run to add a
metric" philosophy acb/usage.py already documents for every other metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

COSTS_PATH = Path(__file__).resolve().parent.parent / "config" / "costs.yaml"


@dataclass
class ModelCost:
    """USD per 1,000,000 tokens, by token kind."""

    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    cache_read_per_1m: float = 0.0
    cache_write_per_1m: float = 0.0


def load_cost_table(path: Path = COSTS_PATH) -> dict[str, ModelCost]:
    """model id -> ModelCost. A model absent from the file is absent here
    too -- callers should treat that as "no cost data", not a $0 rate.
    """
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    table: dict[str, ModelCost] = {}
    for model, fields in (data.get("models") or {}).items():
        table[model] = ModelCost(**(fields or {}))
    return table


def estimate_cost(
    cost: ModelCost,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """USD cost of one request given its token breakdown."""
    return (
        input_tokens / 1_000_000 * cost.input_per_1m
        + output_tokens / 1_000_000 * cost.output_per_1m
        + cache_read_tokens / 1_000_000 * cost.cache_read_per_1m
        + cache_creation_tokens / 1_000_000 * cost.cache_write_per_1m
    )
