"""Exercise a cached rgctl skill and binary through all four Harbor harnesses."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.preparation import freeze_provider_images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('skill_cache', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = args.skill_cache.resolve()
    binary_hash = hashlib.sha256((source / 'rgctl').read_bytes()).hexdigest()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080' or plan['subset'] != ['bridge']:
        raise ValueError('requires the deterministic bridge fixture')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / 'cached-skill'
    shutil.copytree(source, snapshot)
    task = output / 'tasks/bridge'
    shutil.copytree(Path('tests/fixtures/harbor/bridge'), task)
    (task / 'environment/skill_fixture.py').write_text('def acb_rgctl_canary(value):\n    return value + 1\n')
    with (task / 'environment/Dockerfile').open('a') as handle:
        handle.write('\nCOPY skill_fixture.py /work/skill_fixture.py\n')
    commands = [
        'rgctl --version && sha256sum /usr/local/bin/rgctl',
        'cd /work && rgctl discover .',
        'cd /work && rgctl -f json query find acb_rgctl_canary > /work/rgctl-query.json && cat /work/rgctl-query.json',
        "/opt/miniconda3/bin/python -c \"import json; from pathlib import Path; data=json.loads(Path('/work/rgctl-query.json').read_text()); assert isinstance(data, list) and data; assert 'acb_rgctl_canary' in json.dumps(data); Path('/work/answer.txt').write_text('fixed\\n')\"",
    ]
    server = task / 'environment/server.py'
    content = server.read_text()
    start, end = content.index('COMMANDS = ['), content.index('\n\ndef command_for')
    content = content[:start] + 'COMMANDS = ' + repr(commands) + content[end:]
    content = content.replace('def command_for(body, index):', '''def command_for(body, index):
    if index == 0:
        import re, shlex
        paths = sorted(set(re.findall(r"/root/[a-zA-Z0-9_./-]+/rgctl/SKILL.md", json.dumps(body))))
        if len(paths) != 1:
            return "printf 'MISSING_RGCTL_HINT'; exit 1"
        return 'cat ' + shlex.quote(paths[0]) + ' && ' + COMMANDS[0]
''')
    server.write_text(content)
    with (task / 'tests/test.sh').open('a') as handle:
        handle.write('\ncp /work/rgctl-query.json /logs/verifier/rgctl-query.json\n')
    plan.pop('task_plans', None)
    plan['benchmark_config']['path'] = str(task.parent)
    plan['benchmark_config'].setdefault('praxis_image', 'acb-praxis-ai:latest')
    plan['attempts'] = 1
    for settings in plan['harnesses'].values():
        settings['skills'] = [{'name': 'rgctl', 'source_type': 'local',
            'source_path': str(snapshot), 'binary_name': 'rgctl',
            'binary_install_path': '/usr/local/bin/rgctl', 'required': True}]
    plan['manifest'] = prepare_dataset(plan)
    plan = freeze_provider_images(plan)
    requested = output / 'prepared.json'
    requested.write_text(json.dumps(plan, indent=2))
    with (output / 'run.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'acb.harbor.worker', 'run', str(requested),
            str(output / 'measured')], stdout=log, stderr=subprocess.STDOUT, check=True)
    observed = {}
    for name in plan['harnesses']:
        report = json.loads((output / 'measured' / name / 'report.json').read_text())
        assert report['resolved'] == 1 and report['incomplete_measurements'] == 0, name
        trial = output / 'measured/harbor' / report['evaluations'][0]['trial_name']
        ledger = next(trial.rglob('fixture-requests.jsonl')).read_text()
        assert binary_hash in ledger, name
        query = json.loads((trial / 'verifier/rgctl-query.json').read_text())
        assert isinstance(query, list) and query and 'acb_rgctl_canary' in json.dumps(query), name
        observed[name] = {'reward': 1, 'installed_binary_hash_verified': True, 'query': query}
    (output / 'rgctl-check.json').write_text(json.dumps({
        'passed': True, 'source_cache': str(source), 'binary_sha256': binary_hash,
        'harnesses': observed}, indent=2))
    print(f'Cached rgctl check passed: {output}')


if __name__ == '__main__':
    main()
