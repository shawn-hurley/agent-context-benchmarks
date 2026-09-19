"""Exercise task and configured skill delivery with all four real harnesses."""
import argparse
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
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080' or plan['subset'] != ['bridge']:
        raise ValueError('requires the deterministic bridge fixture')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = output / 'tasks/bridge'
    shutil.copytree(Path('tests/fixtures/harbor/bridge'), task)
    for name, root in [('task-helper', task / 'environment/skills'),
                       ('configured-helper', output / 'configured-skills')]:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Skill delivery fixture\n---\nRead reference.txt beside this file.\n')
        (directory / 'reference.txt').write_text(f'ACB_SKILL_READ_{name}\n')
    with (task / 'environment/Dockerfile').open('a') as handle:
        handle.write('\nCOPY skills /fixture-skills\n')
    config = task / 'task.toml'
    config.write_text(config.read_text().replace('[environment]', '[environment]\nskills_dir = "/fixture-skills"'))
    server = task / 'environment/server.py'
    server.write_text(server.read_text().replace('def command_for(body, index):', '''def command_for(body, index):
    if index == 0:
        import re, shlex
        paths = sorted(set(re.findall(r"/root/[a-zA-Z0-9_./-]+/(?:task-helper|configured-helper)/SKILL.md", json.dumps(body))))
        if len(paths) != 2:
            return "printf 'MISSING_SKILL_HINTS\\\\n'; exit 1"
        files = [item for path in paths for item in (path, str(Path(path).parent / 'reference.txt'))]
        return 'cat ' + ' '.join(shlex.quote(path) for path in files) + "; printf 'fixed\\\\n' > /work/answer.txt"
'''))
    plan.pop('task_plans', None)
    plan['benchmark_config']['path'] = str(task.parent)
    plan['benchmark_config'].setdefault('praxis_image', 'acb-praxis-ai:latest')
    plan['attempts'] = 1
    for settings in plan['harnesses'].values():
        settings['skills'] = [{'name': 'configured-helper', 'source_type': 'local',
            'source_path': str(output / 'configured-skills/configured-helper'), 'required': True}]
    plan['manifest'] = prepare_dataset(plan)
    plan = freeze_provider_images(plan)
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
    assert runtime['task_skills'][0]['name'] == 'task-helper'
    plan['task_plans'] = {'bridge': {'runtime': runtime, 'harnesses': plan['harnesses']}}
    worker('run', output / 'measured')
    observed = {}
    for name in plan['harnesses']:
        report = json.loads((output / 'measured' / name / 'report.json').read_text())
        assert report['resolved'] == 1 and report['incomplete_measurements'] == 0, name
        trial = output / 'measured/harbor' / report['evaluations'][0]['trial_name']
        delivery = json.loads((trial / 'agent/acb/skill-delivery.json').read_text())
        assert delivery['task_skills'] == runtime['task_skills']
        assert delivery['configured_skills'] == ['configured-helper']
        ledger = next(trial.rglob('fixture-requests.jsonl')).read_text()
        for skill in ['task-helper', 'configured-helper']:
            assert f'ACB_SKILL_READ_{skill}' in ledger, (name, skill)
        observed[name] = {'reward': 1, 'both_skill_support_files_returned_to_model': True}
    (output / 'skill-check.json').write_text(json.dumps({'passed': True, 'harnesses': observed}, indent=2))
    print(f'Skill delivery check passed: {output}')


if __name__ == '__main__':
    main()
