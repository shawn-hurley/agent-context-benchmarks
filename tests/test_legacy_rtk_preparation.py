from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from acb.config import Registries, RunConfig
from acb.preparation import prepare, _prepare_assets_for_runtime
from acb.resolver import resolve
from acb.integrations.runtime import prepare_legacy_rtk_runtime


def configuration(tmp_path, arch):
    cfg = RunConfig('rtk', 'fixture', ['goose', 'pi', 'opencode', 'claude-code'], 'local',
                    extensions=['rtk'], output_dir=str(tmp_path / 'runs'))
    registry = Registries({}, {'fixture': {'image_arch': arch, 'praxis_image': 'existing'}},
                          {'models': {'local': {'api': 'openai', 'endpoint': 'localhost:8000'}}})
    return cfg, registry


@pytest.mark.parametrize('arch', ['arm64', 'amd64'])
def test_automatic_rtk_uses_same_assets_for_both_backends(tmp_path, monkeypatch, arch):
    binary = tmp_path / 'rtk'
    binary.write_bytes(b'fixture artifact')
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    calls = []
    def ensure(cache, engine, requested_arch, offline):
        calls.append((requested_arch, engine, offline))
        return str(binary), digest
    monkeypatch.setattr('acb.preparation.ensure_rtk', ensure)
    cfg, registry = configuration(tmp_path, arch)
    plan = resolve(cfg, registry)
    original = plan.to_dict()
    legacy = prepare(plan)
    harbor = _prepare_assets_for_runtime(original, {'arch': arch, 'python_path': '/usr/bin/python3'})
    assert all(call == (arch, 'podman', False) for call in calls)
    for name in original['harnesses']:
        left = legacy['harnesses'][name]['execution_integrations'][0]
        right = harbor['harnesses'][name]['execution_integrations'][0]
        assert left['binary_path'] == right['binary_path'] == str(binary)
        assert left['sha256'] == right['sha256'] == digest
    assert legacy['benchmark_config']['image_arch'] == arch
    assert legacy['preparation']['probed'] is False
    assert plan.to_dict() == original


def test_explicit_artifact_is_verified_without_build(tmp_path, monkeypatch):
    binary = tmp_path / 'rtk'
    binary.write_bytes(b'fixture artifact')
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    def forbidden(*args):
        pytest.fail('explicit artifact must not build')
    monkeypatch.setattr('acb.preparation.ensure_rtk', forbidden)
    cfg, registry = configuration(tmp_path, 'arm64')
    cfg.extensions = [{'name': 'rtk', 'options': {'binary_path': str(binary), 'sha256': digest.upper()}}]
    assert prepare(resolve(cfg, registry))['harnesses']['pi']['execution_integrations'][0]['binary_path'] == str(binary)
    binary.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum mismatch'):
        prepare(resolve(cfg, registry))


def test_conflicting_architecture_fails_before_build(tmp_path, monkeypatch):
    cfg, registry = configuration(tmp_path, 'amd64')
    registry.benchmarks['fixture']['architecture'] = 'arm64'
    monkeypatch.setattr('acb.preparation.ensure_rtk', lambda *a: pytest.fail('build before validation'))
    with pytest.raises(ValueError, match='conflicts'):
        prepare(resolve(cfg, registry))


@pytest.mark.parametrize('explicit', [False, True])
def test_interpreter_probe_executes_and_preserves_task_settings(tmp_path, monkeypatch, explicit):
    import acb.integrations.runtime as runtime
    def capture(container, command):
        if command == ['uname', '-m']:
            return 'aarch64\n'
        return subprocess.run(command, text=True, capture_output=True, check=True).stdout
    monkeypatch.setattr(runtime, 'container_exec_capture', capture)
    cfg = {'workdir': '/work', 'conda_env': None, 'execution_integrations': [
        {'name': 'rtk', **({'python_path': sys.executable} if explicit else {})}]}
    original = deepcopy(cfg)
    result = prepare_legacy_rtk_runtime('fixture', 'claude-code', cfg, 'arm64', tmp_path)
    assert result['workdir'] == '/work'
    assert result['conda_env'] is None
    assert result['execution_integrations'][0]['python_path'].startswith('/')
    if explicit:
        assert result == original
    assert cfg == original
    evidence = json.loads((tmp_path / 'rtk-runtime.json').read_text())
    assert evidence['passed'] is True
    assert evidence['interpreter']['version'] >= [3, 8]


@pytest.mark.parametrize('failure', ['architecture', 'python'])
def test_failed_probe_records_failure_and_does_not_fallback(tmp_path, monkeypatch, failure):
    import acb.integrations.runtime as runtime
    commands = []
    def capture(container, command):
        commands.append(command)
        if command == ['uname', '-m']:
            return 'x86_64' if failure == 'architecture' else 'aarch64'
        assert '/missing/python' in command[-1]
        assert '/usr/bin/python3' not in command[-1]
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(runtime, 'container_exec_capture', capture)
    cfg = {'execution_integrations': [{'name': 'rtk', 'python_path': '/missing/python'}]}
    with pytest.raises(ValueError):
        prepare_legacy_rtk_runtime('fixture', 'claude-code', cfg, 'arm64', tmp_path)
    evidence = json.loads((tmp_path / 'rtk-runtime.json').read_text())
    assert evidence['passed'] is False
    assert evidence['error']
    assert len(commands) == (1 if failure == 'architecture' else 2)
