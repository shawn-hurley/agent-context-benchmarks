import json
from pathlib import Path
from copy import deepcopy

import pytest

from acb.config import Registries, RunConfig
from acb.resolver import resolve


def registry():
    return Registries({}, {}, {"local": {"api": "openai", "endpoint": "localhost:8000", "tls": False}})


def config(**kwargs):
    return RunConfig(**{"run_id": "smoke", "benchmark": "rh-swe-bench", "harness": ["goose", "pi", "opencode", "claude-code"], "model": "local", **kwargs})


def test_shared_selection_and_baseline_parity():
    r = registry()
    before = deepcopy(r)
    baseline = resolve(config(extensions=[]), r).to_dict()
    treatment = resolve(config(extensions=["rtk"]), r).to_dict()
    assert r == before
    for name, settings in treatment["harnesses"].items():
        assert settings["version"] == baseline["harnesses"][name]["version"]
        assert settings.get("launch_profile") == baseline["harnesses"][name].get("launch_profile")
        assert settings["execution_integrations"][0]["mode"] == ("shell-wrapper" if name == "goose" else "native")
    assert resolve(config(), r) == resolve(config(), r)


def test_workflow_is_selected_at_run_level(tmp_path):
    workflow = tmp_path / "flow"
    workflow.mkdir()
    (workflow / "instruction.md").write_text("Finish the task.\n")
    (workflow / "workflow.yaml").write_text("""\
version: 1
name: user-flow
steps:
  - name: finish
    instruction: instruction.md
    timeout_sec: 60
    gate: {type: native}
""")
    cfg = config(benchmark="scarfbench", harness="goose", workflow="./flow",
                 source_file=str(tmp_path / "run.yaml"))
    plan = resolve(cfg, registry()).to_dict()
    assert plan["workflow"]["name"] == "user-flow"
    assert plan["workflow"]["source_dir"] == str(workflow)
    assert "workflow" not in plan["benchmark_config"]


def test_per_harness_empty_list_replaces_shared():
    plan = resolve(config(extensions=["rtk"], overrides={"harnesses": {"pi": {"extensions": []}}}), registry()).to_dict()
    assert plan["harnesses"]["pi"]["execution_integrations"] == []
    assert plan["harnesses"]["goose"]["execution_integrations"]


@pytest.mark.parametrize("changes,match", [
    ({"execution": {"max_workers": 0}}, "max_workers"),
    ({"harness": ["goose", "goose"]}, "unique"),
    ({"harness": ["missing"]}, "unknown harness"),
    ({"extensions": ["rtk", "rtk"]}, "duplicate"),
    ({"extensions": [{"name": "rtk", "options": {"oops": 1}}]}, "unknown fields"),
    ({"extensions": ["rtk"], "overrides": {"harnesses": {"claude-code": {"launch_profile": "bare"}}}}, "launch_profile"),
    ({"extensions": ["rtk"], "overrides": {"harness": {"execution_integrations": [{"name": "rtk"}]}}}, "named extensions"),
    ({"execution": {"environment": "host"}}, "host execution"),
    ({"overrides": {"harness": {"typo": True}}}, "unknown fields"),
])
def test_invalid_config_fails_without_side_effects(changes, match):
    with pytest.raises(ValueError, match=match):
        resolve(config(**changes), registry())


def test_model_alias_is_distinct_from_wire_id():
    r = registry()
    r.models = {"friendly": {"model": "provider/actual", "api": "anthropic", "endpoint": "api.example.com", "key_env": "MY_KEY"}}
    plan = resolve(config(model="friendly"), r).to_dict()
    assert plan["model_alias"] == "friendly"
    assert plan["model"]["name"] == "provider/actual"
    assert plan["model"]["key_env"] == "MY_KEY"


