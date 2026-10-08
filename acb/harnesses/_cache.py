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


def _inventory(root: Path):
    import hashlib
    import os
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if relative == '.acb-cache.json':
            continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'harness cache link escapes its directory: {relative}')
            result[relative] = {'link': os.readlink(path)}
        elif path.is_file():
            result[relative] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'mode': path.stat().st_mode & 0o777}
        elif not path.is_dir():
            raise ValueError(f'unsupported harness cache entry: {relative}')
    return result


def harness_cache_ready(directory: Path, executable: str) -> bool:
    import json
    marker = directory / '.acb-cache.json'
    if not marker.exists():
        return False
    if marker.exists():
        try:
            record = json.loads(marker.read_text())
        except (OSError, ValueError) as error:
            raise ValueError(f'invalid harness cache manifest: {directory}') from error
        if (not isinstance(record, dict) or record.get('key') != directory.name
                or record.get('executable') != executable or not record.get('files')
                or record.get('files') != _inventory(directory)):
            raise ValueError(f'harness cache contents changed: {directory}')
    binary = directory / executable
    if not binary.is_file():
        if marker.exists():
            raise ValueError(f'harness cache executable is missing: {binary}')
        return False
    if binary.stat().st_size == 0 or not binary.stat().st_mode & 0o111:
        raise ValueError(f'harness cache executable is empty or not executable: {binary}')
    return True


@contextmanager
def staged_harness_cache(directory: Path, executable: str, source: str):
    """Called under the cache lock; publish extraction and inventory together."""
    import json
    import tempfile
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='harness-', dir=directory.parent) as temporary:
        staging = Path(temporary)
        payload = staging / 'payload'
        payload.mkdir()
        yield payload
        marker = payload / '.acb-cache.json'
        if marker.exists() or marker.is_symlink():
            raise ValueError('harness archive contains reserved cache metadata')
        binary = payload / executable
        if not binary.is_file() or not binary.stat().st_size or not binary.stat().st_mode & 0o111:
            raise ValueError(f'harness archive lacks a usable executable: {executable}')
        record = {'key': directory.name, 'executable': executable, 'source': source,
                  'files': _inventory(payload)}
        marker.write_text(json.dumps(record, indent=2))
        previous = staging / 'previous'
        if directory.exists():
            directory.replace(previous)
        try:
            payload.replace(directory)
        except BaseException:
            if previous.exists():
                previous.replace(directory)
            raise
