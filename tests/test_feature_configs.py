from pathlib import Path
import json
import subprocess
import sys

import pytest
import yaml

from acb.config import RunConfig
from acb.resolver import resolve, HARNESSES
from acb.harbor.dataset import prepare_dataset, verify_manifest
from acb.mcp import MCPServerManager
import importlib.util

# Scripts are checkout tools, not an installed Python package.
_spec = importlib.util.spec_from_file_location('reset_feature_config', Path(__file__).resolve().parents[1] / 'scripts/reset_config.py')
_reset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reset)
reset_config, TEMPLATE = _reset.reset_config, _reset.TEMPLATE

MATRIX = yaml.safe_load((TEMPLATE / 'matrix.yaml').read_text())


@pytest.mark.parametrize('case', MATRIX['cases'], ids=lambda item: Path(item['config']).stem)
def test_feature_configs_resolve_without_execution(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('resolution must not execute commands')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    plan = resolve(RunConfig.from_file(TEMPLATE / case['config'])).to_dict()
    assert plan['execution_backend'] == 'harbor'
    assert plan['max_workers'] == 1
    assert plan['model']['name']
    for harness, config in plan['harnesses'].items():
        MCPServerManager().generate_config(config.get('mcp_servers', []), harness)
    if 'mcp' in case['features']:
        assert 'pi' not in plan['harnesses']
        assert all(c['mcp_servers'][0]['command'] == 'npx' for c in plan['harnesses'].values())
    if 'rtk' in case['features']:
        assert all(c['execution_integrations'][0]['name'] == 'rtk' for c in plan['harnesses'].values())
    if 'context' in case['features']:
        assert all(c['model_middleware'][0]['name'] == 'caveman' for c in plan['harnesses'].values())


def test_inventory_has_all_configs_and_clean_baseline():
    assert {row['config'] for row in MATRIX['cases']} == {str(path.relative_to(TEMPLATE)) for path in (TEMPLATE / 'runs').glob('*.yaml')}
    plan = resolve(RunConfig.from_file(TEMPLATE / 'runs/baseline.yaml')).to_dict()
    assert set(plan['harnesses']) == set(HARNESSES)
    for config in plan['harnesses'].values():
        assert not any(config.get(key) for key in ('skills', 'mcp_servers', 'execution_integrations', 'model_middleware', 'system_prompt'))


@pytest.mark.parametrize('task', sorted(path.name for path in (TEMPLATE / 'tasks').iterdir()))
def test_local_tasks_export_with_their_declared_capabilities(tmp_path, task):
    cfg = RunConfig.from_file(TEMPLATE / 'runs/baseline.yaml')
    cfg.benchmark = task
    cfg.subset = [task]
    plan = resolve(cfg).to_dict()
    plan.update(cache_dir=str(tmp_path / 'cache'), offline=True)
    manifest = prepare_dataset(plan)
    verify_manifest(manifest)
    assert len(manifest['tasks']) == 1
    from harbor.models.task.task import Task
    exported = Task(Path(manifest['tasks'][0]['path']))
    if task == 'multi-step':
        assert [step.name for step in exported.config.steps] == ['first', 'second']
    if task == 'task-skill':
        assert exported.config.environment.skills_dir == '/task-skills'
        assert (exported.paths.environment_dir / 'task-skills/task-guide/SKILL.md').is_file()
    if task == 'resources':
        assert exported.config.environment.cpus == 1
        assert exported.config.environment.memory_mb == 1024


def test_custom_metric_uses_supplied_rewards(tmp_path):
    source = tmp_path / 'rewards.jsonl'
    source.write_text('{"quality":1}\nnull\n{"quality":0}\n')
    output = tmp_path / 'result.json'
    subprocess.run([sys.executable, str(TEMPLATE / 'assets/metrics/score.py'), '-i', str(source), '-o', str(output)], check=True)
    assert json.loads(output.read_text()) == {'quality_sum': 1, 'graded_attempts': 2}


def test_reset_archives_results_and_preserves_models_without_old_treatments(tmp_path):
    root = tmp_path / 'config'
    (root / 'phase6/runs').mkdir(parents=True)
    (root / 'phase6/runs/result.json').write_text('saved evidence')
    (root / 'models.yaml').write_text('local-qwen:\n  model: local-selected\n  api: openai\n  endpoint: localhost:1234\n  tls: false\n')
    (root / 'harnesses.yaml').write_text('pi:\n  system_prompt: old experiment\n')
    backup = reset_config(root, backup_dir=tmp_path / 'backups')
    assert (backup / 'phase6/runs/result.json').read_text() == 'saved evidence'
    assert not (root / 'phase6').exists()
    plan = resolve(RunConfig.from_file(root / 'runs/baseline.yaml')).to_dict()
    assert plan['model']['name'] == 'local-selected'
    assert all(not c.get('system_prompt') for c in plan['harnesses'].values())
    assert len(list((root / 'runs').glob('*.yaml'))) == len(MATRIX['cases'])


def test_invalid_template_does_not_move_existing_config(tmp_path):
    import shutil
    root = tmp_path / 'config'; root.mkdir()
    (root / 'keep').write_text('original')
    template = tmp_path / 'template'; shutil.copytree(TEMPLATE, template)
    (template / 'runs/baseline.yaml').write_text('invalid: true\n')
    with pytest.raises(ValueError):
        reset_config(root, template=template, backup_dir=tmp_path / 'backups')
    assert (root / 'keep').read_text() == 'original'
