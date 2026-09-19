"""Controlled four-harness Harbor RTK activation and Caveman skill delivery.

Uses the cached fixture images and deterministic API service, not a real model.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.preparation import freeze_provider_images, prepare_assets
from acb.resolver import defaults


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080' or plan['subset'] != ['bridge']:
        raise ValueError('requires the deterministic bridge fixture plan')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = output / 'tasks/bridge'
    shutil.copytree(Path('tests/fixtures/harbor/bridge'), task)
    # Generate a meaningful git-status payload in the AGENT's workspace too.
    source = (task / 'environment/server.py').read_text().split('COMMANDS =')[0]
    (task / 'environment/seed.py').write_text(source.replace('Path("/testbed")', 'Path("/work")'))
    with (task / 'environment/Dockerfile').open('a') as handle:
        handle.write('\nCOPY seed.py /seed.py\nRUN /opt/miniconda3/bin/python /seed.py\n')
    plan.pop('task_plans', None)
    plan['benchmark_config']['path'] = str(task.parent)
    plan['benchmark_config'].setdefault('praxis_image', 'acb-praxis-ai:latest')
    plan['attempts'] = 1
    catalog = defaults()
    for name, settings in plan['harnesses'].items():
        settings['version'] = catalog['harnesses'][name]['version']
        settings['timeout'] = 120
        settings['skills'] = [{'name': 'caveman', **deepcopy(catalog['skills']['caveman'])}]
        settings['execution_integrations'] = [{'name': 'rtk', 'version': '0.48.0',
            'mode': 'shell-wrapper' if name == 'goose' else 'native', 'experimental': True,
            'binary_path': str(args.binary.resolve()), 'sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest()}]
    plan['manifest'] = prepare_dataset(plan)
    requested = output / 'prepared.json'
    def worker(action, destination):
        requested.write_text(json.dumps(plan, indent=2))
        with (output / f'{action}.log').open('w') as log:
            subprocess.run([sys.executable, '-m', 'acb.harbor.worker', action,
                            str(requested), str(destination)], stdout=log, stderr=subprocess.STDOUT, check=True)
    worker('inspect', output / 'inspection')
    record = json.loads(next((output / 'inspection/harbor').glob('*/agent/acb/runtime.json')).read_text())
    runtime = {key: value for key, value in record.items()
               if key not in ('task_id', 'harness_version', 'launch_profile')}
    plan['runtime_contracts'] = {'bridge': runtime}
    plan['verifier_contracts'] = json.loads((output / 'inspection/verifier-contracts.json').read_text())
    plan = freeze_provider_images(prepare_assets(plan))
    worker('run', output / 'measured')
    checks = {}
    for name in plan['harnesses']:
        report = json.loads((output / 'measured' / name / 'report.json').read_text())
        assert report['resolved'] == 1 and report['incomplete_measurements'] == 0, name
        trial = output / 'measured/harbor' / report['evaluations'][0]['trial_name']
        artifacts = trial / 'agent/acb'
        manifest = json.loads((artifacts / 'integrations/rtk/manifest.json').read_text())
        activity = manifest['metadata']['activity']
        assert activity['commands_rewritten'] >= 1 and activity['passthroughs'] >= 2, (name, activity)
        assert activity['agent_tool_verified'] and activity['errors'] == 0, (name, activity)
        assert activity['compression_observed'], (name, activity)
        delivery = json.loads((artifacts / 'instruction-delivery.json').read_text())
        assert delivery['components'][0]['sha256'] == catalog['skills']['caveman']['sha256']
        ledger = next(trial.rglob('fixture-requests.jsonl')).read_text()
        assert 'Caveman response style, intensity=lite' in ledger, name
        assert 'Selected intensity: lite' in ledger, name
        requests = [json.loads(line) for line in ledger.splitlines()]
        usage = [json.loads(line) for line in (output / 'measured' / name / 'usage.jsonl').read_text().splitlines()]
        assert len(requests) == len(usage), name
        assert len({row['request_id'] for row in usage}) == len(usage), name
        assert report['avg_total_tokens'] == len(requests) * 120, name
        assert 'ACB_STDERR' in ledger and 'ACB_UNSUPPORTED' in ledger, name
        assert json.loads((trial / 'verifier/proxy-stopped.json').read_text())['stopped']
        checks[name] = {'reward': 1, 'measurement_complete': True,
                        'rtk_activity': activity, 'caveman_instructions_reached_model': True,
                        'request_count': len(requests), 'fixture_tokens': len(requests) * 120,
                        'model_compliance': 'not tested; deterministic API fixture'}
    (output / 'treatment-check.json').write_text(json.dumps({'passed': True, 'harnesses': checks}, indent=2))
    print(f'Harbor treatment check passed: {output}')


if __name__ == '__main__':
    main()
