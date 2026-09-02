"""Shared helpers for host-side harness binary caches."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Iterator


@contextmanager
def binary_cache_lock(cache_dir: Path, key: str) -> Iterator[None]:
    """Serialize writes to a cached binary across concurrent ACB processes."""
    locks_dir = cache_dir / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{key}.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
