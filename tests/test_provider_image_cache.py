import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from acb.image_cache import prepare_image


def fixture(tmp_path, monkeypatch):
    context = tmp_path / 'source'
    context.mkdir()
    (context / 'Containerfile').write_text('FROM scratch\nCOPY payload /payload\n')
    (context / 'payload').write_text('original')
    plan = {'cache_dir': str(tmp_path / 'cache'), 'environment': 'podman', 'offline': False}
    images, calls = {}, []
    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ['image', 'inspect']:
            image = images.get(command[-1])
            return SimpleNamespace(returncode=0 if image else 1, stdout=json.dumps([image]))
        assert command[1] == 'build'
        # Mutating the source at build time cannot change the supplied snapshot.
        (context / 'payload').write_text('modified after snapshot')
        assert (Path(command[-1]) / 'payload').read_text() == 'original'
        reference = command[command.index('-t') + 1]
        labels = dict(command[i+1].split('=', 1) for i, arg in enumerate(command) if arg == '--label')
        images[reference] = {'Id': 'sha256:' + 'a' * 64, 'Architecture': 'arm64',
                             'Os': 'linux', 'Config': {'Labels': labels}}
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('acb.image_cache.subprocess.run', run)
    return context, plan, images, calls


def test_build_snapshot_and_offline_reuse(tmp_path, monkeypatch):
    context, plan, images, calls = fixture(tmp_path, monkeypatch)
    identity = prepare_image(context, 'Containerfile', 'fixture', plan)
    assert identity == 'sha256:' + 'a' * 64
    (context / 'payload').write_text('original')
    plan['offline'] = True
    assert prepare_image(context, 'Containerfile', 'fixture', plan) == identity
    assert sum(call[1] == 'build' for call in calls) == 1
    manifest = next((Path(plan['cache_dir']) / 'provider-images').glob('*.json'))
    assert json.loads(manifest.read_text())['image']['image_id'] == identity
    assert not list(manifest.parent.glob('context-*'))


def test_offline_missing_or_changed_inputs_never_build(tmp_path, monkeypatch):
    context, plan, images, calls = fixture(tmp_path, monkeypatch)
    plan['offline'] = True
    with pytest.raises(FileNotFoundError, match='offline'):
        prepare_image(context, 'Containerfile', 'fixture', plan)
    assert all(call[1:3] == ['image', 'inspect'] for call in calls)
    plan['offline'] = False
    prepare_image(context, 'Containerfile', 'fixture', plan)
    plan['offline'] = True
    # Source differs from its build snapshot now.
    with pytest.raises(FileNotFoundError, match='offline'):
        prepare_image(context, 'Containerfile', 'fixture', plan)
    assert sum(call[1] == 'build' for call in calls) == 1


@pytest.mark.parametrize('damage', ['image', 'label', 'manifest'])
def test_changed_provider_cache_is_rejected(tmp_path, monkeypatch, damage):
    context, plan, images, calls = fixture(tmp_path, monkeypatch)
    prepare_image(context, 'Containerfile', 'fixture', plan)
    (context / 'payload').write_text('original')
    if damage == 'image':
        next(iter(images.values()))['Id'] = 'sha256:' + 'b' * 64
    elif damage == 'label':
        next(iter(images.values()))['Config']['Labels'] = {}
    else:
        next((Path(plan['cache_dir']) / 'provider-images').glob('*.json')).write_text('{}')
    plan['offline'] = True
    with pytest.raises(ValueError, match='provenance|changed|manifest'):
        prepare_image(context, 'Containerfile', 'fixture', plan)
    assert sum(call[1] == 'build' for call in calls) == 1


def test_failed_build_does_not_publish_manifest(tmp_path, monkeypatch):
    import subprocess
    context, plan, images, calls = fixture(tmp_path, monkeypatch)
    def run(command, **kwargs):
        if command[1] == 'build':
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr('acb.image_cache.subprocess.run', run)
    with pytest.raises(subprocess.CalledProcessError):
        prepare_image(context, 'Containerfile', 'fixture', plan)
    cache = Path(plan['cache_dir']) / 'provider-images'
    assert not list(cache.glob('*.json')) and not list(cache.glob('context-*'))


def test_context_symlinks_rejected_before_engine_access(tmp_path, monkeypatch):
    context, plan, images, calls = fixture(tmp_path, monkeypatch)
    (context / 'linked').symlink_to(context / 'payload')
    with pytest.raises(ValueError, match='unsupported'):
        prepare_image(context, 'Containerfile', 'fixture', plan)
    assert not calls
