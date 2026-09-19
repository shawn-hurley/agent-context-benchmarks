from copy import deepcopy
from pathlib import Path

import pytest

from acb.preparation import prepare_assets


def plan(tmp_path):
    return {
        'manifest': {'tasks': [{'id': 'arm'}, {'id': 'intel'}]},
        'runtime_contracts': {
            'arm': {'arch': 'arm64', 'python_path': '/usr/bin/python3', 'workdir': '/work', 'user': '0'},
            'intel': {'arch': 'amd64', 'python_path': '/opt/python/bin/python', 'workdir': '/testbed', 'user': '0'},
        },
        'harnesses': {'claude-code': {'execution_integrations': [{'name': 'rtk'}]}},
        'cache_dir': str(tmp_path), 'environment': 'podman', 'offline': False,
        'benchmark_config': {'praxis_image': 'fixture-praxis:fixed'},
    }


def test_assets_follow_task_architecture_and_interpreter(tmp_path, monkeypatch):
    calls = []
    def build(cache, engine, arch, offline):
        calls.append(arch)
        return f'/cache/{arch}/rtk', f'digest-{arch}'
    monkeypatch.setattr('acb.preparation.ensure_rtk', build)
    original = plan(tmp_path)
    before = deepcopy(original)
    prepared = prepare_assets(original)
    assert original == before
    assert calls == ['arm64', 'amd64']
    for task_id, runtime in original['runtime_contracts'].items():
        extension = prepared['task_plans'][task_id]['harnesses']['claude-code']['execution_integrations'][0]
        assert extension['binary_path'] == f"/cache/{runtime['arch']}/rtk"
        assert extension['python_path'] == runtime['python_path']


def test_missing_task_inspection_prevents_asset_build(tmp_path):
    value = plan(tmp_path)
    del value['runtime_contracts']['intel']
    with pytest.raises(ValueError, match='inspect every'):
        prepare_assets(value)


def test_explicit_architecture_conflict_is_rejected(tmp_path):
    value = plan(tmp_path)
    value['benchmark_config']['architecture'] = 'amd64'
    with pytest.raises(ValueError, match='conflicts'):
        prepare_assets(value)


def test_language_environment_is_frozen_into_each_harness_plan(tmp_path, monkeypatch):
    value = plan(tmp_path)
    value['harnesses'] = {name: {} for name in ['goose', 'pi', 'opencode', 'claude-code']}
    for runtime in value['runtime_contracts'].values():
        runtime['language_environment'] = {'conda_env': 'testbed'}
    prepared = prepare_assets(value)
    for task in prepared['task_plans'].values():
        assert all(config['conda_env'] == 'testbed' for config in task['harnesses'].values())
    assert all('conda_env' not in config for config in value['harnesses'].values())


def test_provider_images_are_pinned_without_mutating_requested_plan(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from acb.preparation import freeze_provider_images
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps([
            {'Id': 'sha256:' + 'a' * 64, 'Architecture': 'arm64', 'Os': 'linux'}]))
    monkeypatch.setattr('acb.preparation.subprocess.run', run)
    original = plan(tmp_path)
    result = freeze_provider_images(original)
    assert 'provider_images' not in original
    assert result['provider_images']['acb-praxis']['image_id'] == 'sha256:' + 'a' * 64
    assert calls == [['podman', 'image', 'inspect', 'fixture-praxis:fixed']]


def test_missing_provider_is_not_pulled_offline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from acb.preparation import freeze_provider_images
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr('acb.preparation.subprocess.run', run)
    value = plan(tmp_path)
    value['offline'] = True
    with pytest.raises(FileNotFoundError, match='offline'):
        freeze_provider_images(value)
    assert len(calls) == 1


def test_harbor_uses_acb_interpreter(tmp_path):
    import os
    import subprocess
    import sys
    from acb.harbor.backend import runtime
    python = runtime({'benchmark_config': {}})
    assert python == Path(os.path.abspath(sys.executable))
    subprocess.run([str(python), '-c', 'import yaml, harbor'], check=True)


def test_harbor_rejects_removed_interpreter_override():
    from acb.harbor.backend import runtime
    with pytest.raises(ValueError, match='benchmark.python was removed'):
        runtime({'benchmark_config': {'python': '/other/python'}})


