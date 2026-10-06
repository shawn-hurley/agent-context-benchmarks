"""Persistent ScarfBench downloads; locally installed artifacts stay in each image/trial."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

DEFAULT_VOLUME = 'acb-scarfbench-maven-v1'
TARGET = '/root/.m2/repository/cached'
MARKER = '# ACB Maven dependency cache v1'
# Locks must live on the shared filesystem, not in each container's private root.
OPTIONS = ('-Daether.enhancedLocalRepository.split=true '
           '-Daether.enhancedLocalRepository.remotePrefix=cached '
           '-Daether.enhancedLocalRepository.localPrefix=installed '
           '-Daether.syncContext.named.factory=file-lock '
           '-Daether.syncContext.named.nameMapper=file-gav '
           '-Daether.syncContext.named.basedir.locksDir=' + TARGET + '/.locks')


def cache_volume(config):
    enabled = config.get('maven_cache', True)
    if not isinstance(enabled, bool):
        raise ValueError('scarfbench.maven_cache must be a boolean')
    if not enabled:
        return None
    name = config.get('maven_cache_volume', DEFAULT_VOLUME)
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', name):
        raise ValueError('scarfbench.maven_cache_volume must be a container volume name')
    return name


def cached_recipe(recipe, config):
    """Add persistent BuildKit/Buildah cache mounts to a copied container recipe."""
    volume = cache_volume(config)
    if volume is None or MARKER in recipe:
        return recipe
    lines = [MARKER + '\n']
    for line in recipe.splitlines(keepends=True):
        if re.match(r'^\s*RUN\s', line, re.I):
            line = re.sub(r'^(\s*RUN\s+)', lambda m: m[1] +
                          f'--mount=type=cache,id={volume},target={TARGET},sharing=locked ',
                          line, count=1, flags=re.I)
        lines.append(line)
        if re.match(r'^\s*FROM\s', line, re.I):
            lines.append('ENV MAVEN_OPTS="${MAVEN_OPTS} ' + OPTIONS + '"\n')
    return ''.join(lines)


def compose_cache(overlay, config):
    """External named volumes survive Harbor's compose down --volumes cleanup."""
    volume = cache_volume(config)
    if volume is None:
        return
    overlay.setdefault('volumes', {})['acb-maven-downloads'] = {'external': True, 'name': volume}
    main = overlay.setdefault('services', {}).setdefault('main', {})
    main.setdefault('volumes', []).append({'type': 'volume', 'source': 'acb-maven-downloads',
                                         'target': TARGET})


def ensure_volume(runtime, config):
    volume = cache_volume(config)
    if volume:
        command = [runtime, 'volume', 'create']
        # Docker reuses named volumes; Podman requires an explicit opt-in.
        if runtime == 'podman':
            command.append('--ignore')
        result = subprocess.run([*command, volume], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f'cannot create Maven cache volume {volume}: '
                               f'{result.stderr.strip() or result.stdout.strip()}')


def validation_env(config, env, directory):
    """Route native validator container runs through the same persistent download volume."""
    volume = cache_volume(config)
    if volume is None:
        return env
    backend = config.get('container_backend', 'auto')
    if backend == 'auto':
        backend = 'podman' if 'podman' in env.get('DOCKER_HOST', '') or not shutil.which('docker') else 'docker'
    directory = Path(directory) / 'maven-cache-bin'
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / 'docker'
    script.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, str(Path(__file__).resolve()),
                                                     backend, volume]) + ' "$@"\n')
    script.chmod(0o755)
    return {**env, 'PATH': str(directory) + os.pathsep + env.get('PATH', os.environ.get('PATH', '')),
            'DOCKER_BUILDKIT': '1'}


def container_arguments(args, volume):
    if args and args[0] in {'run', 'create'}:
        return [args[0], '--volume', volume + ':' + TARGET, *args[1:]]
    return args


def main():
    backend, volume, *arguments = sys.argv[1:]
    command = [backend, *container_arguments(arguments, volume)]
    os.execvp(backend, command)


if __name__ == '__main__':
    main()
