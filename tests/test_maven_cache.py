import os
from pathlib import Path
import subprocess

import pytest

from acb.maven_cache import (DEFAULT_VOLUME, TARGET, cache_volume, cached_recipe,
                             compose_cache, container_arguments, validation_env)


def test_cache_configuration_and_opt_out():
    assert cache_volume({}) == DEFAULT_VOLUME
    assert cache_volume({'maven_cache': False}) is None
    for config in ({'maven_cache': 'yes'}, {'maven_cache_volume': '/tmp/cache'},
                   {'maven_cache_volume': 'name:other'}):
        with pytest.raises(ValueError):
            cache_volume(config)


def test_multistage_builds_keep_installed_artifacts_outside_shared_download_mount():
    recipe = 'FROM maven:3.9.12 AS build\nRUN mvn install \\\n && echo done\nFROM build\nRUN ["mvn", "validate"]\n'
    result = cached_recipe(recipe, {})
    assert result.count('ENV MAVEN_OPTS=') == 2
    assert result.count('--mount=type=cache,') == 2
    assert 'localPrefix=installed' in result
    assert 'target=' + TARGET in result
    assert 'locksDir=' + TARGET + '/.locks' in result
    assert 'nameMapper=file-gav' in result
    assert cached_recipe(result, {}) == result
    assert cached_recipe(recipe, {'maven_cache': False}) == recipe


def test_external_runtime_volume_survives_compose_cleanup():
    overlay = {'services': {'main': {'image': 'fixture'}, 'proxy': {'image': 'proxy'}}}
    compose_cache(overlay, {'maven_cache_volume': 'my-cache'})
    assert overlay['volumes']['acb-maven-downloads'] == {'external': True, 'name': 'my-cache'}
    assert overlay['services']['main']['volumes'][0]['target'] == TARGET
    assert 'volumes' not in overlay['services']['proxy']
    disabled = {}
    compose_cache(disabled, {'maven_cache': False})
    assert disabled == {}


def test_native_validation_wrapper_keeps_argument_boundaries(tmp_path):
    engine = tmp_path / 'podman'
    engine.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    engine.chmod(0o755)
    env = validation_env({'container_backend': 'podman'}, {'PATH': str(tmp_path) + ':' + os.environ['PATH']}, tmp_path)
    import json
    result = subprocess.run(['docker', 'run', '--name', 'trial', 'fixture', 'a b'], env=env,
                            check=True, capture_output=True, text=True)
    args = json.loads(result.stdout)
    assert args == ['run', '--volume', DEFAULT_VOLUME + ':' + TARGET,
                    '--name', 'trial', 'fixture', 'a b']
    assert container_arguments(['build', '--iidfile', 'file with spaces', '.'], DEFAULT_VOLUME) == [
        'build', '--iidfile', 'file with spaces', '.']
    assert env['DOCKER_BUILDKIT'] == '1'
    assert validation_env({'maven_cache': False}, {}, tmp_path) == {}


def test_scarfbench_export_is_opt_out_and_does_not_mutate_reference(tmp_path):
    from test_harbor_benchmark_tasks import scarf_plan
    from acb.harbor.dataset import prepare_dataset
    plan = scarf_plan(tmp_path)
    reference = Path(plan['benchmark_config']['benchmark_cache_dir']) / 'business_domain/cart/quarkus/Dockerfile'
    original = reference.read_bytes()
    manifest = prepare_dataset(plan)
    recipe = (Path(manifest['tasks'][0]['path']) / 'environment/Dockerfile').read_text()
    assert 'localPrefix=installed' in recipe
    assert reference.read_bytes() == original
    plan['benchmark_config']['maven_cache'] = False
    disabled = prepare_dataset(plan)
    assert 'localPrefix=installed' not in (Path(disabled['tasks'][0]['path']) / 'environment/Dockerfile').read_text()


@pytest.mark.parametrize("runtime,flags", [("podman", ["--ignore"]), ("docker", [])])
def test_cache_volume_creation_reuses_existing_volume(monkeypatch, runtime, flags):
    from acb.maven_cache import ensure_volume
    from types import SimpleNamespace
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=DEFAULT_VOLUME, stderr='')
    monkeypatch.setattr('acb.maven_cache.subprocess.run', run)
    ensure_volume(runtime, {})
    ensure_volume(runtime, {})
    assert calls == [[runtime, 'volume', 'create', *flags, DEFAULT_VOLUME]] * 2
    ensure_volume(runtime, {'maven_cache': False})
    assert len(calls) == 2


