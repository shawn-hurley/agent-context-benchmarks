"""Verify setup/agent/verifier policy transitions against local canary services."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

from acb.harbor.dataset import prepare_dataset
from acb.harbor.paths import job_dir


PROBE = '''import json,sys,urllib.request
from pathlib import Path
phase = sys.argv[1]
expected = {'setup': [True, True], 'agent': [True, False], 'verifier': [False, False]}[phase]
observed = []
for host in ('fixture-model', 'denied-model'):
    try:
        urllib.request.urlopen('http://' + host + ':18080/v1/models', timeout=2).read()
        observed.append(True)
    except OSError:
        observed.append(False)
directory = Path('/logs/verifier' if phase == 'verifier' else '/logs/agent')
directory.mkdir(parents=True, exist_ok=True)
(directory / ('network-' + phase + '.json')).write_text(json.dumps({
    'phase': phase, 'expected': expected, 'observed': observed, 'passed': observed == expected}))
assert observed == expected, (phase, observed, expected)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080' or plan['subset'] != ['bridge']:
        raise ValueError('requires the deterministic bridge fixture')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'tasks'
    task = root / 'bridge'
    shutil.copytree(Path(plan['benchmark_config']['path']) / 'bridge', task)
    environment = task / 'environment'
    (environment / 'network_probe.py').write_text(PROBE)
    dockerfile = environment / 'Dockerfile'
    dockerfile.write_text(dockerfile.read_text() + '\nCOPY network_probe.py /network_probe.py\n')
    server = environment / 'server.py'
    server.write_text(server.read_text().replace('COMMANDS = [',
        'COMMANDS = [\n    "/opt/miniconda3/bin/python /network_probe.py agent",'))
    compose_path = environment / 'docker-compose.yaml'
    compose = yaml.safe_load(compose_path.read_text())
    compose['services']['fixture-model']['networks'] = ['default']
    compose['services']['denied-model'] = dict(compose['services']['fixture-model'])
    compose_path.write_text(yaml.safe_dump(compose))
    task_file = task / 'task.toml'
    task_file.write_text(task_file.read_text().replace('[agent]',
        '[environment.healthcheck]\ncommand = "/opt/miniconda3/bin/python /network_probe.py setup"\n'
        '[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = ["fixture-model"]')
        .replace('[verifier]', '[verifier]\nnetwork_mode = "no-network"'))
    verifier = task / 'tests/test.sh'
    verifier.write_text(verifier.read_text().replace('set -eu',
        'set -eu\n/opt/miniconda3/bin/python /network_probe.py verifier'))
    plan.pop('task_plans', None)
    plan['harnesses'] = {'goose': plan['harnesses']['goose']}
    plan['attempts'] = plan['max_workers'] = 1
    plan['benchmark_config']['path'] = str(root)
    plan['manifest'] = prepare_dataset(plan)
    requested = output / 'fixture-plan.json'
    requested.write_text(json.dumps(plan, indent=2))
    with (output / 'worker.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'acb.harbor.worker', 'run', str(requested), str(output)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    report = json.loads((output / 'goose/report.json').read_text())
    assert report['resolved'] == 1 and report['incomplete_measurements'] == 0
    trial = job_dir(output) / report['evaluations'][0]['trial_name']
    records = {phase: json.loads((trial / ('verifier' if phase == 'verifier' else 'agent') / f'network-{phase}.json').read_text())
               for phase in ('setup', 'agent', 'verifier')}
    assert all(record['passed'] for record in records.values())
    containers = subprocess.check_output([plan['environment'], 'ps', '-a', '--format', '{{.Names}}'], text=True)
    assert trial.name.lower() not in containers.lower()
    (output / 'network-phase-check.json').write_text(json.dumps({'passed': True, 'phases': records}, indent=2))
    print(f'Network phase check passed: {output}')


if __name__ == '__main__':
    main()
