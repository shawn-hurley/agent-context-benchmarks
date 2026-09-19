"""Atomic, source-specific caches for remotely fetched skill directories."""
import hashlib
import json
from pathlib import Path
import tempfile

from acb.harnesses._cache import binary_cache_lock
from acb.skills.validator import validate_skill_md


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'unsupported cached skill entry: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'mode': path.stat().st_mode & 0o777,
            }
    return result


def cached_skill(cache_dir, source, fetch):
    key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    cache = Path(cache_dir) / 'verified'
    cache.mkdir(parents=True, exist_ok=True)
    root = cache / key
    with binary_cache_lock(cache, key):
        if root.exists():
            try:
                record = json.loads((root / 'manifest.json').read_text())
            except (OSError, ValueError) as error:
                raise ValueError(f'invalid skill cache manifest: {root}') from error
            if (not isinstance(record, dict) or record.get('source') != source
                    or not record.get('files') or not (root / 'payload').is_dir()
                    or record.get('files') != inventory(root / 'payload')):
                raise ValueError(f'skill cache contents changed: {root}')
            return root / 'payload'
        with tempfile.TemporaryDirectory(prefix='skill-', dir=cache) as temporary:
            staging = Path(temporary)
            downloaded = Path(fetch(staging / 'download'))
            error = validate_skill_md(downloaded / 'SKILL.md')
            if error:
                raise ValueError(error)
            payload = staging / 'entry'
            payload.mkdir()
            downloaded.replace(payload / 'payload')
            record = {'source': source, 'files': inventory(payload / 'payload')}
            (payload / 'manifest.json').write_text(json.dumps(record, indent=2))
            payload.replace(root)
            return root / 'payload'
