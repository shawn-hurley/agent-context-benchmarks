"""Prove recovery-store isolation between two live Harbor environments."""
import argparse
import asyncio
import json
from pathlib import Path
import shlex
import uuid

from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths
from acb.harbor.environment import ACBPodmanEnvironment, ACBDockerEnvironment


async def check(image, output, engine):
    environments = []
    evidence = {'passed': False, 'sessions': [], 'cleanup_errors': []}
    try:
        for _ in range(2):
            name = 'acb-store-' + uuid.uuid4().hex[:10]
            trial = output / name
            environment_dir = trial / 'environment'
            environment_dir.mkdir(parents=True)
            cls = ACBPodmanEnvironment if engine == 'podman' else ACBDockerEnvironment
            environment = cls(environment_dir=environment_dir, environment_name='store-isolation',
                              session_id=name + '__env', trial_paths=TrialPaths(trial),
                              task_env_config=EnvironmentConfig(docker_image=image))
            environments.append(environment)
            evidence['sessions'].append(name + '__env')
            await environment.start(force_build=False)

        async def capture(environment, argv):
            result = await environment.exec(shlex.join(argv), timeout_sec=30)
            if result.return_code:
                raise RuntimeError(f'fixture command failed: {result.stderr}')
            return result.stdout or ''

        original = ('INFO private trial ' + uuid.uuid4().hex + '\n') * 1000
        script = ("import subprocess,json; r=subprocess.run(['caveman-engine','compress','--type','log'],"
                  f"input={original!r}.encode(),capture_output=True,check=True); "
                  "print(json.loads(r.stderr)['recovery_handle'])")
        handle = (await capture(environments[0], ['python3', '-c', script])).strip()
        recovered = await capture(environments[0], ['caveman-engine', 'retrieve', handle])
        assert recovered == original
        rejected = ("import subprocess; r=subprocess.run(['caveman-engine','retrieve'," + repr(handle) +
                    "],capture_output=True); assert r.returncode != 0")
        await capture(environments[1], ['python3', '-c', rejected])
        evidence.update(passed=True, own_store_retrieval='exact bytes', other_store_retrieval='rejected')
    finally:
        for environment in reversed(environments):
            try:
                await environment.stop(delete=True)
            except Exception as error:
                evidence['cleanup_errors'].append(str(error))
        if evidence['cleanup_errors']:
            evidence['passed'] = False
        (output / 'check.json').write_text(json.dumps(evidence, indent=2))
    if not evidence['passed']:
        raise RuntimeError('store isolation or cleanup failed; see check.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    parser.add_argument('output', type=Path)
    parser.add_argument('--environment', choices=('podman', 'docker'), default='podman')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    asyncio.run(check(args.image, output, args.environment))


if __name__ == '__main__':
    main()
