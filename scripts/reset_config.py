"""Install the feature-test config matrix while archiving existing local config."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import platform
import tempfile

import yaml

from acb.config import RunConfig
from acb.resolver import resolve

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / 'config.example/feature-tests'


def read(path):
    return yaml.safe_load(path.read_text()) if path.is_file() else {}


def write(path, value):
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def reset_config(destination, *, template=TEMPLATE, backup_dir=None):
    destination = Path(destination).absolute()
    if destination.is_symlink():
        raise ValueError('config destination must not be a symlink')
    template = Path(template).resolve()
    backup_dir = Path(backup_dir or REPO / '.config/backups').absolute()
    if backup_dir.is_relative_to(destination) or template.is_relative_to(destination):
        raise ValueError('template and backup directory must be outside the config being reset')
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix='.acb-config-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'config'
        shutil.copytree(template, staged)
        guide = staged / 'README.md'
        if guide.is_file():
            text = guide.read_text().replace('../../scripts/', '../scripts/').replace('../../docs/', '../docs/')
            guide.write_text(text)
        # Keep provider choices, not old harness prompts or implicit treatments.
        models = read(staged / 'models.yaml')
        models.update((read(destination / 'proxy.yaml') or {}).get('models', {}))
        models.update(read(destination / 'phase6/models.yaml') or {})
        models.update(read(destination / 'models.yaml') or {})
        write(staged / 'models.yaml', models)
        (staged / 'models.yaml').chmod(0o600)
        machine = {'environment': 'podman', 'cache_dir': str(REPO / 'runs/.cache')}
        previous_machine = read(destination / 'machine.yaml') or read(destination / 'phase6/machine.yaml') or {}
        machine['environment'] = previous_machine.get('environment', 'podman')
        write(staged / 'machine.yaml', machine)
        if (destination / 'costs.yaml').is_file():
            shutil.copy2(destination / 'costs.yaml', staged / 'costs.yaml')
        # Reuse existing local prerequisite assets without baking machine paths
        # into the tracked template. The archive retains original registries.
        benchmarks = read(staged / 'benchmarks.yaml')
        old_benchmarks = read(destination / 'benchmarks.yaml') or {}
        scarf = old_benchmarks.get('scarfbench', {}).get('benchmark_cache_dir')
        if scarf:
            candidates = [Path(scarf).expanduser(), destination / scarf]
            existing = next((p.resolve() for p in candidates if p.is_dir()), None)
            if existing and not existing.is_relative_to(destination):
                benchmarks['scarfbench']['benchmark_cache_dir'] = str(existing)
        # The approved pilot bundle lives outside config; use its actual path.
        approved = REPO / 'runs/phase6-rh-storage-copy/tasks'
        if approved.is_dir():
            target = staged / 'assets/benchmarks/rh-swe-bench'
            shutil.copytree(approved, target / 'tasks')
            deviation = approved.parent / 'deviation.json'
            if deviation.is_file():
                shutil.copy2(deviation, target / 'deviation.json')
            benchmarks['rh-swe-bench']['path'] = 'assets/benchmarks/rh-swe-bench/tasks'
        write(staged / 'benchmarks.yaml', benchmarks)
        rgctl = REPO / 'runs/.cache/skills/rgctl-v0.4.11-aarch64/rgctl'
        if rgctl.is_file() and platform.machine() in ('arm64', 'aarch64'):
            shutil.copy2(rgctl, staged / 'assets/skills/rgctl/rgctl')
        for path in sorted((staged / 'runs').glob('*.yaml')):
            resolve(RunConfig.from_file(path))
        if destination.exists():
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = backup_dir / (destination.name + '-' + stamp)
            destination.rename(backup)
        try:
            staged.rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=REPO / 'config')
    args = parser.parse_args()
    backup = reset_config(args.destination)
    print(f'Feature configurations installed in {args.destination}')
    if backup:
        print(f'Previous configuration and saved results archived at {backup}')


if __name__ == '__main__':
    main()
