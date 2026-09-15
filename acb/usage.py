"""Core usage record schema and metric derivation.

The atomic unit of measurement is one *LLM request* (one row in ``usage.jsonl``).
All four requested context metrics are derived from this stream:

* total tokens        -> fresh input + output + cache creation (cache reads excluded)
* context tokens      -> input + cache reads + cache creation
* peak context        -> max prompt size (input + cache_read + cache_creation)
* per-turn growth     -> prompt size ordered by turn_index
* cache efficiency    -> cache_read / prompt size

Keeping the atom at request granularity means we never have to re-run a harness
to compute a new metric -- it is all a re-aggregation of the same log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Iterator


def normalize_benchmark_metric(metric: dict) -> dict:
    """Normalize OpenAI's inclusive prompt count to exclusive token buckets.

    Anthropic input counts already exclude cache reads/creation. Praxis's
    OpenAI metrics retain prompt_tokens as input_tokens, so cached reads must
    be subtracted once. The marker makes repeated normalization idempotent.
    """
    result = dict(metric)
    endpoint = (metric.get("endpoint") or "").split("?", 1)[0].rstrip("/")
    semantics = metric.get("input_tokens_semantics")
    inclusive = semantics == "inclusive" or (
        semantics is None and endpoint.endswith(("/chat/completions", "/completions", "/responses"))
    )
    if inclusive:
        prompt = metric.get("input_tokens", 0) or 0
        cached = min(prompt, metric.get("cache_read_input_tokens", 0) or 0)
        result["input_tokens"] = prompt - cached
        result["cache_read_input_tokens"] = cached
    result["input_tokens_semantics"] = "exclusive"
    return result


@dataclass
class UsageRecord:
    """One LLM request observed by the proxy."""

    # correlation / tags (baked in by the proxy the runner launches per instance)
    run_id: str
    benchmark: str
    harness: str
    model: str
    instance_id: str
    turn_index: int  # 0-based order of requests within this instance run

    # Exclusive token buckets: input_tokens excludes cache read/creation.
    # OpenAI prompt_tokens (inclusive of cached input) is normalized at ingestion.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    # request context
    request_id: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    ts: float | None = None  # epoch seconds
    source: str | None = None  # which proxy backend produced this row

    @property
    def prompt_tokens(self) -> int:
        """Tokens occupying the context window when this request was sent."""
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


def write_records(path: str | Path, records: Iterable[UsageRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(r.to_json() + "\n")


def read_records(path: str | Path) -> Iterator[UsageRecord]:
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield UsageRecord(**json.loads(line))


@dataclass
class InstanceMetrics:
    """Derived context metrics for one (harness, model, benchmark, instance) run."""

    run_id: str
    benchmark: str
    harness: str
    model: str
    instance_id: str
    turns: int = 0
    total_input: int = 0
    total_output: int = 0
    total_cache_read: int = 0
    total_cache_creation: int = 0
    total_tokens: int = 0  # fresh input/creation + output; not a billing total
    context_tokens: int = 0  # tokens occupying the context window across requests
    peak_context: int = 0
    per_turn_prompt: list[int] = field(default_factory=list)
    cache_efficiency: float = 0.0  # cache_read / total prompt tokens sent
    resolved: bool | None = None  # filled in from benchmark evaluation

    @classmethod
    def from_records(cls, records: list[UsageRecord]) -> "InstanceMetrics":
        records = sorted(records, key=lambda r: r.turn_index)
        first = records[0]
        m = cls(
            run_id=first.run_id,
            benchmark=first.benchmark,
            harness=first.harness,
            model=first.model,
            instance_id=first.instance_id,
            turns=len(records),
        )
        prompt_total = 0
        for r in records:
            m.total_input += r.input_tokens
            m.total_output += r.output_tokens
            m.total_cache_read += r.cache_read_tokens
            m.total_cache_creation += r.cache_creation_tokens
            m.per_turn_prompt.append(r.prompt_tokens)
            m.peak_context = max(m.peak_context, r.prompt_tokens)
            prompt_total += r.prompt_tokens
        # Cache reads are reused prompt tokens. Keep them in context_tokens for
        # context/caching analysis, but exclude them from fresh-token totals.
        m.total_tokens = m.total_input + m.total_output + m.total_cache_creation
        m.context_tokens = prompt_total
        m.cache_efficiency = (m.total_cache_read / prompt_total) if prompt_total else 0.0
        return m
