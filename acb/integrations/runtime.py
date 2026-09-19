"""Inspect prerequisites in retained benchmark containers before activation."""
from copy import deepcopy
import json
from pathlib import Path
import shlex

from acb.container import container_exec_capture


def prepare_legacy_rtk_runtime(container, harness: str, config: dict,
                               arch: str, directory: Path) -> dict:
    config = deepcopy(config)
    extensions = [item for item in config.get('execution_integrations', []) if item['name'] == 'rtk']
    if not extensions:
        return config
    evidence = {'harness': harness, 'expected_architecture': arch, 'passed': False}
    try:
        machine = container_exec_capture(container, ['uname', '-m']).strip()
        actual = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine)
        evidence['actual_architecture'] = actual
        if actual != arch:
            raise ValueError(f'RTK task architecture {machine!r} differs from prepared {arch}')
        if harness == 'claude-code':
            extension = extensions[0]
            requested = extension.get('python_path')
            candidates = [requested] if requested else [
                'python3', 'python', '/usr/bin/python3', '/opt/conda/bin/python',
                '/opt/miniconda3/bin/python',
            ]
            probe = ('import sys,json,selectors,subprocess,hashlib; '
                     'assert sys.version_info >= (3,8); '
                     "print(json.dumps({'python_path':sys.executable,'version':list(sys.version_info[:3])}))")
            command = ('for candidate in ' + ' '.join(shlex.quote(x) for x in candidates) + '; do '
                       'p=$(command -v "$candidate" 2>/dev/null) || continue; '
                       '"$p" -c ' + shlex.quote(probe) + ' 2>/dev/null && exit 0; done; exit 1')
            try:
                record = json.loads(container_exec_capture(container, ['bash', '-c', command]))
                if not isinstance(record.get('python_path'), str) or not record['python_path'].startswith('/'):
                    raise ValueError('probe did not return an absolute interpreter path')
            except Exception as error:
                raise ValueError('Claude RTK hooks require working Python >=3.8 in the task image; '
                                 'an explicit python_path must work without fallback') from error
            extension['python_path'] = record['python_path']
            evidence['interpreter'] = record
        evidence.update(passed=True, execution_integrations=config['execution_integrations'])
        return config
    except Exception as error:
        evidence['error'] = str(error)
        raise
    finally:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'rtk-runtime.json').write_text(json.dumps(evidence, indent=2))


def prepare_legacy_caveman_runtime(container, config: dict, directory: Path) -> dict:
    """Resolve the recovery interpreter inside this task, preserving failure evidence."""
    config = deepcopy(config)
    for middleware in config.get('model_middleware', []):
        if middleware['name'] != 'caveman':
            continue
        evidence = {'passed': False}
        try:
            requested = middleware.get('python_path')
            candidates = [requested] if requested else [
                'python3', 'python', '/usr/bin/python3', '/opt/conda/bin/python',
                '/opt/miniconda3/bin/python',
            ]
            probe = ('import sys,json,urllib.request; assert sys.version_info >= (3,8); '
                     "print(json.dumps({'python_path':sys.executable}))")
            command = ('for candidate in ' + ' '.join(shlex.quote(x) for x in candidates) + '; do '
                       'p=$(command -v "$candidate" 2>/dev/null) || continue; '
                       '"$p" -c ' + shlex.quote(probe) + ' 2>/dev/null && exit 0; done; exit 1')
            record = json.loads(container_exec_capture(container, ['bash', '-c', command]))
            python = record['python_path']
            if not isinstance(python, str) or not python.startswith('/') or any(c.isspace() for c in python):
                raise ValueError('probe did not return an absolute interpreter path')
            middleware['python_path'] = python
            evidence.update(passed=True, python_path=python)
        except Exception as error:
            evidence['error'] = str(error)
            raise ValueError('Caveman recovery requires working Python >=3.8 in the task image') from error
        finally:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'caveman-runtime.json').write_text(json.dumps(evidence, indent=2))
    return config
