from concurrent.futures import ThreadPoolExecutor
import importlib
import io
from pathlib import Path
import tarfile

import pytest

from acb.downloads import download_policy


@pytest.fixture(params=['goose', 'opencode', 'pi', 'claude_code'])
def harness(request, tmp_path, monkeypatch):
    module = importlib.import_module('acb.harnesses.' + request.param)
    member = {'goose': 'goose', 'opencode': 'opencode', 'pi': 'pi/pi', 'claude_code': 'package/claude'}[request.param]
    calls = []
    def download(url, destination):
        calls.append(url)
        with tarfile.open(destination, 'w') as archive:
            items = {member: b'fixture executable'}
            if request.param == 'pi':
                items['pi/support.dat'] = b'necessary support file'
            for name, data in items.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o755
                archive.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(module, 'download_file', download)
    function = module.ensure_linux_files if request.param == 'pi' else module.ensure_linux_binary
    def ensure():
        return function('arm64', tmp_path, version='1.2.3')
    return request.param, ensure, calls, module


def test_concurrent_publication_and_offline_integrity(harness, tmp_path, monkeypatch):
    name, ensure, calls, module = harness
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: ensure(), range(2)))
    assert paths[0] == paths[1] and len(calls) == 1
    def unexpected(*args):
        pytest.fail('complete cache must not download')
    monkeypatch.setattr(module, 'download_file', unexpected)
    with download_policy(offline=True):
        assert ensure() == paths[0]
        target = paths[0] / 'support.dat' if name == 'pi' else paths[0]
        target.write_bytes(b'changed cache bytes')
        with pytest.raises(ValueError, match='contents changed'):
            ensure()
    assert not list(tmp_path.glob('harness-*'))


def test_failed_extraction_never_exposes_partial_binary(harness, tmp_path, monkeypatch):
    name, ensure, calls, module = harness
    method = 'extractall' if name in ('goose', 'pi') else 'extract'
    original = getattr(tarfile.TarFile, method)
    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OSError('extraction interrupted after writing binary')
    monkeypatch.setattr(tarfile.TarFile, method, interrupted)
    with pytest.raises(OSError, match='extraction interrupted'):
        ensure()
    assert not list(tmp_path.glob('harness-*'))
    assert not list(tmp_path.rglob('.acb-cache.json'))
    # Retry must extract again, rather than returning the partially written binary.
    monkeypatch.setattr(tarfile.TarFile, method, original)
    ensure()
    assert len(calls) == 2


def test_offline_miss_does_not_download_or_publish(harness, tmp_path, monkeypatch):
    from acb.downloads import download_file
    name, ensure, calls, module = harness
    monkeypatch.setattr(module, 'download_file', download_file)
    with download_policy(offline=True), pytest.raises(FileNotFoundError, match='offline'):
        ensure()
    assert not calls
    assert not list(tmp_path.rglob('.acb-cache.json'))
    assert not list(tmp_path.glob('harness-*'))


def test_publication_failure_restores_prior_incomplete_entry(tmp_path, monkeypatch):
    from acb.harnesses._cache import staged_harness_cache
    root = tmp_path / 'fixture'
    root.mkdir()
    (root / 'previous').write_text('preserve')
    original = Path.replace
    def replace(source, target):
        if source.name == 'payload':
            raise OSError('publication failed')
        return original(source, target)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(OSError, match='publication failed'):
        with staged_harness_cache(root, 'binary', 'fixture-source') as staging:
            (staging / 'binary').write_bytes(b'binary')
            (staging / 'binary').chmod(0o755)
    assert (root / 'previous').read_text() == 'preserve'
    assert not (root / '.acb-cache.json').exists()
    assert not list(tmp_path.glob('harness-*'))
