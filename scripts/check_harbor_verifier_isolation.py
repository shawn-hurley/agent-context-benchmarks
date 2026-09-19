"""Verify synthetic credential and filesystem isolation in a separate verifier."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.harbor.paths import job_dir


AGENT_CHECK = '''test "$ACB_AGENT_CANARY" = agent-only &&
test -z "${ACB_PROVIDER_CANARY+x}" && test -z "${ACB_VERIFIER_CANARY+x}" &&
test ! -f /tests/test.sh &&
printf '{"passed":true}\\n' > /logs/agent/credential-check.json'''

VERIFIER = '''#!/bin/bash
set -eu
test "$ACB_VERIFIER_CANARY" = verifier-only
test -z "${ACB_AGENT_CANARY+x}"
test -z "${ACB_PROVIDER_CANARY+x}"
test ! -f /work/answer.txt
test ! -f /usr/local/bin/goose
if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/18880' 2>/dev/null; then
    echo 'agent proxy reachable from separate verifier' >&2
    exit 1
fi
mkdir -p /logs/verifier
printf '{"passed":true,"agent_files_absent":true,"proxy_unreachable":true}\\n' > /logs/verifier/isolation.json
printf '1\\n' > /logs/verifier/reward.txt
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
    server = task / 'environment/server.py'
    server.write_text(server.read_text().replace('COMMANDS = [', 'COMMANDS = [\n    ' + repr(AGENT_CHECK) + ','))
    config = task / 'task.toml'
    config.write_text(config.read_text() + '''
[environment.env]
ACB_AGENT_CANARY = "agent-only"
[verifier.env]
ACB_VERIFIER_CANARY = "verifier-only"
[verifier.environment]
workdir = "/verify"
''')
    (task / 'tests/Dockerfile').write_text('FROM docker.io/library/ubuntu:22.04\nWORKDIR /verify\nCOPY test.sh /tests/test.sh\n')
    (task / 'tests/test.sh').write_text(VERIFIER)
    plan.pop('task_plans', None)
    plan['harnesses'] = {'goose': plan['harnesses']['goose']}
    plan['model']['key_env'] = 'ACB_PROVIDER_CANARY'
    plan['attempts'] = plan['max_workers'] = 1
    plan['benchmark_config']['path'] = str(root)
    plan['manifest'] = prepare_dataset(plan)
    requested = output / 'fixture-plan.json'
    requested.write_text(json.dumps(plan, indent=2))
    env = os.environ.copy()
    env['ACB_PROVIDER_CANARY'] = 'synthetic-provider-only'
    with (output / 'inspect.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'acb.harbor.worker', 'inspect', str(requested), str(output / 'inspection')],
                       stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    plan['verifier_contracts'] = json.loads((output / 'inspection/verifier-contracts.json').read_text())
    assert len(plan['verifier_contracts']) == 1
    requested.write_text(json.dumps(plan, indent=2))
    with (output / 'worker.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'acb.harbor.worker', 'run', str(requested), str(output)],
                       stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    report = json.loads((output / 'goose/report.json').read_text())
    assert report['resolved'] == 1 and report['incomplete_measurements'] == 0
    trial = job_dir(output) / report['evaluations'][0]['trial_name']
    agent = json.loads((trial / 'agent/credential-check.json').read_text())
    verifier = json.loads((trial / 'verifier/isolation.json').read_text())
    assert agent['passed'] and verifier['passed']
    key, expected = next(iter(plan['verifier_contracts'].items()))
    actual = json.loads((trial / f'agent/acb/verifier-runtime-{key}.json').read_text())
    assert actual == expected
    containers = subprocess.check_output([plan['environment'], 'ps', '-a', '--format', '{{.Names}}'], text=True)
    assert trial.name.lower() not in containers.lower()
    (output / 'verifier-isolation-check.json').write_text(json.dumps({
        'passed': True, 'agent': agent, 'verifier': verifier, 'containers_remaining': 0,
        'verifier_contract': actual,
        'scope': 'synthetic credential variables, agent files, and loopback proxy access',
    }, indent=2))
    print(f'Separate verifier isolation passed: {output}')


if __name__ == '__main__':
    main()
