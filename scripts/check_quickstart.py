"""Exercise the clone-to-run Quick Start and retain evidence in a new directory.

Controls contact no model. --live authorizes exactly one small model-driven trial.
--scarf adds native Cart controls against the published corrected benchmark fork.
Container-engine isolation is supplied by the caller's process environment.
"""
import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

import yaml

from acb.report_data import ReportSource, read_jsonl
from acb.telemetry import trajectory

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'config.example/quickstart'
SCARF_REPOSITORY = 'https://github.com/shawn-hurley/scarf-benchmark.git'
SCARF_REVISION = 'd0a6a3d3bd7c0e8a80bc6474737475a593ba4418'
SCARF_BRANCH = 'fix/realworld-shared-behavior'


class References(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.references = []
        self.ids = set()
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for name in ('href', 'src'):
            if attrs.get(name):
                self.references.append(attrs[name])


def verify_bundle(archive, destination):
    with ZipFile(archive) as bundle:
        bundle.extractall(destination)
    root = destination.resolve()
    documents = {p.resolve(): References(p.read_text()) for p in root.rglob('*.html')}
    if not (root / 'index.html').is_file() or not documents:
        raise ValueError('report bundle has no entry page')
    links = 0
    for path, document in documents.items():
        for reference in document.references:
            link = urlsplit(reference)
            if link.scheme or link.netloc:
                raise ValueError(f'report requires a remote link: {reference}')
            target = (path.parent / unquote(link.path)).resolve() if link.path else path
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError(f'missing or escaping report link: {reference}')
            if link.fragment and (target not in documents or unquote(link.fragment) not in documents[target].ids):
                raise ValueError(f'missing report anchor: {reference}')
            links += 1
    return {'html_pages': len(documents), 'local_links': links}


def verify_run(run, harness, reward, *, measured=False, patch=False):
    source = next((item for item in ReportSource(run).harnesses.values()
                   if item.report['harness'] == harness), None)
    if source is None:
        raise ValueError(f'missing {harness} report in {run}')
    records = source.report.get('evaluations', [])
    if len(records) != 1:
        raise ValueError(f'expected one trial, got {len(records)}')
    record = records[0]
    if (record.get('status') != 'completed' or record.get('exception') or
            (record.get('rewards') or {}).get('reward') != reward):
        raise ValueError(f'trial did not complete with reward {reward}; inspect {run}')
    result = {'run': str(run), 'harness': harness, 'reward': reward}
    if measured:
        requests = read_jsonl(source.directory / 'usage.jsonl')
        metrics = read_jsonl(source.directory / 'benchmark_metrics.jsonl')
        tokens = sum(sum(row.get(key, 0) or 0 for key in
                         ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
                     for row in requests)
        tools = trajectory(source.trial_directory(record['trial_id']) / 'transcript.jsonl', harness)['tool_calls'] or 0
        classified = sum(len(row.get('tools') or []) for row in metrics
                         if row.get('content_type') == 'tool_call')
        if (record.get('measurement_complete') is not True or not requests or tokens <= 0
                or tools <= 0 or classified <= 0):
            raise ValueError(f'missing complete model usage or tool calls; inspect {run}')
        result.update(model_requests=len(requests), tokens=tokens, tool_calls=tools,
                      classified_tool_calls=classified)
    if patch:
        changes = source.source_changes(record['trial_id'])
        if not changes or not changes['diff'].strip():
            raise ValueError(f'missing migration diff; inspect {run}')
        result['patch_bytes'] = len(changes['diff'].encode())
    return result


def setup_config(output, environment, model_config=None, model='quickstart-model'):
    target = output / 'config'
    shutil.copytree(TEMPLATE, target)
    if model_config:
        models = yaml.safe_load(Path(model_config).read_text())
        if not isinstance(models, dict) or model not in models:
            raise ValueError(f'model {model!r} missing from supplied model configuration')
        selected = dict(models[model])
        selected.setdefault('model', model)
        (target / 'models.yaml').write_text(yaml.safe_dump({'quickstart-model': selected}))
    (target / 'machine.yaml').write_text(yaml.safe_dump({
        'environment': environment, 'cache_dir': str(output / 'cache')}))
    for name in ('run.yaml', 'scarfbench-cart.yaml'):
        path = target / name
        config = yaml.safe_load(path.read_text())
        config['output_dir'] = str(output / 'runs')
        path.write_text(yaml.safe_dump(config))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory for configuration, logs and results')
    parser.add_argument('--environment', choices=('podman', 'docker'), default='podman')
    parser.add_argument('--model-config', type=Path, help='models.yaml; keys remain in environment variables')
    parser.add_argument('--model', default='quickstart-model', help='model entry in --model-config')
    parser.add_argument('--live', action='store_true', help='make one billed model-driven smoke trial')
    parser.add_argument('--scarf', action='store_true', help='download the pinned public fork and run Cart controls')
    parser.add_argument('--scarf-benchmark', type=Path,
                        help='use an explicit local benchmark tree instead; does not verify publication')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    check = {'passed': False, 'platform': platform.platform(), 'environment': args.environment,
             'live_requested': args.live, 'commands': [], 'runs': []}
    def command(argv, name, *, cwd=ROOT, env=None):
        started = time.monotonic()
        with (output / (name + '.log')).open('w') as log:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        check['commands'].append({'name': name, 'exit_code': result.returncode,
                                  'duration_sec': round(time.monotonic() - started, 2)})
        if result.returncode:
            raise RuntimeError(f'{name} failed; inspect {output / (name + ".log")}')
    def cli(arguments, name):
        command([sys.executable, '-m', 'acb.cli', *map(str, arguments)], name)
    def trial(config, name, control=None, patch=False):
        before = set((output / 'runs').glob('*'))
        cli(['run', '--config', config, *(['--control', control] if control else [])], name)
        added = [path for path in set((output / 'runs').glob('*')) - before if (path / 'report.json').is_file()]
        if len(added) != 1:
            raise ValueError(f'{name} did not produce one run')
        run = added[0]
        check['runs'].append(verify_run(run, control or 'goose', 0 if control == 'nop' else 1,
                                        measured=control is None, patch=patch))
        archive = output / (name + '.zip')
        cli(['report', run, '--html', '--bundle', archive], name + '-report')
        check['runs'][-1]['bundle'] = verify_bundle(archive, output / (name + '-extracted'))
    try:
        command(['git', 'rev-parse', 'HEAD'], 'acb-revision')
        check['acb_revision'] = (output / 'acb-revision.log').read_text().strip()
        command([args.environment, 'version'], 'engine-version')
        command([args.environment, 'compose', 'version'], 'compose-version')
        if args.environment == 'docker':
            command(['docker', 'buildx', 'version'], 'buildx-version')
            if os.environ.get('DOCKER_BUILDKIT') == '0':
                raise ValueError('Docker builds require BuildKit; set DOCKER_BUILDKIT=1')
        config = setup_config(output, args.environment, args.model_config, args.model)
        if args.live:
            model = yaml.safe_load((config / 'models.yaml').read_text())['quickstart-model']
            if model.get('model') == 'YOUR_MODEL_ID' or not model.get('model'):
                raise ValueError('--live requires a real model ID in --model-config')
            if model.get('key_env') and not os.environ.get(model['key_env']):
                raise ValueError('configured credential environment variable is missing')
        cli(['resolve', '--config', config / 'run.yaml'], 'resolve')
        cli(['prepare', '--config', config / 'run.yaml'], 'prepare')
        trial(config / 'run.yaml', 'oracle', 'oracle')
        trial(config / 'run.yaml', 'nop', 'nop')
        if args.live:
            trial(config / 'run.yaml', 'live')
        if args.scarf or args.scarf_benchmark:
            if args.scarf_benchmark:
                benchmark = args.scarf_benchmark.resolve(strict=True)
                check['benchmark_source'] = 'explicit local tree; public download not verified'
            else:
                checkout = output / 'scarf-benchmark'
                git_env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
                command(['git', '-c', 'credential.helper=', 'clone', '--single-branch', '--branch',
                         SCARF_BRANCH, SCARF_REPOSITORY, str(checkout)], 'benchmark-clone', env=git_env)
                command(['git', 'checkout', '--detach', SCARF_REVISION], 'benchmark-checkout', cwd=checkout)
                benchmark = checkout / 'benchmark'
                check['benchmark_source'] = SCARF_REPOSITORY
                check['benchmark_revision'] = SCARF_REVISION
            command(['scarf', '--version'], 'scarf-version')
            if (output / 'scarf-version.log').read_text().strip() != 'scarf 0.1.2':
                raise ValueError('this acceptance check requires tested scarf 0.1.2')
            registry = yaml.safe_load((config / 'benchmarks.yaml').read_text())
            registry['scarfbench'].update(benchmark_cache_dir=str(benchmark),
                                           maven_cache_volume='acb-quickstart-' + str(os.getpid()))
            (config / 'benchmarks.yaml').write_text(yaml.safe_dump(registry))
            cli(['resolve', '--config', config / 'scarfbench-cart.yaml'], 'scarf-resolve')
            trial(config / 'scarfbench-cart.yaml', 'scarf-oracle', 'oracle', patch=True)
            trial(config / 'scarfbench-cart.yaml', 'scarf-nop', 'nop')
        check['passed'] = True
    except Exception as error:
        check['error'] = str(error)
        raise
    finally:
        (output / 'check.json').write_text(json.dumps(check, indent=2))
    print(f'Quick Start check passed: {output / "check.json"}')


if __name__ == '__main__':
    main()