def test_cache_volume_error_preserves_engine_diagnostic(monkeypatch):
    from acb.maven_cache import ensure_volume
    from types import SimpleNamespace
    monkeypatch.setattr('acb.maven_cache.subprocess.run', lambda *a, **kw:
                        SimpleNamespace(returncode=125, stdout='', stderr='connection refused'))
    with pytest.raises(RuntimeError, match='connection refused'):
        ensure_volume('podman', {})


@pytest.mark.parametrize('location', ['registry', 'override', 'inline'])
def test_yaml_cache_options_resolve_before_execution(tmp_path, monkeypatch, location):
    import yaml
    from acb.config import RunConfig
    from acb.resolver import resolve
    (tmp_path / 'models.yaml').write_text('fixture:\n  api: openai\n  endpoint: example.invalid\n')
    settings = {'maven_cache': True, 'maven_cache_volume': 'acb-custom-cache'}
    run = {'run_id': 'cache-check', 'benchmark': 'scarfbench', 'harness': 'goose',
           'model': 'fixture', 'config_dir': '.'}
    if location == 'registry':
        (tmp_path / 'benchmarks.yaml').write_text(yaml.safe_dump({'scarfbench': settings}))
    elif location == 'inline':
        run['benchmark'] = {'name': 'scarfbench', **settings}
    else:
        run['overrides'] = {'benchmark': settings}
    path = tmp_path / 'run.yaml'
    path.write_text(yaml.safe_dump(run))
    def forbidden(*args, **kwargs):
        pytest.fail('configuration resolution must not execute commands')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    plan = resolve(RunConfig.from_file(path)).to_dict()
    assert plan['benchmark_config']['maven_cache_volume'] == 'acb-custom-cache'
    assert cache_volume(plan['benchmark_config']) == 'acb-custom-cache'


def test_cache_resolution_defaults_and_override_precedence():
    from acb.config import Registries, RunConfig
    from acb.resolver import resolve
    registry = Registries({}, {}, {}, models={'fixture': {'api': 'openai', 'endpoint': 'example.invalid'}})
    cfg = RunConfig(run_id='cache-check', benchmark='scarfbench', harness='goose', model='fixture')
    assert cache_volume(resolve(cfg, registry).to_dict()['benchmark_config']) == DEFAULT_VOLUME
    registry.benchmarks = {'scarfbench': {'maven_cache': True, 'maven_cache_volume': 'registry-cache'}}
    cfg.overrides = {'benchmark': {'maven_cache_volume': 'override-cache'}}
    assert cache_volume(resolve(cfg, registry).to_dict()['benchmark_config']) == 'override-cache'
    cfg.overrides['benchmark']['maven_cache'] = False
    assert cache_volume(resolve(cfg, registry).to_dict()['benchmark_config']) is None


@pytest.mark.parametrize('settings,match', [
    ({'maven_cache': 'yes'}, 'must be a boolean'),
    ({'maven_cache': 1}, 'must be a boolean'),
    ({'maven_cache_volume': '/tmp/cache'}, 'container volume name'),
    ({'maven_cache_volume': 'name:other'}, 'container volume name'),
    ({'maven_cache_volume': ''}, 'container volume name'),
])
def test_invalid_cache_options_fail_during_resolution(settings, match):
    from acb.config import Registries, RunConfig
    from acb.resolver import resolve
    registry = Registries({}, {}, {}, models={'fixture': {'api': 'openai', 'endpoint': 'example.invalid'}})
    cfg = RunConfig(run_id='cache-check', benchmark='scarfbench', harness='goose', model='fixture',
                    overrides={'benchmark': settings})
    with pytest.raises(ValueError, match=match):
        resolve(cfg, registry)


def test_docker_cache_wrapper_invokes_engine_without_recursing(tmp_path):
    engine_dir = tmp_path / 'engine'
    engine_dir.mkdir()
    binary = engine_dir / 'docker'
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    binary.chmod(0o755)
    env = validation_env({'container_backend': 'docker'},
                         {**os.environ, 'PATH': str(engine_dir)}, tmp_path)
    result = subprocess.run(['docker', 'run', '--rm', 'fixture'], env=env,
                            capture_output=True, text=True, timeout=5, check=True)
    assert result.stdout.splitlines() == ['run', '--volume', DEFAULT_VOLUME + ':/root/.m2/repository/cached', '--rm', 'fixture']
