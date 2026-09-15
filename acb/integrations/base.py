"""Controller-side lifecycle shared by execution integrations and model middleware."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrationContext:
    container: str
    arch: str
    harness: str
    harness_version: str
    cache_dir: Path
    artifact_dir: Path


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    api_key: str
    api: str


class Integration:
    name: str
    category: str
    # Exclusive resources prevent silently overwriting shell or process settings.
    resources: frozenset[str] = frozenset()

    def __init__(self, config: dict):
        self.config = dict(config)
        self.metadata: dict = {}

    def validate(self, harness: str, harness_config: dict) -> None:
        raise NotImplementedError

    def install(self, context: IntegrationContext) -> None:
        raise NotImplementedError

    def activate(self, context: IntegrationContext) -> dict[str, str]:
        return {}

    def verify(self, context: IntegrationContext) -> dict:
        raise NotImplementedError

    def start(self, context: IntegrationContext, upstream: ModelEndpoint) -> ModelEndpoint:
        """Middleware returns its local endpoint, forwarding to upstream.

        Execution integrations return upstream unchanged. Never mutate upstream
        credentials or infer permission to use an external provider endpoint.
        """
        return upstream

    def health_check(self, context: IntegrationContext) -> None:
        """Raise if a started middleware service is unavailable."""

    def collect(self, context: IntegrationContext) -> None:
        """Export evidence before containers are removed, including failed runs."""

    def stop(self, context: IntegrationContext) -> None:
        """Stop managed services; must tolerate partially completed startup."""
