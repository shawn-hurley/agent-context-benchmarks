"""Identity tags attached to recorded model requests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProxyTags:
    run_id: str
    benchmark: str
    harness: str
    model: str
    instance_id: str
