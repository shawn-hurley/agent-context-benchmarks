"""Identify benchmark inputs and grading separately from experiment setup."""
from hashlib import sha256
import json
from pathlib import Path

from acb.tree_diff import GENERATED_DIRS

# Reference deployment scaffolding is experiment setup, not behavioral assertions.
DEPLOYMENT_FILES = frozenset({'Dockerfile', 'Containerfile', 'Makefile', 'metadata.json'})


def tree_digest(root: Path, *, deployment=False) -> str | None:
    if not root.is_dir():
        return None
    digest = sha256()
    files = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(part in GENERATED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            # Do not establish identity by silently omitting unknown input files.
            return None
        if path.is_file() and not (deployment and path.name in DEPLOYMENT_FILES):
            files.append(path)
    if not files:
        return None
    for path in sorted(files):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, 'big') + relative)
        digest.update(len(content).to_bytes(8, 'big') + content)
    return digest.hexdigest()


def document_digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def benchmark_contract(root: Path) -> dict | None:
    """Read only frozen benchmark artifacts; omit prompts, tools and image setup."""
    try:
        record = json.loads((root / 'tests/benchmark.json').read_text())
        benchmark = record['benchmark']
        if benchmark == 'scarfbench':
            source = tree_digest(root / 'environment/source')
            grading = tree_digest(root / 'tests/benchmark', deployment=True)
            extra = record['extra']
            conversion = {key: extra[key] for key in ('layer', 'app', 'source', 'target')}
            if source is None or grading is None:
                return None
            return {'version': 1, 'benchmark': benchmark,
                    'inputs': document_digest({'source': source, 'conversion': conversion}),
                    'grading': grading}
        if benchmark in ('swebench', 'swebench-lite'):
            row = record['extra']['dataset_row']
            if not row.get('repo') or not row.get('base_commit'):
                return None
            inputs = {key: row.get(key) for key in ('repo', 'base_commit', 'problem_statement')}
            grading = {key: row.get(key) for key in
                       ('test_patch', 'FAIL_TO_PASS', 'PASS_TO_PASS', 'eval_script', 'eval_type', 'log_parser')}
            assets = tree_digest(root / 'tests/task_repo')
            return {'version': 1, 'benchmark': benchmark,
                    'inputs': document_digest(inputs),
                    'grading': document_digest({'rules': grading, 'assets': assets})}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None
