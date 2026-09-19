"""Inspect, freeze, and run the deterministic fixture with pinned service images."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.preparation import freeze_provider_images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--caveman-image', help='Include a cached Caveman provider in record mode')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080' or plan['subset'] != ['bridge']:
        raise ValueError('requires the deterministic bridge fixture')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan.pop('task_plans', None)
    if args.caveman_image:
        from acb.integrations.caveman import REVISION
        plan['caveman_image'] = args.caveman_image
        for settings in plan['harnesses'].values():
            settings['model_middleware'] = [{'name': 'caveman', 'version': REVISION, 'mode': 'record'}]
    plan['attempts'] = 1
    plan['manifest'] = prepare_dataset(plan)
    # Older fixture plans relied on the worker's explicit local Praxis default.
    plan['benchmark_config'].setdefault('praxis_image', 'acb-praxis-ai:latest')
    plan = freeze_provider_images(plan)
    requested = output / 'prepared.json'
    requested.write_text(json.dumps(plan, indent=2))
    def worker(action, destination):
        with (output / f'{action}.log').open('w') as log:
            subprocess.run([sys.executable, '-m', 'acb.harbor.worker', action,
                            str(requested), str(destination)], stdout=log, stderr=subprocess.STDOUT, check=True)
    worker('inspect', output / 'inspection')
    record = json.loads(next((output / 'inspection/harbor').glob('*/agent/acb/runtime.json')).read_text())
    runtime = {key: value for key, value in record.items()
               if key not in ('task_id', 'harness_version', 'launch_profile')}
    assert 'fixture-model' in runtime['service_images']
    if args.caveman_image:
        assert runtime['python_path']
        for settings in plan['harnesses'].values():
            settings['model_middleware'][0]['python_path'] = runtime['python_path']
    plan['verifier_contracts'] = json.loads((output / 'inspection/verifier-contracts.json').read_text())
    plan['task_plans'] = {'bridge': {'runtime': runtime, 'harnesses': plan['harnesses']}}
    requested.write_text(json.dumps(plan, indent=2))
    worker('run', output / 'measured')
    observed = {}
    for name in plan['harnesses']:
        report = json.loads((output / 'measured' / name / 'report.json').read_text())
        assert report['resolved'] == 1 and report['incomplete_measurements'] == 0
        trial = output / 'measured/harbor' / report['evaluations'][0]['trial_name'] / 'agent/acb'
        actual = json.loads((trial / 'runtime.json').read_text())
        assert actual['container']['image_id'] == runtime['container']['image_id']
        assert actual['service_images'] == runtime['service_images']
        providers = json.loads((trial / 'provider-images.json').read_text())
        assert providers == {service: value['image_id'] for service, value in plan['provider_images'].items()}
        observed[name] = {'reward': 1, 'service_images': actual['service_images'], 'providers': providers}
    (output / 'service-image-check.json').write_text(json.dumps({'passed': True, 'harnesses': observed}, indent=2))
    print(f'Service image check passed: {output}')


if __name__ == '__main__':
    main()
