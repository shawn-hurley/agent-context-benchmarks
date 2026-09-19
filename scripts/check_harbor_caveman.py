"""Controlled harness compression/retrieval acceptance through Harbor's real worker."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.integrations.caveman import REVISION
from acb.preparation import freeze_provider_images, prepare_assets
from acb.resolver import defaults

ORIGINAL = ('INFO cache entry available for fixture verification\n' * 150).rstrip('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--harnesses', nargs='+', default=['goose', 'pi', 'opencode', 'claude-code'])
    parser.add_argument('--combined', action='store_true', help='Validate RTK then Caveman context composition')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080':
        raise ValueError('requires the deterministic bridge fixture plan')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = output / 'tasks/bridge'
    shutil.copytree('tests/fixtures/harbor/bridge', task)
    (task / 'environment/events.log').write_text(ORIGINAL)
    with (task / 'environment/Dockerfile').open('a') as file:
        file.write('\nCOPY events.log /work/events.log\n')
    source = (task / 'environment/server.py').read_text()
    start = source.index('COMMANDS =')
    end = source.index('\n\nclass Handler', start)
    replacement = """COMMANDS = [
    "cat /work/events.log",
    "retrieval replaced dynamically",
    "cat /work/events.log; exit 7",
    "cat /work/events.log; printf 'ERROR critical diagnostic\\\\n'",
    "printf 'fixed\\\\n' > /work/answer.txt",
]


def command_for(body, index):
    if index == 1:
        import re
        match = re.search(r'acb-recall ([A-Za-z0-9:_.-]+)', json.dumps([m for m in body['messages'] if m.get('role') == 'tool']))
        if not match:
            raise RuntimeError('eligible real tool output was not compressed')
        return 'acb-recall ' + match[1] + ' > /work/recovered.log'
    return COMMANDS[index]
"""
    if args.combined:
        # Keep an eligible post-RTK payload and separately exercise a rewrite.
        replacement = replacement.replace('cat /work/events.log', 'dd if=/work/events.log status=none')
        replacement += '\nCOMMANDS.insert(4, "ls /work")\n'
    (task / 'environment/server.py').write_text(source[:start] + replacement + source[end:])
    verifier = task / 'tests/test.sh'
    verification = '\ncmp /work/events.log /work/recovered.log\n' + ("""/opt/miniconda3/bin/python - <<'CHECK'
import socket
with socket.socket() as s:
    s.settimeout(2)
    assert s.connect_ex(('127.0.0.1', 18881)) != 0
CHECK
""")
    verifier.write_text(verifier.read_text().replace('if [ "$(cat /work/answer.txt)"', verification + '\nif [ "$(cat /work/answer.txt)"'))
    plan.pop('task_plans', None)
    plan['benchmark_config']['path'] = str(task.parent)
    plan['benchmark_config']['praxis_image'] = 'acb-praxis-ai:latest'
    plan['subset'] = ['bridge']
    plan['attempts'] = 1
    plan['harnesses'] = {name: {
        **defaults()['harnesses'][name], 'timeout': 120,
        'model_middleware': [{'name': 'caveman', 'version': REVISION, 'mode': 'compress'}],
    } for name in args.harnesses}
    if args.combined:
        for name, settings in plan['harnesses'].items():
            settings['execution_integrations'] = [{'name': 'rtk', 'version': '0.48.0',
                'mode': 'shell-wrapper' if name == 'goose' else 'native', 'experimental': True}]
    plan['manifest'] = prepare_dataset(plan)
    requested = output / 'prepared.json'

    def worker(action, destination):
        requested.write_text(json.dumps(plan, indent=2))
        with (output / (action + '.log')).open('w') as log:
            subprocess.run([sys.executable, '-m', 'acb.harbor.worker', action,
                            str(requested), str(destination)], stdout=log,
                           stderr=subprocess.STDOUT, check=True)

    worker('inspect', output / 'inspection')
    runtime = json.loads(next((output / 'inspection/harbor').glob('*/agent/acb/runtime.json')).read_text())
    plan['runtime_contracts'] = {'bridge': {k: v for k, v in runtime.items()
        if k not in ('task_id', 'harness_version', 'launch_profile')}}
    plan['verifier_contracts'] = json.loads((output / 'inspection/verifier-contracts.json').read_text())
    plan = freeze_provider_images(prepare_assets(plan))
    worker('run', output / 'measured')
    checks = {}
    for name in args.harnesses:
        report = json.loads((output / 'measured' / name / 'report.json').read_text())
        assert report['resolved'] == 1 and report['incomplete_measurements'] == 0, report
        trial = output / 'measured/harbor' / report['evaluations'][0]['trial_name']
        manifest = json.loads((trial / 'agent/acb/integrations/caveman/manifest.json').read_text())
        assert manifest['verification']['compression_verified'], manifest
        assert manifest['verification']['agent_tool_verified'], manifest
        events = [json.loads(line) for line in (trial / 'agent/acb/integrations/caveman/runtime/evidence.jsonl').read_text().splitlines()]
        assert any(e.get('original_sha256') == hashlib.sha256(ORIGINAL.encode()).hexdigest()
                   and e['status'] == 'compressed' for e in events)
        requests = [json.loads(line) for line in next(trial.rglob('fixture-requests.jsonl')).read_text().splitlines()]
        results = {m['tool_call_id']: m['content'] for r in requests for m in r.get('messages', []) if m.get('role') == 'tool'}
        assert 'acb-recall ' in results['fixture-call-0']
        assert ORIGINAL not in results['fixture-call-0']
        assert 'acb-recall ' not in results['fixture-call-2']
        assert ORIGINAL.strip() in results['fixture-call-2']
        assert ORIGINAL.strip() in results['fixture-call-3'] and 'ERROR critical diagnostic' in results['fixture-call-3']
        usage = [json.loads(line) for line in (output / 'measured' / name / 'usage.jsonl').read_text().splitlines()]
        assert len(usage) == len(requests) and len(usage) >= 6
        assert len({r['request_id'] for r in usage}) == len(usage)
        assert report['avg_total_tokens'] == len(usage) * 120
        check = {'passed': True, 'harness': name, 'reward': 1, 'compressed': True,
                 'exact_agent_recovery': True, 'failed_and_critical_output_preserved': True,
                 'requests': len(usage), 'fixture_tokens': len(usage) * 120, 'measurement_complete': True}
        if args.combined:
            rtk = json.loads((trial / 'agent/acb/integrations/rtk/manifest.json').read_text())
            activity = rtk['metadata']['activity']
            assert activity['agent_tool_verified'] and activity['commands_rewritten'] >= 1 and activity['errors'] == 0, rtk
            check['rtk_activity'] = activity
            check['composition'] = 'RTK execution then Caveman context; recovery returns post-RTK bytes'
        checks[name] = check
    (output / 'check.json').write_text(json.dumps({'passed': True, 'harnesses': checks}, indent=2))
    print(json.dumps({'artifacts': str(output), 'harnesses': checks}, indent=2))



if __name__ == '__main__':
    main()
