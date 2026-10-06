"""Model-free runtime/build cache checks against a local Maven fixture repository."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from acb.maven_cache import OPTIONS, TARGET, cached_recipe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--engine', choices=['podman', 'docker'], default='podman')
    parser.add_argument('--image', default='docker.io/library/maven:3.9.12-ibm-semeru-21-noble')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    unique = 'acb-maven-check-' + uuid4().hex[:12]
    containers, images = [], []
    volume = unique + '-runtime'

    def call(*command, check=True):
        result = subprocess.run([args.engine, *command], capture_output=True, text=True, timeout=180)
        with (output / 'commands.log').open('a') as log:
            log.write(' '.join(command) + '\n' + result.stdout + result.stderr + '\n')
        if check and result.returncode:
            raise RuntimeError('container command failed; see commands.log: ' + ' '.join(command))
        return result

    fixture = output / 'fixture'
    repository = fixture / 'repository/acb/cache-parent/1'
    repository.mkdir(parents=True)
    (repository / 'cache-parent-1.pom').write_text(
        '<project><modelVersion>4.0.0</modelVersion><groupId>acb</groupId>'
        '<artifactId>cache-parent</artifactId><version>1</version><packaging>pom</packaging></project>')
    pom = ('<project><modelVersion>4.0.0</modelVersion><parent><groupId>acb</groupId>'
           '<artifactId>cache-parent</artifactId><version>1</version><relativePath/></parent>'
           '<artifactId>cache-child</artifactId><packaging>pom</packaging><repositories>'
           '<repository><id>fixture</id><url>file:///tmp/fixture/repository</url></repository>'
           '</repositories></project>')
    (fixture / 'pom.xml').write_text(pom)
    checks = {}
    try:
        call('volume', 'create', volume)
        for suffix in ('first', 'concurrent', 'warm'):
            name = unique + '-' + suffix
            containers.append(name)
            call('run', '-d', '--name', name, '--volume', volume + ':' + TARGET,
                 '-e', 'MAVEN_OPTS=' + OPTIONS, args.image, 'sleep', 'infinity')
            call('cp', str(fixture), name + ':/tmp/fixture')
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(call, 'exec', name, 'bash', '-lc',
                                   'cd /tmp/fixture && mvn -B -ntp validate') for name in containers[:2]]
            for result in pending:
                result.result()
        checks['concurrent_cold_downloads'] = True
        call('exec', containers[0], 'bash', '-lc',
             'mkdir -p /root/.m2/repository/installed/acb/private/1 && '
             'echo private > /root/.m2/repository/installed/acb/private/1/private-1.pom')
        call('rm', '-f', containers[0])
        call('exec', containers[2], 'bash', '-lc',
             'set -eu; cd /tmp/fixture; rm -rf repository; mvn -B -ntp -o validate; '
             'test ! -e /root/.m2/repository/installed/acb/private/1/private-1.pom')
        checks['runtime_reuse_after_container_removal'] = True
        checks['private_artifact_isolation'] = True
        config = {'maven_cache_volume': unique + '-build'}
        (fixture / 'Dockerfile').write_text(cached_recipe(
            'FROM ' + args.image + '\nCOPY . /tmp/fixture\nWORKDIR /tmp/fixture\n'
            'RUN mvn -B -ntp validate\n', config))
        cold_image = unique + ':cold'
        images.append(cold_image)
        call('build', '-t', cold_image, str(fixture))
        warm = output / 'warm-build'
        warm.mkdir()
        (warm / 'pom.xml').write_text(pom)
        (warm / 'Dockerfile').write_text(cached_recipe(
            'FROM ' + args.image + '\nCOPY pom.xml /tmp/fixture/pom.xml\nWORKDIR /tmp/fixture\n'
            'RUN mvn -B -ntp -o validate\n', config))
        warm_image = unique + ':warm'
        images.append(warm_image)
        call('build', '-t', warm_image, str(warm))
        checks['separate_build_offline_reuse'] = True
    finally:
        for name in containers:
            call('rm', '-f', name, check=False)
        for image in images:
            call('image', 'rm', image, check=False)
        call('volume', 'rm', volume, check=False)
        (output / 'check.json').write_text(json.dumps(checks, indent=2))
    print(json.dumps(checks))


if __name__ == '__main__':
    main()
