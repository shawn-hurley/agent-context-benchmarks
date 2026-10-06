import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest
import yaml

from acb.harbor.benchmark_verifier import BenchmarkVerifier, strip_workflow_artifacts
from acb.harbor.dataset import prepare_dataset, verify_manifest
from acb.harbor.skills import task_skills, with_task_skills
from acb.workflows import load_workflow
from test_harbor_benchmark_tasks import scarf_plan


@pytest.mark.parametrize('name', ['kantra-rgctl', 'migiq'])
def test_migration_workflow_export_and_skill_delivery(tmp_path, name):
    from harbor.models.task.task import Task
    plan = scarf_plan(tmp_path)
    workflow = load_workflow(name, tmp_path, 'scarfbench', ['goose'])
    plan['workflow'] = workflow
    manifest = prepare_dataset(plan)
    root = Path(manifest['tasks'][0]['path'])
    task = Task(root)
    assert task.config.environment.skills_dir == '/opt/acb/skills'
    assert [step.name for step in task.config.steps] == ['plan', 'execute', 'verify']
    assert sum(step.agent.timeout_sec for step in task.config.steps) == 2700
    assert workflow['steps'][-1]['gate']['type'] == 'native'
    assert 'COPY skills/ /opt/acb/skills/' in (root / 'environment/Dockerfile').read_text()
    skills, records = task_skills(root / 'environment/skills')
    delivered = with_task_skills({'skills': []}, skills, 'goose')
    names = {item['name'] for item in delivered['skills']}
    assert 'rgctl' in names
    assert ('migiq' in names) == (name == 'migiq')
    assert ('kantra' in names) == (name == 'kantra-rgctl')
    assert len(records) == len(names)
    assert '/root/.agents/skills/rgctl/SKILL.md' in delivered['system_prompt']
    provenance = json.loads((root / 'environment/provenance.json').read_text())
    for relative, info in provenance['skill_files'].items():
        content = (root / 'environment/skills' / relative).read_bytes()
        assert hashlib.sha256(content).hexdigest() == info['packaged_sha256']
    assert hashlib.sha256((root / 'environment/Cargo.lock').read_bytes()).hexdigest() == provenance['rgctl']['cargo_lock']['sha256']
    # Every packaged Markdown file has usable local links after container delivery.
    for file in (root / 'environment/skills').rglob('*.md'):
        for target in re.findall(r'!?\[[^\]]*\]\(([^\s)]+)\)', file.read_text()):
            if target.startswith(('#', 'http:', 'https:', 'mailto:', 'data:')):
                continue
            assert (file.parent / target.split('#', 1)[0]).exists(), (file, target)
    verify_manifest(manifest)


@pytest.mark.parametrize('name', ['kantra-rgctl', 'migiq'])
def test_workflow_evidence_does_not_enter_submission(tmp_path, name):
    workflow = load_workflow(name, tmp_path, 'scarfbench', ['goose'])
    source = tmp_path / 'src/main/java/App.java'
    source.parent.mkdir(parents=True)
    source.write_text('application')
    for item in workflow['exclude_from_grading']:
        if '.' in Path(item).name and not item.startswith('.'):
            (tmp_path / item).write_text('evidence')
        else:
            (tmp_path / item).mkdir(exist_ok=True)
            (tmp_path / item / 'evidence').write_text('evidence')
    strip_workflow_artifacts(tmp_path, workflow['exclude_from_grading'])
    assert source.read_text() == 'application'
    assert all(not (tmp_path / item).exists() for item in workflow['exclude_from_grading'])


@pytest.mark.parametrize('reward', [False, True])
def test_native_gate_archives_final_report_without_replacing_grade(tmp_path, monkeypatch, reward):
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    import acb.harbor.benchmark_verifier as module
    plan = scarf_plan(tmp_path)
    plan['workflow'] = load_workflow('migiq', tmp_path, 'scarfbench', ['goose'])
    task = Task(Path(prepare_dataset(plan)['tasks'][0]['path']))
    work = tmp_path / 'work'
    for name in ('migiq-workspace', 'mig-plan-workspace', 'mig-execute-workspace'):
        (work / name).mkdir(parents=True)
    (work / 'migiq-workspace/MIGRATION_REPORT.md').write_text('Checks performed; native grading pending.')
    class Environment:
        async def download_dir(self, source, destination):
            shutil.copytree(work / source.removeprefix('/work/'), destination)
    paths = TrialPaths(tmp_path / 'trial')
    paths.verifier_dir.mkdir(parents=True)
    verifier = BenchmarkVerifier(task=task, trial_paths=paths, environment=Environment(),
                                 benchmark_config=plan['benchmark_config'], step_name='verify')
    async def native(record, output, workflow):
        assert (paths.verifier_dir / 'migiq-final/migiq-workspace/MIGRATION_REPORT.md').is_file()
        return [], {}
    async def grade(command, *, cwd, env, log):
        (cwd / 'grade.json').write_text(json.dumps({'resolved': reward}))
        return 0
    monkeypatch.setattr(verifier, '_scarfbench', native)
    monkeypatch.setattr(module, 'run_grader', grade)
    assert asyncio.run(verifier.verify()).rewards == {'reward': int(reward)}


@pytest.mark.parametrize('value', ['relative/skills', '/opt/../skills', 42])
def test_workflow_rejects_unsafe_skill_paths(tmp_path, value):
    root = tmp_path / 'workflow'
    root.mkdir()
    (root / 'finish.md').write_text('Finish migration')
    data = {'version': 1, 'name': 'fixture', 'environment': {'skills_dir': value},
            'steps': [{'name': 'finish', 'instruction': 'finish.md', 'timeout_sec': 1,
                       'gate': {'type': 'native'}}]}
    (root / 'workflow.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match='skills_dir'):
        load_workflow(str(root), tmp_path, 'scarfbench', ['goose'])


def test_import_records_benchmark_identity_independent_of_setup(tmp_path):
    from acb.harbor.results import import_results
    plan = scarf_plan(tmp_path)
    plan.update(harnesses={'goose': {'timeout': 900}}, run_id='fixture', model={'name': 'local'}, proxy='praxis')
    plan['workflow'] = load_workflow('kantra-rgctl', tmp_path, 'scarfbench', ['goose'])
    plan['manifest'] = prepare_dataset(plan)
    output = tmp_path / 'run'
    output.mkdir()
    import_results(plan, output, [])
    report = json.loads((output / 'goose/report.json').read_text())
    task = plan['manifest']['tasks'][0]
    contract = report['comparison_provenance']['tasks'][task['id']]['benchmark_contract']
    assert contract['inputs'] and contract['grading']
