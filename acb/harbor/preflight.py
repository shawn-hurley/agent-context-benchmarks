"""Check installed harness startup in the actual task environment before inference."""
import json
from pathlib import Path
import re
import shlex


class HarnessStartupError(RuntimeError):
    """The pinned harness cannot start in this task environment."""


async def verify_harness_startup(environment, harness: str, version: str, directory: Path):
    executable = '/usr/local/bin/' + {'claude-code': 'claude'}.get(harness, harness)
    try:
        result = await environment.exec(
            f'ulimit -c 0; {shlex.quote(executable)} --version', timeout_sec=30)
    except Exception as error:
        (directory / 'harness-startup.json').write_text(json.dumps({
            'harness': harness, 'expected_version': version, 'executable': executable,
            'passed': False, 'error': str(error), 'error_type': type(error).__name__,
        }, indent=2))
        raise HarnessStartupError(f'{harness} startup probe failed; see harness-startup.json') from error
    output = (result.stdout or '') + (result.stderr or '')
    matches = re.search(r'(?<![\w.])' + re.escape(version) + r'(?![\w.])', output)
    passed = result.return_code == 0 and matches is not None
    (directory / 'harness-startup.json').write_text(json.dumps({
        'harness': harness, 'expected_version': version, 'executable': executable,
        'exit_code': result.return_code, 'output': output, 'passed': passed,
        'scope': 'binary startup/version only; does not prove model protocol compatibility',
    }, indent=2))
    if not passed:
        raise HarnessStartupError(
            f'{harness} {version} failed startup/version check in the task image '
            f'(exit {result.return_code}). See harness-startup.json. '
            'Verify the image architecture and runtime; emulated binaries may be incompatible.')
