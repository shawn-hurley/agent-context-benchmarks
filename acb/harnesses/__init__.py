from __future__ import annotations

from acb.harnesses.base import HarnessAdapter, HarnessResult
from acb.harnesses.goose import Goose
from acb.harnesses.stubs import ClaudeCode, OpenCode, Pi

_HARNESSES: dict[str, type[HarnessAdapter]] = {
    "claude-code": ClaudeCode,
    "goose": Goose,
    "opencode": OpenCode,
    "pi": Pi,
}


def make_harness(name: str, config: dict | None = None) -> HarnessAdapter:
    if name not in _HARNESSES:
        raise KeyError(f"unknown harness {name!r}; have {list(_HARNESSES)}")
    return _HARNESSES[name](config=config)


__all__ = ["HarnessAdapter", "HarnessResult", "make_harness"]
