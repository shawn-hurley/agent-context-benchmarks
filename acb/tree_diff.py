"""Readable source changes for benchmarks that submit a directory instead of a patch."""
from __future__ import annotations

from difflib import unified_diff
import filecmp
import os
from pathlib import Path


GENERATED_DIRS = frozenset({'.git', '.gradle', '.idea', '.venv', '__pycache__',
                            'build', 'dist', 'node_modules', 'target'})
MAX_FILE_BYTES = 512 * 1024
MAX_DIFF_CHARS = 2 * 1024 * 1024


def _source_files(root: Path) -> dict[Path, Path]:
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs
                   if name not in GENERATED_DIRS and not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(root)] = path
    return files


def source_tree_diff(before: Path, after: Path) -> str:
    """Diff saved source and candidate trees, excluding generated build output."""
    before_files, after_files = _source_files(before), _source_files(after)
    sections = []
    length = 0
    for relative in sorted(before_files.keys() | after_files.keys()):
        old_path, new_path = before_files.get(relative), after_files.get(relative)
        if old_path and new_path and filecmp.cmp(old_path, new_path, shallow=False):
            continue
        if any(path and path.stat().st_size > MAX_FILE_BYTES for path in (old_path, new_path)):
            section = f'Large file changed; inspect the saved trees: {relative}\n'
        else:
            old = old_path.read_bytes() if old_path else b''
            new = new_path.read_bytes() if new_path else b''
            if b'\x00' in old or b'\x00' in new:
                section = f'Binary file changed; inspect the saved trees: {relative}\n'
            else:
                try:
                    old_text, new_text = old.decode('utf-8'), new.decode('utf-8')
                except UnicodeDecodeError:
                    section = f'Non-UTF-8 file changed; inspect the saved trees: {relative}\n'
                else:
                    section = (f'diff --git a/{relative} b/{relative}\n' + ''.join(unified_diff(
                        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                        fromfile=f'a/{relative}' if old_path else '/dev/null',
                        tofile=f'b/{relative}' if new_path else '/dev/null')))
        if length + len(section) > MAX_DIFF_CHARS:
            sections.append('Source diff truncated; inspect the saved trees for remaining files.\n')
            break
        sections.append(section)
        length += len(section)
    return ''.join(sections)
