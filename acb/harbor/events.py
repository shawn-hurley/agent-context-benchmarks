"""Structured worker events used by ACB's host-side terminal UI."""
from __future__ import annotations

import json
import os
from pathlib import Path


def emit(kind: str, **fields) -> None:
    """Append one complete event when the host requested an event stream."""
    destination = os.environ.get("ACB_HARBOR_EVENT_PATH")
    if not destination:
        return
    record = {"event": kind, **fields}
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        # UI telemetry must never alter the benchmark result.
        return
