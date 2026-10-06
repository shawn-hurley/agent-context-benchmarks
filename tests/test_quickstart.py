import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from acb.config import RunConfig
from acb.harbor.dataset import prepare_dataset, verify_manifest
from acb.resolver import resolve

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('quickstart_check', ROOT / 'scripts/check_quickstart.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_quickstart_is_portable_and_exports_without_model_or_engine(tmp_path, monkeypatch):
    output = tmp_path / 'outside'; output.mkdir()
    config = check.setup_config(output, 'docker')
    monkeypatch.chdir(output)
    def forbidden(*args, **kwargs):
        pytest.fail('configuration and local task export must not execute commands')
    monkeypatch.setattr(check.subprocess, 'run', forbidden)
    plan = resolve(RunConfig.from_file(config / 'run.yaml')).to_dict()
    assert plan['environment'] == 'docker'
    assert plan['harnesses'].keys() == {'goose'}
    assert plan['max_workers'] == plan['attempts'] == 1
    assert plan['cache_dir'] == str(output / 'cache')
    assert plan['harnesses']['goose']['timeout'] == 300
    manifest = prepare_dataset(plan)
    verify_manifest(manifest)
    assert len(manifest['tasks']) == 1
    assert Path(manifest['tasks'][0]['path']).is_relative_to(output)
    scarf = resolve(RunConfig.from_file(config / 'scarfbench-cart.yaml')).to_dict()
    assert scarf['subset'] == ['business_domain/cart/jakarta-to-quarkus']
    assert scarf['benchmark_config']['maven_cache_volume'] == 'acb-quickstart-maven-v1'


def test_supplied_model_alias_preserves_credential_environment_name(tmp_path):
    models = tmp_path / 'models.yaml'
    models.write_text(yaml.safe_dump({'selected': {'model': 'actual-model', 'api': 'openai',
                                                 'endpoint': 'example.invalid', 'key_env': 'WORK_KEY'}}))
    output = tmp_path / 'output'; output.mkdir()
    config = check.setup_config(output, 'podman', models, 'selected')
    plan = resolve(RunConfig.from_file(config / 'run.yaml')).to_dict()
    assert plan['model']['name'] == 'actual-model'
    assert plan['model']['key_env'] == 'WORK_KEY'


def trial(tmp_path, *, reward=1, complete=True):
    run = tmp_path / 'run'
    harness = run / 'goose'
    directory = harness / 'smoke'
    directory.mkdir(parents=True)
    (harness / 'report.json').write_text(json.dumps({'harness': 'goose', 'instances': 1,
        'evaluations': [{'trial_id': 'smoke', 'status': 'completed', 'exception': None,
                         'rewards': {'reward': reward}, 'measurement_complete': complete}]}))
    (harness / 'usage.jsonl').write_text('{"instance_id":"smoke","input_tokens":100,"output_tokens":20}\n')
    (harness / 'benchmark_metrics.jsonl').write_text('{"content_type":"tool_call","tools":[{"name":"shell"}]}\n')
    (directory / 'transcript.jsonl').write_text(json.dumps({'message': {'role': 'assistant', 'id': 'm1',
        'content': [{'type': 'toolRequest', 'id': 'call1'}]}}) + '\n')
    return run


def test_acceptance_checks_grade_measurements_and_observed_tools(tmp_path):
    result = check.verify_run(trial(tmp_path), 'goose', 1, measured=True)
    assert result['tokens'] == 120
    assert result['tool_calls'] == result['classified_tool_calls'] == 1


@pytest.mark.parametrize('failure', ['grade', 'coverage', 'proxy-tools', 'trace-tools', 'usage', 'patch'])
def test_acceptance_rejects_incomplete_evidence(tmp_path, failure):
    run = trial(tmp_path, reward=0 if failure == 'grade' else 1, complete=failure != 'coverage')
    files = {'proxy-tools': run / 'goose/benchmark_metrics.jsonl',
             'trace-tools': run / 'goose/smoke/transcript.jsonl', 'usage': run / 'goose/usage.jsonl'}
    if failure in files:
        files[failure].unlink()
    with pytest.raises(ValueError):
        check.verify_run(run, 'goose', 1, measured=True, patch=failure == 'patch')


@pytest.mark.parametrize('link', ['missing.html', '../outside.html', 'https://example.invalid/script.js', '#missing'])
def test_bundle_check_rejects_broken_or_remote_navigation(tmp_path, link):
    archive = tmp_path / 'bad.zip'
    with ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', f'<a href="{link}">Details</a>')
    with pytest.raises(ValueError):
        check.verify_bundle(archive, tmp_path / 'extracted')


def test_bundle_check_accepts_offline_navigation_and_assets(tmp_path):
    archive = tmp_path / 'good.zip'
    with ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', '<script src="chart.js"></script><a href="detail.html#grade">Details</a>')
        bundle.writestr('chart.js', '// local chart')
        bundle.writestr('detail.html', '<h1 id="grade">Grade</h1><a href="index.html">Overview</a>')
    assert check.verify_bundle(archive, tmp_path / 'extracted') == {'html_pages': 2, 'local_links': 3}


def test_missing_buildx_stops_before_preparation_or_model_execution(tmp_path, monkeypatch):
    from types import SimpleNamespace
    output = tmp_path / 'check'
    def run(argv, **kwargs):
        if argv[0] == check.sys.executable:
            pytest.fail('missing build prerequisites must stop before ACB preparation or execution')
        kwargs['stdout'].write('fixture revision or dependency version\n')
        return SimpleNamespace(returncode=1 if 'buildx' in argv else 0)
    monkeypatch.setattr(check.subprocess, 'run', run)
    monkeypatch.setattr(check.sys, 'argv', ['check', str(output), '--environment', 'docker', '--live'])
    monkeypatch.setattr(check.platform, 'platform', lambda: 'fixture-platform')
    with pytest.raises(RuntimeError, match='buildx-version failed'):
        check.main()
    result = json.loads((output / 'check.json').read_text())
    assert result['passed'] is False and result['runs'] == []
    assert 'buildx-version failed' in result['error']
    assert not (output / 'config').exists()
