import json
import subprocess

import pytest

from acb.integrations.legacy import LegacyIntegrationTransport
from acb.integrations.runtime import prepare_legacy_caveman_runtime
from acb.integrations.caveman import REVISION
from acb.preparation import prepare
from acb.resolver import resolve
from test_execution_plan import settings


def test_preparation_uses_shared_image_and_guidance(tmp_path, monkeypatch):
    cfg, registry = settings(tmp_path, extensions=['caveman'])
    builds = []
    def image(context, recipe, prefix, plan):
        builds.append((prefix, plan['architecture']))
        return 'sha256:fixture'
    monkeypatch.setattr('acb.preparation.prepare_image', image)
    prepared = prepare(resolve(cfg, registry))
    assert builds == [('acb-caveman', prepared['architecture'])]
    assert prepared['benchmark_config']['caveman_image'] == 'sha256:fixture'
    assert prepared['harnesses']['pi']['model_middleware'][0]['version'] == REVISION
    assert 'acb-recall HANDLE' in prepared['harnesses']['pi']['system_prompt']


@pytest.mark.parametrize('invalid', [False, True])
def test_runtime_python_and_failure_evidence(tmp_path, monkeypatch, invalid):
    def capture(container, argv):
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout
    monkeypatch.setattr('acb.integrations.runtime.container_exec_capture', capture)
    config = {'model_middleware': [{'name': 'caveman'}]}
    if invalid:
        config['model_middleware'][0]['python_path'] = '/missing/python'
        with pytest.raises(ValueError, match='Python'):
            prepare_legacy_caveman_runtime('task', config, tmp_path)
    else:
        prepared = prepare_legacy_caveman_runtime('task', config, tmp_path)
        assert prepared['model_middleware'][0]['python_path'].startswith('/')
        assert 'python_path' not in config['model_middleware'][0]
    assert json.loads((tmp_path / 'caveman-runtime.json').read_text())['passed'] is not invalid


@pytest.mark.parametrize('fail', ['create', 'start', None])
def test_partial_service_startup_cleanup_is_owned_and_idempotent(monkeypatch, fail):
    events = []
    def operation(name):
        def call(*args, **kwargs):
            events.append((name, args))
            if fail == name:
                raise RuntimeError(name)
            return 'ready'
        return call
    for name, method in [('create', 'container_create'), ('start', 'container_start'),
                         ('capture', 'container_exec_capture'), ('remove', 'container_stop_rm')]:
        monkeypatch.setattr('acb.integrations.legacy.engine.' + method, operation(name))
    transport = LegacyIntegrationTransport('task', 'owned-pod', 'image')
    try:
        if fail:
            with pytest.raises(RuntimeError):
                transport.start()
        else:
            transport.start()
    finally:
        transport.close()
        transport.close()
    assert [args for name, args in events if name == 'remove'] == [('owned-pod-caveman',)]
    with pytest.raises(ValueError, match='unknown'):
        transport.stop_service('another-task')


def test_recovery_directory_copies_contents(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('acb.integrations.legacy.engine.container_cp_out',
                        lambda *args: calls.append(args))
    transport = LegacyIntegrationTransport('task', 'pod', 'image')
    transport.service_download('acb-caveman', '/data', tmp_path / 'runtime', directory=True)
    assert calls == [('pod-caveman', '/data/.', tmp_path / 'runtime')]


def test_praxis_readiness_is_excluded_before_exposing_endpoint(tmp_path, monkeypatch):
    from acb.config import ModelSpec
    from acb.proxy import ProxyTags
    from acb.proxy.praxis import PraxisContainerBackend
    calls = []
    for name in ('container_create', 'container_cp_in', 'container_start'):
        monkeypatch.setattr('acb.proxy.praxis.' + name, lambda *args, **kwargs: None)
    def capture(container, argv, **kwargs):
        calls.append(argv)
        return 'ready'
    monkeypatch.setattr('acb.proxy.praxis.container_exec_capture', capture)
    backend = PraxisContainerBackend(
        tags=ProxyTags('run', 'fixture', 'pi', 'model', 'task'),
        usage_path=tmp_path / 'usage.jsonl', config={},
        model_spec=ModelSpec('model', api='openai', endpoint='fixture:8000', tls=False),
        harness_api='openai', pod='owned-pod', image='praxis', port=18880)
    assert backend.start() == 'http://127.0.0.1:18880'
    assert '/v1/models' in calls[0][-1]
    assert calls[-1] == ['sh', '-c', ': > /tmp/benchmark_metrics.jsonl']
    assert PraxisContainerBackend.CONTAINER_PORT == 8080
