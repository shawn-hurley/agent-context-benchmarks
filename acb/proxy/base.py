"""Proxy backend interface.

A ProxyBackend is launched *per instance run* by the runner. It exposes an
OpenAI/Anthropic-compatible ``base_url`` that the harness is pointed at, forwards
traffic to the real provider, and records one :class:`UsageRecord` per LLM
request. Because one backend instance serves exactly one benchmark instance, the
arrival order of requests is the ``turn_index`` and no per-request header
correlation is needed (CLI harnesses cannot inject custom headers).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from acb.config import ModelSpec
from acb.usage import UsageRecord, read_records


@dataclass
class ProxyTags:
    run_id: str
    benchmark: str
    harness: str
    model: str
    instance_id: str


class ProxyBackend(ABC):
    """Base class for a per-instance recording proxy."""

    name: str = "base"

    def __init__(
        self,
        tags: ProxyTags,
        usage_path: Path,
        config: dict | None = None,
        model_spec: ModelSpec | None = None,
        harness_api: str = "anthropic",
    ):
        self.tags = tags
        self.usage_path = Path(usage_path)
        self.config = config or {}
        self.model_spec = model_spec  # backend the proxy connects to
        self.harness_api = harness_api  # api the harness speaks (inbound)
        self._base_url: str | None = None

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            raise RuntimeError("proxy not started")
        return self._base_url

    @property
    def api_key(self) -> str:
        """Dummy key handed to the harness; real key is injected by the proxy."""
        return "acb-proxy-placeholder"

    @abstractmethod
    def start(self) -> str:
        """Start the proxy and return its base_url."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the proxy and flush recorded usage to ``usage_path``."""

    def collect(self) -> list[UsageRecord]:
        """Read back the usage rows this backend wrote for its instance."""
        if not self.usage_path.exists():
            return []
        return [r for r in read_records(self.usage_path) if r.instance_id == self.tags.instance_id]

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
