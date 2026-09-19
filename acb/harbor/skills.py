"""Task-provided skills delivered through Harbor's environment transport."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

import yaml

from acb.skills.installer import SkillInstaller
from acb.skills.validator import validate_skill_md


def task_skills(snapshot: Path):
    """Validate a downloaded skills root and inventory every supporting file."""
    configs, records = [], []
    for directory in sorted(snapshot.iterdir()):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f'task skills root must contain skill directories: {directory.name}')
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', directory.name):
            raise ValueError(f'invalid task skill directory name: {directory.name}')
        files = {}
        for path in sorted(directory.rglob('*')):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError(f'task skill contains unsupported file: {path}')
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        error = validate_skill_md(directory / 'SKILL.md')
        if error:
            raise ValueError(f'{directory.name}: {error}')
        metadata = yaml.safe_load((directory / 'SKILL.md').read_text().split('---', 2)[1])
        if metadata['name'] != directory.name:
            raise ValueError(f'task skill name must match its directory: {directory.name}')
        configs.append({'name': directory.name, 'description': metadata['description'],
                        'source_type': 'local', 'source_path': str(directory), 'required': True})
        records.append({'name': directory.name, 'files': files})
    return configs, records


def with_task_skills(config, native, harness):
    result = deepcopy(config)
    skills = result.setdefault('skills', [])
    names = [item['name'] for item in skills + native]
    if len(names) != len(set(names)):
        raise ValueError('task/configured skill names collide')
    skills.extend(native)
    base = SkillInstaller.HARNESS_SKILL_PATHS[harness].replace('~/', '/root/')
    if skills:
        hints = '\n'.join(f"- {item['name']}: {base}/{item['name']}/SKILL.md" for item in skills)
        result['system_prompt'] = '\n\n'.join(filter(None, [result.get('system_prompt'),
            'Available skills (read SKILL.md for instructions and supporting files):\n' + hints]))
    return result
