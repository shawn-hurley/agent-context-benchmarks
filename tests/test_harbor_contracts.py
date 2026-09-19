from copy import deepcopy

import pytest

from acb.harbor.contracts import container_contract


def records():
    return ({"Image": "sha256:" + "a" * 64,
             "Config": {"Env": ["SECRET=never-record"]},
             "HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 512 * 1024 * 1024},
             "Mounts": [{"Destination": "/data", "Type": "bind", "RW": False,
                         "Source": "/private/unstable-path"}]},
            {"Id": "sha256:" + "a" * 64, "Architecture": "arm64", "Os": "linux"})


def test_contract_records_enforced_limits_and_immutable_image_without_secrets():
    container, image = records()
    original = deepcopy(container)
    result = container_contract(container, image, {"cpus": 2, "memory_mb": 512})
    assert result['image_id'] == image['Id']
    assert result['resources']['cpu_limit'] == 2
    assert result['mounts'] == [{'destination': '/data', 'type': 'bind', 'read_only': True}]
    assert 'never-record' not in str(result) and '/private/' not in str(result)
    assert container == original


def test_podman_quota_period_representation_is_equivalent():
    container, image = records()
    container['HostConfig'].update(NanoCpus=0, CpuQuota=200000, CpuPeriod=100000)
    assert container_contract(container, image, {'cpus': 2})['resources']['cpu_limit'] == 2


@pytest.mark.parametrize('requirements', [{'cpus': 4}, {'memory_mb': 1024},
                                         {'storage_mb': 100}, {'gpus': 1}, {'tpu': {'type': 'v5'}}])
def test_unenforced_or_unsupported_resources_fail(requirements):
    with pytest.raises(ValueError):
        container_contract(*records(), requirements)


def test_image_identity_must_match_running_container():
    container, image = records()
    image['Id'] = 'sha256:' + 'b' * 64
    with pytest.raises(ValueError, match='differs'):
        container_contract(container, image, {})


def test_task_services_cannot_be_silently_replaced_by_acb(tmp_path):
    from acb.harbor.contracts import validate_service_names
    path = tmp_path / 'environment'
    path.mkdir()
    (path / 'docker-compose.yaml').write_text('services:\n  acb-praxis:\n    image: task-owned\n')
    tasks = [{'id': 'fixture', 'path': str(tmp_path)}]
    with pytest.raises(ValueError, match='conflict'):
        validate_service_names(tasks, ['acb-praxis'])
    validate_service_names(tasks, ['acb-caveman'])


def test_frozen_image_survives_teardown_and_does_not_replace_verifier(tmp_path):
    import asyncio
    import json
    pytest.importorskip('harbor')
    from harbor.models.task.config import EnvironmentConfig
    from acb.harbor.environment import FrozenImageEnvironment

    class Provider:
        def __init__(self, **kwargs):
            self.settings = kwargs

        async def start(self, force_build):
            self.force_build = force_build

        async def _run_docker_compose_command(self, command, **kwargs):
            return command

    class Environment(FrozenImageEnvironment, Provider):
        pass

    config = EnvironmentConfig(docker_image='mutable:tag')
    image = 'sha256:' + 'a' * 64
    kwargs = dict(environment_dir=tmp_path, task_env_config=config,
                  frozen_images={str(tmp_path.resolve()): image},
                  frozen_services={str(tmp_path.resolve()): {'fixture-model': 'sha256:' + 'b' * 64}})
    main = Environment(session_id='fixture__env', **kwargs)
    assert main.settings['task_env_config'].docker_image == image
    assert config.docker_image == 'mutable:tag'
    overlay = json.loads(__import__('pathlib').Path(main.settings['extra_docker_compose'][-1]).read_text())
    assert overlay['services']['main'] == {'image': image, 'pull_policy': 'never'}
    assert overlay['services']['fixture-model'] == {'image': 'sha256:' + 'b' * 64, 'pull_policy': 'never'}
    asyncio.run(main.start(True))
    assert main.force_build is False
    assert asyncio.run(main._run_docker_compose_command(
        ['down', '--rmi', 'local', '--volumes', '--remove-orphans'])) == [
            'down', '--volumes', '--remove-orphans']
    verifier = Environment(session_id='fixture__verifier', **kwargs)
    assert verifier.settings['task_env_config'].docker_image == 'mutable:tag'
    assert 'extra_docker_compose' not in verifier.settings
    main._frozen_overlay.cleanup()


def test_provider_drift_is_rejected_before_model_work(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from acb.harbor.contracts import verify_provider_images
    async def resolve(name):
        assert name == 'acb-praxis'
        return 'container'
    async def inspect(*args):
        return [{'Image': 'sha256:' + 'a' * 64}]
    monkeypatch.setattr('acb.harbor.contracts.engine_json', inspect)
    environment = SimpleNamespace(_platform=SimpleNamespace(_resolve_service_container=resolve))
    expected = {'acb-praxis': {'image_id': 'sha256:' + 'b' * 64}}
    with pytest.raises(ValueError, match='provider image changed'):
        asyncio.run(verify_provider_images(environment, expected))


@pytest.mark.parametrize('session', ['fixture__verifier__trial', 'long-task-name__8b912abc', 'fixture__verifier__env'])
def test_separate_verifier_requires_prepared_contract_and_rechecks_runtime(tmp_path, monkeypatch, session):
    import asyncio
    from types import SimpleNamespace
    pytest.importorskip('harbor')
    from harbor.models.task.config import EnvironmentConfig
    from acb.harbor.contracts import verifier_contract_key
    from acb.harbor.environment import FrozenImageEnvironment

    class Provider:
        def __init__(self, **kwargs):
            self.settings = kwargs
            self.trial_paths = SimpleNamespace(agent_dir=tmp_path / 'agent')

        async def start(self, force_build):
            self.force_build = force_build

    class Environment(FrozenImageEnvironment, Provider):
        pass

    config = EnvironmentConfig(docker_image='verifier:mutable')
    kwargs = dict(session_id=session, environment_dir=tmp_path, task_env_config=config)
    with pytest.raises(ValueError, match='not inspected'):
        Environment(**kwargs, verifier_contracts={})
    key = verifier_contract_key(tmp_path, config)
    expected = {'container': {'image_id': 'sha256:' + 'b' * 64}, 'service_images': {}}
    environment = Environment(**kwargs, verifier_contracts={key: expected})
    assert environment.settings['task_env_config'].docker_image == expected['container']['image_id']
    async def inspect(*args):
        return {'image_id': 'sha256:' + 'c' * 64}
    async def services(*args):
        return {}
    monkeypatch.setattr('acb.harbor.contracts.inspect_container_contract', inspect)
    monkeypatch.setattr('acb.harbor.contracts.inspect_task_service_images', services)
    try:
        with pytest.raises(ValueError, match='changed since preparation'):
            asyncio.run(environment.start(True))
        assert environment.force_build is False
        assert (tmp_path / f'agent/acb/verifier-runtime-{key}.json').exists()
    finally:
        environment._frozen_overlay.cleanup()


def test_verifier_contract_key_distinguishes_resource_requirements(tmp_path):
    pytest.importorskip('harbor')
    from harbor.models.task.config import EnvironmentConfig
    from acb.harbor.contracts import verifier_contract_key
    first = EnvironmentConfig(cpus=1)
    second = first.model_copy(update={'cpus': 2})
    assert verifier_contract_key(tmp_path, first) != verifier_contract_key(tmp_path, second)
