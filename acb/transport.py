"""Environment commands used by existing synchronous harness adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class EnvironmentTransport(Protocol):
    def capture(self, argv: list[str], workdir: str | None = None) -> str: ...
    def upload(self, source: Path, destination: str) -> None: ...
    def download(self, source: str, destination: Path) -> None: ...
    def execute(self, command: 'EnvironmentCommand', **kwargs): ...


@dataclass(frozen=True)
class EnvironmentCommand:
    transport: EnvironmentTransport
    argv: tuple[str, ...]
    env: dict[str, str]
    workdir: str | None


def command(container, argv: list[str], env: dict[str, str], workdir: str):
    if isinstance(container, EnvironmentTransport):
        return EnvironmentCommand(container, tuple(argv), dict(env), workdir)
    result = ["podman", "exec", "-i"]
    for key, value in env.items():
        result += ["-e", f"{key}={value}"]
    return [*result, "--workdir", workdir, container, *argv]