def rtk_cache(tmp_path, arch='arm64'):
    import hashlib
    import json
    from acb.preparation import RTK_RECIPE, RTK_COMMIT
    key = hashlib.sha256((RTK_RECIPE + arch).encode()).hexdigest()
    root = tmp_path / 'rtk' / key
    root.mkdir(parents=True)
    binary = root / 'rtk'
    binary.write_bytes(b'cached-binary')
    binary.chmod(0o755)
    record = {'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'architecture': arch, 'source_commit': RTK_COMMIT, 'recipe': key}
    (root / 'manifest.json').write_text(json.dumps(record))
    return root, record


@pytest.mark.parametrize('damage', ['architecture', 'source_commit', 'recipe', 'sha256', 'json', 'executable'])
def test_rtk_offline_rejects_invalid_cache_without_engine_calls(tmp_path, monkeypatch, damage):
    import json
    from acb.preparation import ensure_rtk
    root, record = rtk_cache(tmp_path)
    if damage == 'executable':
        (root / 'rtk').chmod(0o644)
    elif damage == 'json':
        (root / 'manifest.json').write_text('{')
    else:
        record[damage] = 'incorrect'
        (root / 'manifest.json').write_text(json.dumps(record))
    def unexpected(*args, **kwargs):
        pytest.fail('offline validation must not invoke the engine')
    monkeypatch.setattr('acb.preparation.subprocess.run', unexpected)
    monkeypatch.setattr('acb.preparation.subprocess.check_output', unexpected)
    with pytest.raises(ValueError, match='RTK'):
        ensure_rtk(tmp_path, 'podman', 'arm64', True)


def test_valid_rtk_cache_is_usable_offline(tmp_path, monkeypatch):
    from acb.preparation import ensure_rtk
    root, record = rtk_cache(tmp_path)
    def unexpected(*args, **kwargs):
        pytest.fail('cached RTK must not invoke the engine')
    monkeypatch.setattr('acb.preparation.subprocess.run', unexpected)
    assert ensure_rtk(tmp_path, 'podman', 'arm64', True) == (str(root / 'rtk'), record['sha256'])


@pytest.mark.parametrize('failure', [None, 'copy', 'publish'])
def test_rtk_publication_preserves_incomplete_cache_until_ready(tmp_path, monkeypatch, failure):
    import subprocess
    from acb.preparation import ensure_rtk
    root, _ = rtk_cache(tmp_path)
    (root / 'manifest.json').unlink()
    calls = []
    def run(command, **kwargs):
        calls.append(command[1])
        if command[1] == 'cp':
            assert (root / 'rtk').read_bytes() == b'cached-binary'
            assert not (root / 'manifest.json').exists()
            if failure == 'copy':
                raise subprocess.CalledProcessError(1, command)
            target = Path(command[-1])
            target.write_bytes(b'new-binary')
            target.chmod(0o755)
    monkeypatch.setattr('acb.preparation.subprocess.run', run)
    monkeypatch.setattr('acb.preparation.subprocess.check_output', lambda *a, **k: 'container-id')
    replace = Path.replace
    def publish(path, target):
        if path.name == 'artifact':
            assert (path / 'rtk').exists() and (path / 'manifest.json').exists()
            if failure == 'publish':
                raise OSError('publication failed')
        return replace(path, target)
    monkeypatch.setattr(Path, 'replace', publish)
    if failure:
        with pytest.raises((OSError, subprocess.CalledProcessError)):
            ensure_rtk(tmp_path, 'podman', 'arm64', False)
        assert (root / 'rtk').read_bytes() == b'cached-binary'
        assert not (root / 'manifest.json').exists()
    else:
        binary, digest = ensure_rtk(tmp_path, 'podman', 'arm64', False)
        assert Path(binary).read_bytes() == b'new-binary'
        assert ensure_rtk(tmp_path, 'podman', 'arm64', True) == (binary, digest)
    assert calls == ['build', 'cp', 'rm']
    assert list(root.parent.iterdir()) == [root]


def test_rtk_concurrent_preparation_builds_once(tmp_path, monkeypatch):
    import multiprocessing
    from acb.preparation import ensure_rtk
    context = multiprocessing.get_context('fork')
    start = context.Event()
    calls = tmp_path / 'builds.txt'
    def run(command, **kwargs):
        if command[1] == 'build':
            with calls.open('a') as handle:
                handle.write('build\n')
        elif command[1] == 'cp':
            target = Path(command[-1])
            target.write_bytes(b'complete-binary')
            target.chmod(0o755)
    monkeypatch.setattr('acb.preparation.subprocess.run', run)
    monkeypatch.setattr('acb.preparation.subprocess.check_output', lambda *a, **kw: 'container-id')
    def prepare():
        assert start.wait(5)
        binary, _ = ensure_rtk(tmp_path, 'podman', 'arm64', False)
        assert Path(binary).read_bytes() == b'complete-binary'
    processes = [context.Process(target=prepare) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    assert calls.read_text() == 'build\n'
    ensure_rtk(tmp_path, 'podman', 'arm64', True)


def test_incomplete_rtk_cache_cannot_trigger_offline_build(tmp_path, monkeypatch):
    from acb.preparation import ensure_rtk
    root, _ = rtk_cache(tmp_path)
    (root / 'manifest.json').unlink()
    def unexpected(*args, **kwargs):
        pytest.fail('offline preparation must not build missing artifacts')
    monkeypatch.setattr('acb.preparation.subprocess.run', unexpected)
    with pytest.raises(FileNotFoundError, match='offline'):
        ensure_rtk(tmp_path, 'podman', 'arm64', True)


def test_amd64_release_checksum_atomic_cache_and_offline(tmp_path, monkeypatch):
    import hashlib
    import io
    import tarfile
    from acb import preparation
    payload = b'fixture executable'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        info = tarfile.TarInfo('rtk')
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    data = buffer.getvalue()
    monkeypatch.setattr(preparation, 'RTK_AMD64_SHA256', hashlib.sha256(data).hexdigest())
    downloads = []
    def download(url, path):
        downloads.append(url)
        path.write_bytes(data)
    monkeypatch.setattr('acb.downloads.download_file', download)
    monkeypatch.setattr(preparation.subprocess, 'run', lambda *a, **kw: pytest.fail('must not invoke an emulated compiler'))
    binary, digest = preparation.ensure_rtk(tmp_path, 'podman', 'amd64', False)
    assert Path(binary).read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert preparation.ensure_rtk(tmp_path, 'podman', 'amd64', True) == (binary, digest)
    assert downloads == [preparation.RTK_AMD64_URL]
    other = tmp_path / 'corrupt'
    monkeypatch.setattr('acb.downloads.download_file', lambda url, path: path.write_bytes(b'corrupt'))
    with pytest.raises(ValueError, match='checksum'):
        preparation.ensure_rtk(other, 'podman', 'amd64', False)
    assert not list(other.rglob('manifest.json'))
