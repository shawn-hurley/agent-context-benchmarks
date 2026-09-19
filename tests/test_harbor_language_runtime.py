import asyncio
import json
import shlex
from types import SimpleNamespace

import pytest

from acb.harbor.runtime import (
    RH_REVISION, apply_language_profile, inspect_language_environment, language_profile,
)


def test_rh_profile_is_revision_scoped_and_task_can_override():
    source = 'rounakbende/rh-swe-bench'
    assert language_profile(source, RH_REVISION, {})['conda_env'] == 'testbed'
    assert language_profile(source, 'unknown', {})['conda_env'] is None
    assert language_profile('local:fixture', None, {})['conda_env'] is None
    assert language_profile(source, RH_REVISION, {'acb_runtime': {'conda_env': None}}) == {
        'conda_env': None, 'source': 'task-metadata'}


@pytest.mark.parametrize('profile', [{'conda_env': '../bad'}, {'conda_env': 3},
                                     {'shell': 'anything'}, 'testbed'])
def test_invalid_task_profile_rejected(profile):
    with pytest.raises(ValueError, match='metadata.acb_runtime'):
        language_profile('local:fixture', None, {'acb_runtime': profile})


def test_runtime_profile_prevents_per_harness_environment_drift():
    profile = {'conda_env': 'testbed'}
    config = {}
    apply_language_profile(config, profile)
    assert config['conda_env'] == 'testbed'
    apply_language_profile(config, profile)
    with pytest.raises(ValueError, match='conflicts'):
        apply_language_profile({'conda_env': None}, profile)


def test_activation_is_verified_and_interpreter_identity_recorded():
    class Environment:
        async def exec(self, command, **kwargs):
            argv = shlex.split(command)
            assert argv[:2] == ['bash', '-c']
            assert 'conda activate testbed && python' in argv[2]
            assert kwargs['timeout_sec'] == 30
            return SimpleNamespace(return_code=0, stdout=json.dumps({
                'python_executable': '/opt/miniconda3/envs/testbed/bin/python',
                'prefix': '/opt/miniconda3/envs/testbed'}))
    result = asyncio.run(inspect_language_environment(Environment(), {'conda_env': 'testbed'}))
    assert result['prefix'].endswith('/testbed')
    assert result['python_executable'].endswith('/testbed/bin/python')


@pytest.mark.parametrize('code,output', [(1, ''), (0, 'noise'), (0, '{}'),
                                        (0, '{"prefix":null,"python_executable":null}')])
def test_broken_activation_is_a_preparation_error(code, output):
    class Environment:
        async def exec(self, *args, **kwargs):
            return SimpleNamespace(return_code=code, stdout=output)
    with pytest.raises(ValueError, match='task'):
        asyncio.run(inspect_language_environment(Environment(), {'conda_env': 'testbed'}))
