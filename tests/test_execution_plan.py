"""Configuration handoff tests; no containers or model requests."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from acb.config import Registries, RunConfig
from acb.preparation import prepare
from acb.resolver import resolve
from acb import runner


@pytest.fixture(autouse=True)
def invocation_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def settings(tmp_path, **changes):
    cfg = RunConfig(**{'run_id': 'handoff', 'benchmark': 'fixture', 'harness': 'pi',
                       'model': 'alias', 'source_file': str(tmp_path / 'run.yaml'),
                       'execution': {'max_workers': 2, 'timeout': 250},
                       'overrides': {'harnesses': {'pi': {'system_prompt': 'per harness'}}},
                       **changes})
    registry = Registries({}, {'fixture': {'execution_backend': 'legacy'}}, {},
                          models={'alias': {'model': 'wire/model', 'api': 'openai', 'endpoint': 'localhost:8000'}},
                          machine={'cache_dir': str(tmp_path / 'cache')})
    return cfg, registry


def test_backend_dispatch_uses_resolver_precedence_once(tmp_path, monkeypatch):
    cfg, registry = settings(tmp_path, benchmark={'name': 'fixture', 'execution_backend': 'legacy'},
                             overrides={'benchmark': {'execution_backend': 'harbor'}})
    expected = resolve(cfg, registry)
    seen = []
    def run_plan(plan, **kwargs):
        seen.append(plan)
        return tmp_path / 'result'
    monkeypatch.setattr('acb.harbor.backend.run_plan', run_plan)
    assert runner.run(cfg, registry) == tmp_path / 'result'
    assert seen == [expected]


@pytest.mark.parametrize('changes,match', [
    ({'execution': {'environment': 'docker'}}, 'environment'),
    ({'execution': {'offline': True}}, 'offline'),
    ({'overrides': {'benchmark': {'attempts': 2}}}, 'attempts'),
])
def test_prepare_and_run_reject_unsupported_legacy_settings_before_side_effects(tmp_path, monkeypatch, changes, match):
    cfg, registry = settings(tmp_path, **changes)
    def unexpected(*args, **kwargs):
        pytest.fail('dataset or output side effect before validation')
    monkeypatch.setattr(runner, 'make_benchmark', unexpected)
    monkeypatch.setattr(runner, '_resolve_run_dir', unexpected)
    for action in (lambda: prepare(resolve(cfg, registry)), lambda: runner.run(cfg, registry)):
        with pytest.raises(ValueError, match=match):
            action()
    assert not (tmp_path / 'runs').exists()


@pytest.mark.parametrize('collision', [False, True])
def test_legacy_execution_consumes_prepared_settings_without_reloading(tmp_path, monkeypatch, collision):
    cfg, registry = settings(tmp_path, subset=['task'], limit=1)
    registry.models['alias']['key_env'] = 'TEST_MODEL_SECRET'
    monkeypatch.setenv('TEST_MODEL_SECRET', 'credential-must-not-be-saved')
    expected = prepare(resolve(cfg, registry))
    if collision:
        (tmp_path / 'runs/handoff').mkdir(parents=True)
    effective_id = 'handoff-1' if collision else 'handoff'
    captured = []
    from acb.benchmarks import Instance, Prediction
    class Benchmark:
        def load_instances(self, **selection):
            assert selection == {'subset': ['task'], 'limit': 1}
            return [Instance('task', 'prompt')]
    def benchmark(name, values):
        assert name == expected['benchmark']
        assert values == expected['benchmark_config']
        # Changing the original registry after resolution cannot change workers.
        registry.harnesses['pi'] = {'timeout': 999}
        cfg.overrides['harnesses']['pi']['system_prompt'] = 'changed'
        return Benchmark()
    monkeypatch.setattr(runner, 'make_benchmark', benchmark)
    def pipeline(instance, name, output, effective, harness, bench, bench_cfg, model, proxy, cache, **kwargs):
        captured.append(deepcopy(harness))
        assert harness == expected['harnesses'][name]
        assert effective.run_id == effective_id
        saved = json.loads((output.parent / 'resolved.json').read_text())
        assert saved['harnesses'][name] == harness
        assert saved['model']['name'] == effective.model
        assert effective.model == model.name == 'wire/model'
        assert effective.max_workers == 2
        assert effective.overrides == {}
        assert cache == Path(expected['cache_dir'])
        harness['timeout'] = 999  # Worker-local mutation cannot change the plan.
        return Prediction('task', model.name), {'task': True}
    monkeypatch.setattr(runner, '_run_instance_pipeline', pipeline)
    monkeypatch.setattr(runner, '_setup_logging', lambda *a, **k: None)
    monkeypatch.setattr(runner, 'setup_interrupt_handler', lambda *a: None)
    monkeypatch.setattr(runner, '_log_terminal_state', lambda *a: None)
    monkeypatch.setattr(runner, 'aggregate_per_instance_files', lambda *a: None)
    monkeypatch.setattr(runner, 'build_report', lambda *a: tmp_path / 'absent-report.json')
    monkeypatch.setattr('acb.html_report.build_html_report', lambda *a: '<html></html>')
    result = runner._run_legacy(cfg, expected)
    assert result == tmp_path / 'runs' / effective_id
    saved = json.loads((result / 'resolved.json').read_text())
    requested = json.loads((result / 'requested.json').read_text())
    assert requested == expected['requested_config']
    assert requested['model'] == 'alias'
    assert requested['overrides']['harnesses']['pi']['system_prompt'] == 'per harness'
    assert saved['run_id'] == effective_id
    assert saved['requested_run_id'] == 'handoff'
    assert saved['run_dir'] == str(result)
    assert saved['model_alias'] == 'alias'
    assert saved['model']['key_env'] == 'TEST_MODEL_SECRET'
    assert 'credential-must-not-be-saved' not in (result / 'resolved.json').read_text()
    assert captured == [expected['harnesses']['pi']]
    assert expected['harnesses']['pi']['timeout'] == 250


def test_run_uses_same_preparation_as_prepare_command(tmp_path, monkeypatch):
    cfg, registry = settings(tmp_path)
    expected = prepare(resolve(cfg, registry))
    captured = []
    def execute(requested, document, verbose):
        captured.append(document)
        return tmp_path
    monkeypatch.setattr(runner, '_run_legacy', execute)
    runner.run(cfg, registry)
    assert captured == [expected]


@pytest.mark.parametrize('collision', [False, True])
def test_harbor_saves_exact_worker_input_and_requested_snapshot(tmp_path, monkeypatch, capsys, collision):
    from acb.harbor import backend
    cfg, registry = settings(tmp_path, benchmark={'name': 'fixture', 'execution_backend': 'harbor'})
    registry.models['alias']['key_env'] = 'TEST_MODEL_SECRET'
    monkeypatch.setenv('TEST_MODEL_SECRET', 'credential-must-not-be-saved')
    plan = resolve(cfg, registry)
    prepared = plan.to_dict()
    prepared.update(worker_python='/fixture/python', runtime_contracts={'task': {'arch': 'arm64'}},
                    manifest={'tasks': [{'id': 'task'}]})
    if collision:
        (tmp_path / 'runs/handoff').mkdir(parents=True)
    cfg.model = 'changed-after-resolution'
    def prepare_with_status(*args, **kwargs):
        kwargs['status']('Inspecting task environments and verifier')
        return prepared
    monkeypatch.setattr(backend, 'prepare', prepare_with_status)
    captured = []
    def worker(python, action, source, output, **kwargs):
        captured.append(json.loads(source.read_text()))
        assert python == '/fixture/python'
        assert action == 'run'
    monkeypatch.setattr(backend, '_worker', worker)
    result = backend.run_plan(plan)
    terminal = capsys.readouterr().out
    assert terminal.index('Inspecting task environments and verifier') < terminal.index('Starting global work queue')
    requested = json.loads((result / 'requested.json').read_text())
    saved = json.loads((result / 'resolved.json').read_text())
    assert captured == [saved]
    assert requested == plan.to_dict()['requested_config']
    assert requested['model'] == 'alias'
    assert saved['run_id'] == ('handoff-1' if collision else 'handoff')
    assert saved['requested_run_id'] == 'handoff'
    assert saved['run_dir'] == str(result)
    assert saved['model_alias'] == 'alias'
    assert saved['model']['name'] == 'wire/model'
    assert saved['model']['key_env'] == 'TEST_MODEL_SECRET'
    assert saved['runtime_contracts'] == prepared['runtime_contracts']
    assert prepared['run_id'] == 'handoff'
    assert 'run_dir' not in prepared
    for name in ('requested.json', 'resolved.json'):
        assert 'credential-must-not-be-saved' not in (result / name).read_text()


def test_requested_metadata_redacts_inline_credentials_without_mutating_input():
    from acb.provenance import requested_config
    raw = {'model': 'alias', 'key_env': 'MODEL_KEY', 'options': {'api_key': 'secret'},
           'mcp_servers': [{'env': {'TOKEN': 'secret', 'OTHER': 'value'}}]}
    saved = requested_config(raw)
    assert saved['key_env'] == 'MODEL_KEY'
    assert saved['options']['api_key'] == '[redacted]'
    assert saved['mcp_servers'][0]['env'] == {'TOKEN': '[redacted]', 'OTHER': '[redacted]'}
    assert raw['options']['api_key'] == 'secret'


def test_saved_baseline_treatment_preserve_launch_settings(tmp_path):
    from acb.provenance import save_configuration
    cfg, registry = settings(tmp_path, harness=['goose', 'pi', 'opencode', 'claude-code'],
                             benchmark={'name': 'fixture', 'execution_backend': 'harbor'}, extensions=[])
    baseline = resolve(cfg, registry).to_dict()
    cfg.extensions = ['rtk']
    treatment = resolve(cfg, registry).to_dict()
    for label, document in [('baseline', baseline), ('treatment', treatment)]:
        output = tmp_path / label
        output.mkdir()
        save_configuration(output, document, effective_run_id=label)
    before = json.loads((tmp_path / 'baseline/resolved.json').read_text())
    after = json.loads((tmp_path / 'treatment/resolved.json').read_text())
    assert before['requested_config']['extensions'] == []
    assert after['requested_config']['extensions'] == ['rtk']
    for name in before['harnesses']:
        for key in ('version', 'launch_profile', 'timeout', 'system_prompt', 'workdir', 'conda_env'):
            assert before['harnesses'][name].get(key) == after['harnesses'][name].get(key)


@pytest.mark.parametrize('intensity', ['lite', 'full', 'ultra'])
def test_caveman_skill_preparation_is_shared_across_backends(tmp_path, monkeypatch, intensity):
    from acb.preparation import _prepare_assets_for_runtime
    cfg, registry = settings(tmp_path, harness=['goose', 'pi', 'opencode', 'claude-code'],
                             skills=[{'name': 'caveman', 'options': {'intensity': intensity}}])
    plan = resolve(cfg, registry)
    original = plan.to_dict()
    legacy = prepare(plan)
    harbor_input = plan.to_dict()
    harbor_input['execution_backend'] = 'harbor'
    harbor_input['benchmark_config']['praxis_image'] = 'fixture-praxis'
    def forbidden(*args, **kwargs):
        pytest.fail('response skill preparation must not build or download')
    monkeypatch.setattr('acb.preparation.subprocess.run', forbidden)
    harbor = _prepare_assets_for_runtime(harbor_input, {'arch': 'arm64'})
    assert legacy['harnesses'] == harbor['harnesses']
    for name, values in legacy['harnesses'].items():
        skill = values['skills'][0]
        import hashlib
        asset = Path(skill['source_path']) / 'SKILL.md'
        assert skill['source_type'] == 'local'
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == skill['sha256']
        assert values['instruction_evidence'][0]['intensity'] == intensity
        assert f'Selected intensity: {intensity}' in values['system_prompt']
        if name == 'pi':
            assert values['system_prompt'].startswith('per harness\n\n')
        assert values['version'] == original['harnesses'][name]['version']
        assert values.get('launch_profile') == original['harnesses'][name].get('launch_profile')
    assert plan.to_dict() == original
    # Repreparing a prepared skill does not duplicate instructions.
    from acb.preparation import prepare_response_skills
    assert prepare_response_skills(legacy) == legacy


def test_legacy_caveman_skill_reaches_scheduled_settings(tmp_path, monkeypatch):
    cfg, registry = settings(tmp_path, skills=['caveman'])
    observed = []
    def execute(requested, document, verbose):
        observed.append(document['harnesses']['pi'])
        return tmp_path
    monkeypatch.setattr(runner, '_run_legacy', execute)
    runner.run(cfg, registry)
    assert observed[0]['skills'][0]['source_type'] == 'local'
    assert observed[0]['instruction_evidence'][0]['name'] == 'caveman'


def test_legacy_caveman_invalid_intensity_fails_in_preparation(tmp_path):
    cfg, registry = settings(tmp_path, skills=[{'name': 'caveman', 'options': {'intensity': 'typo'}}])
    with pytest.raises(ValueError, match='intensity'):
        prepare(resolve(cfg, registry))
