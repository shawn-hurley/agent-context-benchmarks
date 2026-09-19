"""Cancel a fixture trial after it starts a long-running shell tool.

Run using the isolated Harbor Python. Requires a prepared bridge fixture plan;
only the deterministic fixture model endpoint is accepted.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import yaml

from acb.harbor.dataset import prepare_dataset
from acb.harbor.paths import job_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--mode', choices=('cancel', 'timeout', 'service-failure'), default='cancel')
    parser.add_argument('--attempts', type=int, default=1)
    parser.add_argument('--phase', choices=('tool', 'stream', 'setup', 'install', 'upload'), default='tool')
    parser.add_argument('--check-isolation', action='store_true')
    parser.add_argument('--check-network', choices=('public', 'no-network'))
    parser.add_argument('--caveman-image', help='Exercise the required Caveman service too')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080':
        raise ValueError('cancellation check requires the deterministic fixture endpoint')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan['harnesses'] = {'goose': {'version': '1.50.1', 'timeout': 12 if args.mode in ('timeout', 'service-failure') else 300}}
    if args.caveman_image:
        from acb.integrations.caveman import REVISION
        plan['caveman_image'] = args.caveman_image
        plan['harnesses']['goose']['model_middleware'] = [
            {'name': 'caveman', 'version': REVISION, 'mode': 'compress',
             'python_path': '/opt/miniconda3/bin/python'}]
    plan.pop('task_plans', None)
    if args.attempts < 1:
        raise ValueError('attempts must be positive')
    plan['attempts'] = args.attempts
    plan['max_workers'] = 1
    plan['model']['name'] = 'fixture-model-wait'
    if args.phase == 'stream':
        plan['model']['name'] = 'fixture-model-stream-wait'
    if args.phase in ('setup', 'install') or args.check_isolation:
        task_root = output / 'tasks'
        shutil.copytree(Path(plan['benchmark_config']['path']) / 'bridge', task_root / 'bridge')
        plan['benchmark_config']['path'] = str(task_root)
    if args.phase == 'install':
        if args.mode != 'cancel':
            raise ValueError('installation checks require cancellation mode')
        environment_dir = task_root / 'bridge/environment'
        (environment_dir / 'chmod-fixture').write_text(
            '#!/bin/bash\nif [ "$*" = "+x /usr/local/bin/goose" ]; then\n'
            'touch /tmp/acb-install-wait\nsleep 600\nfi\nexec /bin/chmod "$@"\n')
        dockerfile = environment_dir / 'Dockerfile'
        dockerfile.write_text(dockerfile.read_text() +
            '\nCOPY chmod-fixture /usr/local/bin/chmod\nRUN /bin/chmod +x /usr/local/bin/chmod\n')
    if args.phase == 'setup':
        if args.mode != 'cancel':
            raise ValueError('setup checks require cancellation mode')
        task_file = task_root / 'bridge/task.toml'
        task_file.write_text(task_file.read_text().replace('[agent]',
            '[environment.healthcheck]\ncommand = "touch /tmp/acb-setup-wait; sleep 600"\n'
            'timeout_sec = 610\n[agent]'))
    if args.check_network:
        if args.phase != 'setup':
            raise ValueError('network checks require --phase setup')
        task_file.write_text(task_file.read_text().replace('[environment]\n',
            f'[environment]\nnetwork_mode = "{args.check_network}"\n'))
        compose_file = task_root / 'bridge/environment/docker-compose.yaml'
        compose = yaml.safe_load(compose_file.read_text())
        compose['services']['fixture-model']['networks'] = ['default']
        compose_file.write_text(yaml.safe_dump(compose))
    if args.check_isolation:
        if args.phase != 'stream':
            raise ValueError('isolation check requires --phase stream')
        compose_file = task_root / 'bridge/environment/docker-compose.yaml'
        compose = yaml.safe_load(compose_file.read_text())
        compose['services']['main'] = {'volumes': ['fixture-data:/fixture-data:ro']}
        compose['volumes'] = {'fixture-data': {}}
        compose_file.write_text(yaml.safe_dump(compose))
        plan['model']['key_env'] = 'ACB_FIXTURE_TOKEN'
    plan['manifest'] = prepare_dataset(plan)
    requested = output / 'fixture-plan.json'
    requested.write_text(json.dumps(plan, indent=2))
    with (output / 'worker.log').open('w') as log:
        worker_env = os.environ.copy()
        if args.phase == 'upload':
            if args.mode != 'cancel':
                raise ValueError('upload checks require cancellation mode')
            if plan['environment'] != 'podman':
                raise ValueError('upload fault injection currently targets podman-compose')
            frontend = shutil.which('podman-compose')
            if not frontend:
                raise RuntimeError('podman-compose is required for this fixture')
            wrappers = output / 'bin'
            wrappers.mkdir()
            wrapper = wrappers / 'podman-compose'
            marker = output / 'upload-processes.json'
            wrapper.write_text(f'#!{sys.executable}\n' +
                'import json,os,subprocess,sys\nfrom pathlib import Path\n' +
                'if "cp" in sys.argv and "main:/usr/local/bin/goose" in sys.argv:\n' +
                '    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(600)"])\n' +
                f'    Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n' +
                '    child.wait()\nelse:\n' +
                f'    os.execv({frontend!r}, [{frontend!r}, *sys.argv[1:]])\n')
            wrapper.chmod(0o755)
            worker_env['PATH'] = str(wrappers) + os.pathsep + worker_env.get('PATH', '')
        if args.check_isolation:
            worker_env['ACB_FIXTURE_TOKEN'] = 'synthetic-isolation-canary'
        process = subprocess.Popen([sys.executable, '-m', 'acb.harbor.worker', 'run', str(requested), str(output)],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=worker_env)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                transcripts = list((job_dir(output)).glob('*/agent/acb/transcript.jsonl'))
                ready = args.phase == 'tool' and any('sleep 600' in path.read_text() for path in transcripts)
                if args.phase == 'upload':
                    ready = (output / 'upload-processes.json').exists()
                if args.phase not in ('tool', 'upload'):
                    service = 'main' if args.phase in ('setup', 'install') else 'fixture-model'
                    marker = f'/tmp/acb-{args.phase}-wait'
                    for trial in (job_dir(output)).glob('*/config.json'):
                        names = subprocess.check_output([plan['environment'], 'ps', '--filter',
                            f'label=com.docker.compose.project={trial.parent.name.lower()}__env',
                            '--filter', f'label=com.docker.compose.service={service}', '--format', '{{.ID}}'], text=True)
                        for container in names.splitlines():
                            check = subprocess.run([plan['environment'], 'exec', container, 'test', '-f', marker],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            ready |= check.returncode == 0
                if ready:
                    break
                if process.poll() is not None:
                    raise RuntimeError('fixture exited before requested cancellation phase; inspect worker.log')
                time.sleep(.2)
            else:
                raise TimeoutError('fixture never reached requested cancellation phase')
            if args.check_isolation or args.check_network:
                project = next((job_dir(output)).glob('*/config.json')).parent.name.lower() + '__env'
                def inspect_service(service):
                    ids = subprocess.check_output([plan['environment'], 'ps', '--filter',
                        f'label=com.docker.compose.project={project}', '--filter',
                        f'label=com.docker.compose.service={service}', '--format', '{{.ID}}'], text=True).splitlines()
                    assert len(ids) == 1, service
                    return ids[0], json.loads(subprocess.check_output(
                        [plan['environment'], 'container', 'inspect', ids[0]], text=True))[0]
                main_id, main = inspect_service('main')
            if args.check_network:
                canary_id, _ = inspect_service('fixture-model')
                probe = 'import urllib.request; urllib.request.urlopen("http://HOST:18080/v1/models",timeout=2).read()'
                deadline = time.monotonic() + 30
                while True:
                    result = subprocess.run([plan['environment'], 'exec', canary_id, '/opt/miniconda3/bin/python',
                        '-c', probe.replace('HOST', '127.0.0.1')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if result.returncode == 0:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('network canary never became healthy')
                    time.sleep(.2)
                result = subprocess.run([plan['environment'], 'exec', main_id, '/opt/miniconda3/bin/python',
                    '-c', probe.replace('HOST', 'fixture-model')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                reachable = result.returncode == 0
                assert reachable == (args.check_network == 'public')
                (output / 'network-check.json').write_text(json.dumps({
                    'passed': True, 'mode': args.check_network, 'canary_healthy': True,
                    'canary_reachable_from_agent': reachable,
                    'scope': 'reachability of a separate local Compose service during setup',
                }, indent=2))
            if args.check_isolation:
                _, proxy = inspect_service('acb-praxis')
                canary = 'ACB_FIXTURE_TOKEN=synthetic-isolation-canary'
                assert canary not in main['Config']['Env']
                assert canary in proxy['Config']['Env']
                if args.caveman_image:
                    _, middleware = inspect_service('acb-caveman')
                    assert canary not in middleware['Config']['Env']
                    assert not any(m['Destination'] == '/fixture-data' for m in middleware['Mounts'])
                    assert middleware['HostConfig']['NetworkMode'].startswith('container:')
                mounts = [mount for mount in main['Mounts'] if mount['Destination'] == '/fixture-data']
                assert len(mounts) == 1 and mounts[0]['RW'] is False
                result = subprocess.run([plan['environment'], 'exec', main_id, 'touch', '/fixture-data/forbidden'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                assert result.returncode != 0, 'read-only task volume became writable'
                (output / 'isolation-check.json').write_text(json.dumps({
                    'passed': True, 'credential_scope': 'proxy environment only',
                    'task_volume_preserved': True, 'task_volume_write_rejected': True,
                    'scope': 'environment credential delivery and read-only volume; not a hostile-agent security audit',
                }, indent=2))
            if args.mode == 'service-failure':
                if not args.caveman_image:
                    raise ValueError('service-failure requires --caveman-image')
                project = next((job_dir(output)).glob('*/config.json')).parent.name.lower() + '__env'
                ids = subprocess.check_output([plan['environment'], 'ps', '--filter',
                    f'label=com.docker.compose.project={project}', '--filter',
                    'label=com.docker.compose.service=acb-caveman', '--format', '{{.ID}}'], text=True).splitlines()
                assert len(ids) == 1
                subprocess.run([plan['environment'], 'stop', '--time', '0', ids[0]], check=True, capture_output=True)
            if args.mode == 'cancel':
                cancelled_at = time.monotonic()
                os.kill(process.pid, signal.SIGINT)
            code = process.wait(timeout=150)
            if args.mode == 'cancel' and code == 0:
                raise AssertionError('cancelled job returned success')
            if args.mode != 'cancel':
                execution = json.loads((output / 'execution-status.json').read_text())
                assert bool(code) == bool(execution['infrastructure_failures']), execution
            if args.phase in ('install', 'upload') and time.monotonic() - cancelled_at > 45:
                raise AssertionError('setup cancellation waited for the command timeout')
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGTERM)
                process.wait(timeout=30)
    results = [json.loads(p.read_text()) for p in (job_dir(output)).glob('*/result.json')]
    if args.phase == 'upload':
        for pid in json.loads((output / 'upload-processes.json').read_text()):
            state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
            assert state.returncode != 0 or state.stdout.strip().startswith('Z'), 'copy process survived cancellation'
    if not results or not all(result.get('exception_info') for result in results):
        raise AssertionError('missing cancellation failure evidence')
    running = subprocess.check_output([plan['environment'], 'ps', '-a', '--format', '{{.Names}}'], text=True)
    for result in results:
        prefix = result['trial_name'].lower()
        if prefix in running.lower():
            raise AssertionError(f'trial container survived cancellation: {prefix}')
    report = json.loads((output / 'goose/report.json').read_text())
    expected = len(plan['manifest']['tasks']) * plan['attempts']
    if report['evaluation_errors'] != expected or report['instances'] != expected:
        raise AssertionError('report did not count every cancelled trial')
    if report['incomplete_measurements'] != expected:
        raise AssertionError('aborted or missing trials claimed complete measurements')
    if report['missing_trials'] != expected - len(results):
        raise AssertionError('report did not preserve un-emitted trial slots')
    (output / 'cancellation-check.json').write_text(json.dumps({
        'mode': args.mode,
        'phase': args.phase,
        'scheduled_trials': expected, 'emitted_trials': len(results), 'missing_trials': report['missing_trials'],
        'passed': True, 'worker_exit_code': code,
        'verified': [f'{args.phase} phase reached', 'worker cancelled', 'trial exceptions retained',
                     'reports count cancelled trials', 'no matching containers (including stopped)'],
    }, indent=2))
    print(f'{args.mode} check passed: {output}')


if __name__ == '__main__':
    main()
