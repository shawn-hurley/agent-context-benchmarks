from copy import deepcopy

import pytest

from acb.harbor.skills import task_skills, with_task_skills


def snapshot(tmp_path):
    directory = tmp_path / 'task-helper'
    directory.mkdir()
    (directory / 'SKILL.md').write_text('---\nname: task-helper\ndescription: Test task helper\n---\nRead reference.txt.\n')
    (directory / 'reference.txt').write_text('canary')
    return directory


def test_task_skill_inventory_detects_support_file_changes(tmp_path):
    directory = snapshot(tmp_path)
    configs, before = task_skills(tmp_path)
    assert configs[0]['source_path'] == str(directory)
    assert configs[0]['required'] is True
    (directory / 'reference.txt').write_text('changed')
    _, after = task_skills(tmp_path)
    assert before != after
    assert before[0]['files']['SKILL.md'] == after[0]['files']['SKILL.md']


@pytest.mark.parametrize('harness', ['goose', 'pi', 'opencode', 'claude-code'])
def test_task_and_configured_skills_get_explicit_paths(tmp_path, harness):
    snapshot(tmp_path)
    native, _ = task_skills(tmp_path)
    config = {'system_prompt': 'Original prompt', 'skills': [{'name': 'configured-helper'}]}
    before = deepcopy(config)
    result = with_task_skills(config, native, harness)
    assert config == before
    assert len(result['skills']) == 2
    assert result['system_prompt'].startswith('Original prompt\n\n')
    for name in ['task-helper', 'configured-helper']:
        assert f'/{name}/SKILL.md' in result['system_prompt']


def test_task_skill_collision_rejected(tmp_path):
    snapshot(tmp_path)
    native, _ = task_skills(tmp_path)
    with pytest.raises(ValueError, match='collide'):
        with_task_skills({'skills': [{'name': 'task-helper'}]}, native, 'pi')


@pytest.mark.parametrize('kind', ['symlink', 'missing', 'name'])
def test_invalid_task_skill_rejected(tmp_path, kind):
    directory = snapshot(tmp_path)
    if kind == 'symlink':
        (directory / 'escape').symlink_to('/etc/hosts')
    elif kind == 'missing':
        (directory / 'SKILL.md').unlink()
    else:
        (directory / 'SKILL.md').write_text('---\nname: wrong-name\ndescription: Test\n---\n')
    with pytest.raises(ValueError):
        task_skills(tmp_path)