def test_input_paths_are_source_relative_and_output_is_cwd_relative(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    cfg = config(schema_version=2, source_file=str(tmp_path / "run.yaml"), benchmark={"name": "rh-swe-bench", "path": "tasks"}, output_dir="results")
    plan = resolve(cfg, registry()).to_dict()
    assert plan["benchmark_config"]["path"] == str(tmp_path / "tasks")
    assert plan["output_dir"] == str(outside / "results")


def test_category_identity():
    plan = resolve(config(skills=["caveman"], extensions=["rtk"]), registry()).to_dict()
    assert plan["harnesses"]["pi"]["skills"][0]["name"] == "caveman"
    assert [x["name"] for x in plan["harnesses"]["pi"]["extensions"]] == ["rtk"]


@pytest.mark.parametrize('location', ['registry', 'shared', 'local'])
def test_catalog_selections_normalize_at_every_layer(location):
    r = registry()
    kwargs = {}
    if location == 'registry':
        r.harnesses['pi'] = {'extensions': ['rtk']}
    elif location == 'shared':
        kwargs['overrides'] = {'harness': {'extensions': ['rtk']}}
    else:
        kwargs['overrides'] = {'harnesses': {'pi': {'extensions': ['rtk']}}}
    plan = resolve(config(**kwargs), r).to_dict()
    assert plan['harnesses']['pi']['execution_integrations'][0]['name'] == 'rtk'


def test_catalog_options_keep_defaults_and_reject_unavailable_revision():
    plan = resolve(config(skills=['caveman']), registry()).to_dict()
    assert plan['harnesses']['pi']['skills'][0]['options']['intensity'] == 'lite'
    with pytest.raises(ValueError, match='version is not present'):
        resolve(config(skills=[{'name': 'caveman', 'version': 'unreviewed'}]), registry())


def test_machine_environment_overrides_builtin_default():
    r = registry()
    r.machine = {'environment': 'docker'}
    assert resolve(config(), r).to_dict()['environment'] == 'docker'
    assert resolve(config(execution={'environment': 'podman'}), r).to_dict()['environment'] == 'podman'


def test_per_harness_selection_replaces_registry_selection():
    r = registry()
    r.harnesses['pi'] = {'extensions': ['rtk']}
    plan = resolve(config(overrides={'harnesses': {'pi': {'extensions': []}}}), r).to_dict()
    assert plan['harnesses']['pi']['execution_integrations'] == []


def test_v2_registry_and_machine_paths_use_their_declaring_files(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    registry_dir = project / 'config'
    runs = project / 'experiments'
    registry_dir.mkdir(parents=True)
    runs.mkdir()
    (registry_dir / 'benchmarks.yaml').write_text('harbor:\n  path: ../tasks\n')
    (registry_dir / 'machine.yaml').write_text('cache_dir: ../cache\n')
    (registry_dir / 'models.yaml').write_text('local:\n  api: openai\n  endpoint: localhost:8000\n')
    r = Registries.load(registry_dir)
    monkeypatch.chdir(tmp_path)
    cfg = config(schema_version=2, benchmark='harbor', source_file=str(runs / 'run.yaml'))
    plan = resolve(cfg, r).to_dict()
    assert plan['benchmark_config']['path'] == str(project / 'tasks')
    assert plan['cache_dir'] == str(project / 'cache')
    cfg.overrides = {'benchmark': {'path': './override-tasks'}}
    assert resolve(cfg, r).to_dict()['benchmark_config']['path'] == str(runs / 'override-tasks')


def test_top_level_component_typo_is_not_treated_as_legacy_config():
    with pytest.raises(ValueError, match='unknown fields'):
        resolve(config(skills=[{'name': 'caveman', 'optoins': {}}]), registry())


@pytest.mark.parametrize('location', ['catalog', 'registry', 'shared', 'local', 'top'])
def test_component_asset_paths_preserve_declaring_layer(tmp_path, monkeypatch, location):
    project = tmp_path / 'project'
    registry_dir = project / 'config'
    run_dir = project / 'experiments'
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.chdir(outside)
    r = registry()
    r.sources = [str(registry_dir / name) for name in
                 ('skills.yaml', 'extensions.yaml', 'harnesses.yaml')]
    r.skills = {'local-skill': {'source_type': 'local', 'source_path': '../assets/skill'}}
    r.extensions = {'rtk': {'version': '0.48.0',
                            'options': {'binary_path': '../assets/catalog-rtk', 'sha256': 'abc'}}}
    selections = {'skills': ['local-skill'], 'extensions': ['rtk']}
    if location != 'catalog':
        selections['extensions'] = [{'name': 'rtk', 'options': {'binary_path': './selected-rtk'}}]
    kwargs = {}
    if location in ('catalog', 'top'):
        kwargs.update(selections)
    elif location == 'registry':
        r.harnesses['pi'] = selections
    elif location == 'shared':
        kwargs['overrides'] = {'harness': selections}
    else:
        kwargs['overrides'] = {'harnesses': {'pi': selections}}
    cfg = config(source_file=str(run_dir / 'run.yaml'), **kwargs)
    before = deepcopy((cfg, r))
    plan = resolve(cfg, r).to_dict()
    settings = plan['harnesses']['pi']
    skill_path = str(project / 'assets/skill')
    origin = registry_dir if location in ('catalog', 'registry') else run_dir
    binary_path = str(project / 'assets/catalog-rtk') if location == 'catalog' else str(origin / 'selected-rtk')
    assert settings['skills'][0]['source_path'] == skill_path
    assert settings['extensions'][0]['options']['binary_path'] == binary_path
    assert settings['execution_integrations'][0]['binary_path'] == binary_path
    assert (cfg, r) == before


@pytest.mark.parametrize('location', ['registry', 'shared', 'local'])
def test_inline_assets_and_container_paths(tmp_path, monkeypatch, location):
    monkeypatch.chdir(tmp_path)
    registry_dir = tmp_path / 'config'
    run_dir = tmp_path / 'experiments'
    r = registry()
    r.sources = [str(registry_dir / 'harnesses.yaml')]
    r.sources.append(str(registry_dir / 'skills.yaml'))
    r.skills = {'rgctl': {'source_type': 'local', 'source_path': '../skills/rgctl'}}
    settings = {
        'skills': ['rgctl'],
        'extensions': [{'name': 'rtk', 'options': {'binary_path': './bin/rtk',
                                                 'python_path': '/opt/container/python'}}],
        'workdir': '/work',
    }
    kwargs = {}
    if location == 'registry':
        r.harnesses['pi'] = settings
    elif location == 'shared':
        kwargs['overrides'] = {'harness': settings}
    else:
        kwargs['overrides'] = {'harnesses': {'pi': settings}}
    cfg = config(source_file=str(run_dir / 'run.yaml'), **kwargs)
    before = deepcopy((cfg, r))
    result = resolve(cfg, r).to_dict()['harnesses']['pi']
    origin = registry_dir if location == 'registry' else run_dir
    assert result['skills'][0]['source_path'] == str(tmp_path / 'skills/rgctl')
    assert result['execution_integrations'][0]['binary_path'] == str(origin / 'bin/rtk')
    assert result['execution_integrations'][0]['python_path'] == '/opt/container/python'
    assert result['workdir'] == '/work'
    assert (cfg, r) == before


def test_empty_local_assets_replace_shared_assets(tmp_path):
    plan = resolve(config(schema_version=2, source_file=str(tmp_path / 'run.yaml'),
                          skills=['caveman'], extensions=['rtk'], overrides={
                              'harnesses': {'pi': {'skills': [], 'extensions': []}}}), registry()).to_dict()
    assert plan['harnesses']['pi']['skills'] == []
    assert plan['harnesses']['pi']['execution_integrations'] == []


def test_registry_directory_declared_in_run(tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.chdir(outside)
    cfg = config(config_dir='../settings',
                 source_file=str(tmp_path / 'project/experiments/run.yaml'))
    expected = tmp_path / 'project/settings'
    assert cfg.registry_dir().resolve() == expected


@pytest.mark.parametrize('filename', ['skills.yaml', 'extensions.yaml', 'models.yaml'])
def test_discovery_uses_nearest_project_and_prefers_dot_acb(tmp_path, filename):
    from acb.resolver import discover_config_dir
    project = tmp_path / 'project'
    for directory in (tmp_path / 'config', project / 'config', project / '.acb'):
        directory.mkdir(parents=True)
        (directory / filename).write_text('{}')
    assert discover_config_dir(project / 'experiments') == project / '.acb'


@pytest.mark.parametrize('override', [False, True])
@pytest.mark.parametrize('binary', ['scarf', './bin/scarf'])
def test_remaining_host_paths_and_dataset_roots(tmp_path, monkeypatch, override, binary):
    monkeypatch.chdir(tmp_path)
    r = registry()
    r.sources = [str(tmp_path / 'settings/benchmarks.yaml')]
    paths = {'benchmark_cache_dir': './assets', 'scarf_binary': binary}
    r.benchmarks['scarfbench'] = paths
    cfg = config(benchmark='scarfbench', source_file=str(tmp_path / 'experiments/run.yaml'),
                 overrides={'benchmark': paths} if override else {})
    plan = resolve(cfg, r).to_dict()
    origin = tmp_path / ('experiments' if override else 'settings')
    assert plan['benchmark_config']['benchmark_cache_dir'] == str(origin / 'assets')
    expected_binary = str(origin / 'bin/scarf') if '/' in binary else binary
    assert plan['benchmark_config']['scarf_binary'] == expected_binary


@pytest.mark.parametrize('command', ['resolve', 'prepare', 'run'])
def test_cli_config_dir_override_is_cwd_relative(tmp_path, monkeypatch, capsys, command):
    from acb.cli import main
    import yaml
    outside = tmp_path / 'outside'
    outside.mkdir()
    experiments = tmp_path / 'project/experiments'
    experiments.mkdir(parents=True)
    registry_dir = outside / 'cli-settings'
    registry_dir.mkdir()
    (registry_dir / 'models.yaml').write_text('local:\n  api: openai\n  endpoint: cli.example:8000\n')
    run_file = experiments / 'run.yaml'
    run_file.write_text(yaml.safe_dump({'schema_version': 2, 'run_id': 'paths', 'benchmark': 'harbor',
                                      'harness': 'pi', 'model': 'local', 'config_dir': '../missing'}))
    monkeypatch.chdir(outside)
    captured = []
    if command == 'prepare':
        def prepare(plan):
            captured.append(plan.to_dict())
            return plan.to_dict()
        monkeypatch.setattr('acb.harbor.backend.prepare', prepare)
    elif command == 'run':
        def run(plan, *, verbose, control=None):
            captured.append(plan.to_dict())
        monkeypatch.setattr('acb.harbor.backend.run_plan', run)
    main([command, '--config', str(run_file), '--config-dir', 'cli-settings'])
    plan = json.loads(capsys.readouterr().out) if command == 'resolve' else captured[0]
    assert plan['model']['endpoint'] == 'cli.example:8000'
    assert str(registry_dir / 'models.yaml') in plan['sources']
    assert plan['output_dir'] == str(outside / 'runs')


def test_cli_resolve_from_outside_checkout(tmp_path):
    import subprocess
    import sys
    import yaml
    import os
    from pathlib import Path
    project = tmp_path / 'project'
    experiments = project / 'experiments'
    registry_dir = project / 'settings'
    experiments.mkdir(parents=True)
    registry_dir.mkdir()
    (registry_dir / 'models.yaml').write_text('local:\n  api: openai\n  endpoint: localhost:8000\n')
    (registry_dir / 'benchmarks.yaml').write_text('harbor:\n  path: ../tasks\n')
    run_file = experiments / 'run.yaml'
    run_file.write_text(yaml.safe_dump({'schema_version': 2, 'run_id': 'paths', 'benchmark': 'harbor',
                                      'harness': 'pi', 'model': 'local', 'config_dir': '../settings'}))
    outside = tmp_path / 'outside'
    outside.mkdir()
    # Import the current source in a fresh process while its working directory
    # is unrelated to both the checkout and the experiment.
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, '-c', 'from acb.cli import main; main()',
                             'resolve', '--config', str(run_file)], cwd=outside, env=env,
                            capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan['benchmark_config']['path'] == str(project / 'tasks')
    assert plan['output_dir'] == str(outside / 'runs')
    assert plan['sources'] == [str(registry_dir / 'benchmarks.yaml'), str(registry_dir / 'models.yaml'), str(run_file)]
    assert not (experiments / 'runs').exists()


@pytest.mark.parametrize('version', [1, 0, 3, True, 2.0, '2', None])
def test_unsupported_schema_is_rejected_when_loading(tmp_path, version):
    import yaml
    path = tmp_path / 'run.yaml'
    path.write_text(yaml.safe_dump({'schema_version': version, 'run_id': 'unsupported',
                                    'benchmark': 'harbor', 'harness': 'pi', 'model': 'local'}))
    with pytest.raises(ValueError, match='schema_version must be 2'):
        RunConfig.from_file(path)


def test_default_schema_uses_current_paths_and_launch_profile(tmp_path):
    r = registry()
    r.benchmarks['swebench'] = {}
    cfg = config(benchmark='swebench', source_file=str(tmp_path / 'run.yaml'))
    assert cfg.schema_version == 2
    plan = resolve(cfg, r).to_dict()
    assert plan['output_dir'] == str(Path.cwd() / 'runs')
    assert plan['harnesses']['claude-code']['launch_profile'] == 'isolated-hooks'


def test_mutated_schema_cannot_bypass_resolver_or_runner_validation():
    from acb.harbor.backend import run
    cfg = config()
    cfg.schema_version = 1
    for action in (resolve, run):
        with pytest.raises(ValueError, match='schema_version must be 2'):
            action(cfg, registry())


@pytest.mark.parametrize('changes,match', [
    ({'harness': [3]}, 'harness must'),
    ({'harness': {'pi': {}}}, 'harness must'),
    ({'benchmark': {'name': 'unknown'}}, 'unknown benchmark'),
    ({'overrides': {'benchmark': []}}, 'overrides.benchmark'),
    ({'overrides': {'proxy': {'typo': True}}}, 'proxy'),
    ({'execution': {'offline': 'false'}}, 'execution.offline'),
    ({'execution': {'timeout': True}}, 'execution.timeout'),
    ({'execution': {'max_workers': 1.5}}, 'execution.max_workers'),
    ({'execution': {'cache_policy': []}}, 'cache_policy'),
    ({'overrides': {'harness': {'max_budget_usd': float('nan')}}}, 'max_budget_usd'),
    ({'overrides': {'harness': {'launch_profile': 'typo'}}}, 'launch_profile'),
    ({'extensions': [], 'overrides': {'harness': {'extensions': []}, 'harnesses': {name: {'extensions': []} for name in ('goose', 'pi', 'opencode', 'claude-code')}}}, 'ambiguous'),
])
def test_invalid_settings_have_field_errors(changes, match):
    with pytest.raises(ValueError, match=match):
        resolve(config(**changes), registry())


@pytest.mark.parametrize('category,value,match', [
    ('machine', {'typo': 1}, 'machine: unknown'),
    ('machine', {'environment': 'auto'}, 'machine.environment'),
    ('machine', {'cache_dir': []}, 'machine.cache_dir'),
    ('models', {'local': {'api': 'openai', 'endpoint': 'localhost', 'typo': 1}}, 'models.local'),
    ('models', {'local': {'api': 'openai', 'endpoint': 'localhost', 'tls': 'false'}}, 'model.tls'),
    ('models', {'local': {'model': []}}, 'models.local.model'),
    ('benchmarks', {'rh-swe-bench': []}, 'benchmarks.rh-swe-bench'),
    ('harnesses', {'pi': []}, 'harnesses.pi'),
])
def test_invalid_registry_settings_have_field_errors(category, value, match):
    r = registry()
    setattr(r, category, value)
    with pytest.raises(ValueError, match=match):
        resolve(config(), r)


def test_precedence_budget_model_and_backend_without_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('resolution must not start processes or access the network')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr('socket.create_connection', forbidden)
    r = registry()
    r.machine = {'environment': 'docker'}
    r.benchmarks['rh-swe-bench'] = {'environment': 'podman', 'attempts': 2}
    r.harnesses['pi'] = {'timeout': 100, 'system_prompt': 'registry'}
    r.models['alias'] = {'model': 'local', 'api': 'openai', 'endpoint': 'alias.example:8000'}
    cfg = config(model='alias', benchmark={'name': 'rh-swe-bench', 'attempts': 3},
                 execution={'max_workers': 4, 'timeout': 400, 'environment': 'docker'},
                 overrides={'benchmark': {'attempts': 5, },
                            'harness': {'timeout': 200, 'system_prompt': 'shared'},
                            'harnesses': {'pi': {'timeout': 300, 'system_prompt': 'local'}}})
    before = deepcopy((cfg, r))
    plan = resolve(cfg, r).to_dict()
    assert plan['execution_backend'] == 'harbor'
    assert plan['attempts'] == 5
    assert plan['environment'] == 'docker'
    assert plan['max_workers'] == 4
    assert 'max_workers' not in plan['benchmark_config']
    assert all(settings['timeout'] == 400 for settings in plan['harnesses'].values())
    assert plan['harnesses']['pi']['system_prompt'] == 'local'
    assert plan['harnesses']['goose']['system_prompt'] == 'shared'
    assert plan['model']['name'] == 'local'
    assert plan['model']['endpoint'] == 'alias.example:8000'
    assert plan['model']['api'] == 'openai'
    assert (cfg, r) == before


def test_explicit_empty_inline_integration_replaces_registry_before_conflict_check():
    r = registry()
    r.harnesses['pi'] = {'extensions': ['rtk']}
    cfg = config(extensions=[], overrides={'harnesses': {'pi': {'extensions': []}}})
    assert resolve(cfg, r).to_dict()['harnesses']['pi']['execution_integrations'] == []


@pytest.mark.parametrize('contents,match', [('[]', 'expected a mapping'), ('max_worker: 1', 'unknown fields')])
def test_yaml_config_errors_name_file_and_field(tmp_path, contents, match):
    path = tmp_path / 'run.yaml'
    path.write_text(contents)
    with pytest.raises(ValueError, match=match) as error:
        RunConfig.from_file(path)
    assert str(path) in str(error.value)


def test_existing_benchmark_fields_are_supported():
    from pathlib import Path
    r = Registries.load(Path(__file__).resolve().parents[1] / 'config.example')
    for name in ('swebench', 'swebench-lite', 'scarfbench'):
        plan = resolve(config(benchmark=name, model='local-qwen'), r).to_dict()
        assert plan['benchmark'] == name
        assert plan['execution_backend'] == 'harbor'
        assert plan['benchmark_config']['reward_metric'] == 'reward'
        assert plan['benchmark_config']['success_value'] == 1


@pytest.mark.parametrize('entries,match', [
    ([{'name': 'rgctl', 'source_type': 'local', 'source_pth': 'skills'}], 'unknown fields'),
    ([{'name': 'rgctl', 'source_type': 'local'}, {'name': 'rgctl', 'source_type': 'local'}], 'unknown fields'),
    ([{'name': 'rgctl', 'source_type': 'typo'}], 'source_type'),
])
def test_inline_skill_errors_are_validated_during_resolve(entries, match):
    with pytest.raises(ValueError, match=match):
        resolve(config(overrides={'harness': {'skills': entries}}), registry())
