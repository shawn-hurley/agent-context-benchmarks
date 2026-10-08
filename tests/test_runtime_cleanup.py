"""Public cleanup contracts: early rejection, applicability and canonical assets."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from acb.config import Registries, RunConfig
from acb.resolver import resolve


def inputs(**changes):
    registry = Registries({}, {'fixture': {}}, models={
        'alias': {'model': 'wire/model', 'api': 'openai', 'endpoint': 'localhost:8000', 'tls': False}})
    config = RunConfig(**{'run_id': 'cleanup', 'benchmark': 'fixture', 'harness': 'pi',
                         'model': 'alias', **changes})
    return config, registry


@pytest.mark.parametrize('field', ['container_backend', 'praxis_ai_repo', 'namespace',
    'scarfbench_dir', 'git_url', 'python', 'execution_backend', 'max_workers'])
@pytest.mark.parametrize('layer', ['registry', 'inline', 'override'])
def test_retired_benchmark_controls_are_rejected_before_work(field, layer, monkeypatch):
    cfg, registry = inputs()
    if layer == 'registry':
        registry.benchmarks['fixture'][field] = 'obsolete'
    elif layer == 'inline':
        cfg.benchmark = {'name': 'fixture', field: 'obsolete'}
    else:
        cfg.overrides = {'benchmark': {field: 'obsolete'}}
    before = deepcopy((cfg, registry))
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: pytest.fail('resolver started work'))
    with pytest.raises(ValueError, match=field):
        resolve(cfg, registry)
    assert (cfg, registry) == before


@pytest.mark.parametrize('field', ['binary', 'model', 'provider', 'thinking', 'max_tokens',
    'temperature', 'python_path', 'launch_profile', 'execution_integrations', 'model_middleware'])
@pytest.mark.parametrize('layer', ['registry', 'shared', 'local'])
def test_retired_harness_controls_are_rejected_even_when_replaced(field, layer):
    cfg, registry = inputs()
    settings = {field: 'obsolete'}
    if layer == 'registry':
        registry.harnesses['pi'] = settings
    elif layer == 'shared':
        cfg.overrides = {'harness': settings}
    else:
        cfg.overrides = {'harnesses': {'pi': settings}}
    with pytest.raises(ValueError, match=field):
        resolve(cfg, registry)


@pytest.mark.parametrize('field', ['proxy', 'max_workers'])
def test_retired_top_level_fields_offer_migration_guidance(tmp_path, field):
    path = tmp_path / 'run.yaml'
    path.write_text(f'run_id: cleanup\nbenchmark: harbor\nharness: pi\nmodel: alias\n{field}: obsolete\n')
    with pytest.raises(ValueError, match='models.yaml' if field == 'proxy' else 'execution.max_workers'):
        RunConfig.from_file(path)


def test_models_registry_is_complete_and_proxy_is_not_discovered(tmp_path):
    from acb.resolver import discover_config_dir
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'proxy.yaml').write_text('models:\n  old: {endpoint: retired}\n')
    assert discover_config_dir(tmp_path / 'nested') != config
    (config / 'models.yaml').write_text('alias: {model: wire/model, api: openai, endpoint: localhost:8000, tls: false}\n')
    registry = Registries.load(config)
    assert set(registry.models) == {'alias'}
    assert not hasattr(registry, 'proxy')
    assert [Path(p).name for p in registry.sources] == ['models.yaml']
    cfg, _ = inputs(benchmark="harbor")
    assert resolve(cfg, registry).to_dict()['model']['name'] == 'wire/model'
    registry.models['alias']['reports_cache'] = True
    with pytest.raises(ValueError, match='reports_cache'):
        resolve(cfg, registry)


@pytest.mark.parametrize('category,definition', [
    ('skills', {'name': 'custom', 'source_type': 'local', 'source_path': './skill'}),
    ('mcp_servers', {'name': 'custom', 'command': '/custom', 'args': []}),
])
@pytest.mark.parametrize('layer', ['top', 'registry', 'shared', 'local'])
def test_inline_component_definitions_are_rejected_at_every_layer(category, definition, layer):
    cfg, registry = inputs()
    if layer == 'top':
        setattr(cfg, category, [definition])
    elif layer == 'registry':
        registry.harnesses['pi'] = {category: [definition]}
    elif layer == 'shared':
        cfg.overrides = {'harness': {category: [definition]}}
    else:
        cfg.overrides = {'harnesses': {'pi': {category: [definition]}}}
    with pytest.raises(ValueError, match='named selections'):
        resolve(cfg, registry)


@pytest.mark.parametrize('harness,field,value', [
    ('pi', 'max_budget_usd', 1), ('opencode', 'max_budget_usd', 1),
    ('goose', 'max_budget_usd', 1), ('claude-code', 'max_tool_repetitions', 2),
    ('pi', 'max_tool_repetitions', 2), ('opencode', 'max_tool_repetitions', 2),
])
@pytest.mark.parametrize('layer', ['registry', 'shared', 'local'])
def test_harness_control_applicability(harness, field, value, layer):
    cfg, registry = inputs(harness=harness)
    if layer == 'registry':
        registry.harnesses[harness] = {field: value}
    elif layer == 'shared':
        cfg.overrides = {'harness': {field: value}}
    else:
        cfg.overrides = {'harnesses': {harness: {field: value}}}
    with pytest.raises(ValueError, match=field):
        resolve(cfg, registry)


def test_mixed_harness_controls_require_per_harness_settings():
    cfg, registry = inputs(harness=['goose', 'claude-code'])
    cfg.overrides = {'harness': {'max_budget_usd': 5}}
    with pytest.raises(ValueError, match='use overrides.harnesses'):
        resolve(cfg, registry)
    cfg.overrides = {'harnesses': {'goose': {'max_tool_repetitions': 2},
                                 'claude-code': {'max_budget_usd': 5}}}
    plan = resolve(cfg, registry).to_dict()
    assert plan['harnesses']['goose']['max_tool_repetitions'] == 2
    assert plan['harnesses']['claude-code']['max_budget_usd'] == 5
    assert plan['harnesses']['claude-code']['launch_profile'] == 'isolated-hooks'
    assert plan['max_workers'] == 4
    assert 'max_workers' not in plan['requested_config']
    assert 'proxy' not in plan['requested_config']
    assert plan['execution_backend'] == 'harbor' and plan['proxy'] == 'praxis'
    assert 'proxy_config' not in plan
    cfg.execution['cache_policy'] = 'reset every request'
    with pytest.raises(ValueError, match='cache_policy'):
        resolve(cfg, registry)


@pytest.mark.parametrize('benchmark,source,field', [
    ('scarfbench', {'path': './tasks'}, 'scarf_binary'),
    ('swebench', {'path': './tasks'}, 'swebench_python'),
    ('swebench', {}, 'maven_cache'), ('scarfbench', {}, 'task_repo'),
    ('fixture', {'dataset': 'rounakbende/rh-swe-bench'}, 'split'),
    ('fixture', {'dataset': 'registry/tasks'}, 'task_root'),
    ('fixture', {'path': './tasks'}, 'registry'),
])
@pytest.mark.parametrize('layer', ['registry', 'inline', 'override'])
def test_benchmark_loading_route_rejects_ignored_fields(benchmark, source, field, layer):
    cfg, registry = inputs(benchmark=benchmark)
    if layer == 'registry':
        registry.benchmarks[benchmark] = {**source, field: 'ignored'}
    elif layer == 'inline':
        cfg.benchmark = {'name': benchmark, **source, field: 'ignored'}
    else:
        cfg.overrides = {'benchmark': {**source, field: 'ignored'}}
    with pytest.raises(ValueError, match=field):
        resolve(cfg, registry)


@pytest.mark.parametrize('version', [None, 1, '2', 2.0, True, 3])
def test_worker_protocol_rejection_precedes_setup(tmp_path, monkeypatch, version):
    from acb.harbor.worker import execute
    monkeypatch.setattr('acb.harbor.worker.verify_manifest', lambda *a: pytest.fail('manifest setup started'))
    monkeypatch.setattr('acb.harbor.benchmark_grader.verify_grader', lambda *a: pytest.fail('grader setup started'))
    output = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError, match='prepare again'):
        asyncio.run(execute({'protocol_version': version}, output))
    assert not output.exists()


def test_native_grader_shim_survives_child_environment_and_uses_selected_engine(tmp_path, monkeypatch):
    from acb.container import container_env, grader_env
    monkeypatch.setenv('PATH', '/fixture/system/bin')
    monkeypatch.setenv('DOCKER_HOST', 'unix:///explicit/socket')
    monkeypatch.setattr('acb.container.shutil.which', lambda name: '/fixture/podman')
    monkeypatch.setattr('acb.container._ensure_empty_docker_config', lambda: tmp_path / 'credentials')
    parent = grader_env({'container_backend': 'podman'}, tmp_path)
    assert parent['DOCKER_HOST'] == 'unix:///explicit/socket'
    assert parent['PATH'].split(':')[0] == str(tmp_path / 'bin')
    assert '/fixture/podman' in (tmp_path / 'bin/docker').read_text()
    monkeypatch.setenv('PATH', parent['PATH'])
    child = container_env({'container_backend': 'podman'})
    assert child['PATH'] == parent['PATH']
    assert grader_env({'container_backend': 'podman'}, tmp_path)['PATH'] == parent['PATH']
    assert child['DOCKER_CONFIG'] == str(tmp_path / 'credentials')
    docker = grader_env({'container_backend': 'docker'}, tmp_path / 'docker-run')
    assert not (tmp_path / 'docker-run/bin').exists()
    assert docker['DOCKER_HOST'] == 'unix:///explicit/socket'


def test_raw_container_names_cannot_execute_or_transfer(tmp_path):
    from acb.container import container_cp_in, container_cp_out, container_exec_capture
    from acb.transport import command
    from acb.harnesses._streaming import execute
    for operation in [lambda: container_cp_in('old', tmp_path, '/work'),
                      lambda: container_cp_out('old', '/work', tmp_path),
                      lambda: container_exec_capture('old', ['true']),
                      lambda: command('old', ['true'], {}, '/work'),
                      lambda: execute(['podman', 'exec', 'old', 'true'],
                                      tmp_path / 'transcript', 10, lambda _: None)]:
        with pytest.raises(TypeError, match='[Tt]ransport|EnvironmentCommand'):
            operation()
    assert not (tmp_path / 'transcript').exists()


def test_bundled_workflows_use_shared_assets_and_reject_pilot_field(tmp_path):
    from acb.workflows import load_workflow, validate_workflow_snapshot
    shared = Path(__file__).resolve().parents[1] / 'acb/workflows/_shared'
    workflows = [load_workflow(name, tmp_path, 'scarfbench', ['goose'])
                 for name in ('kantra-controller', 'kantra-rgctl', 'migiq')]
    for workflow in workflows:
        validate_workflow_snapshot(workflow)
        assert 'agent_adapter' not in workflow
        assert 'no-think-proxy.py' not in workflow['asset_sources']
        helper = 'rgctl-plan.sh' if workflow['name'] == 'migiq' else 'kantra-plan.sh'
        assert Path(workflow['asset_sources'][helper]).parent == shared
    assert workflows[1]['asset_sources']['Cargo.lock'] == workflows[2]['asset_sources']['Cargo.lock']
    assert workflows[1]['asset_sources']['skills/rgctl/SKILL.md'] == workflows[2]['asset_sources']['skills/rgctl/SKILL.md']
    custom = tmp_path / 'custom'
    custom.mkdir()
    (custom / 'instruction.md').write_text('Complete the task')
    (custom / 'workflow.yaml').write_text('''version: 1
name: retired
agent_adapter: local-qwen-no-think
steps:
- name: finish
  instruction: instruction.md
  timeout_sec: 30
  gate: {type: native}
''')
    with pytest.raises(ValueError, match='agent_adapter'):
        load_workflow(str(custom), tmp_path, 'scarfbench', ['goose'])


def test_shared_source_drift_invalidates_workflow_snapshot(tmp_path, monkeypatch):
    import acb.workflows as module
    package = tmp_path / 'packaged'
    workflow = package / 'kantra-controller'
    workflow.mkdir(parents=True)
    shared = package / '_shared'
    shared.mkdir()
    helper = shared / 'kantra-plan.sh'
    helper.write_text('original helper')
    (workflow / 'instruction.md').write_text('Complete the task')
    (workflow / 'workflow.yaml').write_text('''version: 1
name: shared
benchmarks: [scarfbench]
harnesses: [goose]
environment:
  assets: [kantra-plan.sh]
steps:
- name: finish
  instruction: instruction.md
  timeout_sec: 30
  gate: {type: native}
''')
    monkeypatch.setattr(module, '__file__', str(package / '__init__.py'))
    plan = module.load_workflow('kantra-controller', tmp_path, 'scarfbench', ['goose'])
    module.validate_workflow_snapshot(plan)
    helper.write_text('changed shared helper')
    with pytest.raises(ValueError, match='changed after resolution'):
        module.validate_workflow_snapshot(plan)
    assert module.load_workflow('kantra-controller', tmp_path, 'scarfbench', ['goose'])['sha256'] != plan['sha256']


def test_missing_hierarchical_tasks_have_distinct_direct_artifacts(tmp_path):
    from acb.harbor.results import import_results
    from acb.report_data import HarnessReport
    cfg, registry = inputs()
    plan = resolve(cfg, registry).to_dict()
    plan['manifest'] = {'source': 'fixture', 'revision': None, 'tasks': [
        {'id': name, 'sha256': 'frozen'} for name in ('a/b', 'a__b')]}
    import_results(plan, tmp_path, [])
    report = json.loads((tmp_path / 'pi/report.json').read_text())
    records = report['evaluations']
    assert len(records) == 2 and all(record['trial_id'] is None for record in records)
    artifact_ids = {record['artifact_id'] for record in records}
    assert len(artifact_ids) == 2
    source = HarnessReport(tmp_path / 'pi', report)
    for identity in artifact_ids:
        directory = source.trial_directory(identity)
        assert directory.parent == tmp_path / 'pi'
        assert (directory / 'evaluation.json').is_file()
    assert not (tmp_path / 'pi/instances').exists()
