"""Proxy backends: the layer that measures per-request token/context usage."""

from __future__ import annotations

from pathlib import Path

from acb.config import ModelSpec
from acb.proxy.base import ProxyBackend, ProxyTags
from acb.proxy.praxis import PraxisBackend
from acb.proxy.recording import RecordingProxyBackend

_BACKENDS: dict[str, type[ProxyBackend]] = {
    "praxis": PraxisBackend,
    "recording": RecordingProxyBackend,
}


def make_backend(
    name: str,
    tags: ProxyTags,
    usage_path: Path,
    config: dict | None = None,
    model_spec: ModelSpec | None = None,
    harness_api: str = "anthropic",
) -> ProxyBackend:
    if name not in _BACKENDS:
        raise KeyError(f"unknown proxy backend {name!r}; have {list(_BACKENDS)}")
    return _BACKENDS[name](
        tags=tags, usage_path=usage_path, config=config,
        model_spec=model_spec, harness_api=harness_api,
    )


__all__ = ["ProxyBackend", "ProxyTags", "make_backend"]
