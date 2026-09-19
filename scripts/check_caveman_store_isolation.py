"""Prove independent recovery databases for two simultaneous legacy task pods."""
import argparse
import json
from pathlib import Path
import uuid

from acb.container import pod_create, pod_remove
from acb.integrations.legacy import LegacyIntegrationTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    pods, services = [], []
    try:
        for _ in range(2):
            pod = 'acb-store-' + uuid.uuid4().hex[:10]
            pods.append(pod)
            pod_create(pod)
            service = LegacyIntegrationTransport('unused-task', pod, args.image)
            services.append(service)
            service.start()
        original = 'INFO private trial ' + uuid.uuid4().hex + '\n'
        script = ("import subprocess,json; r=subprocess.run(['caveman-engine','compress','--type','log'],"
                  f"input={(original * 1000)!r}.encode(),capture_output=True,check=True); "
                  "print(json.loads(r.stderr)['recovery_handle'])")
        handle = services[0].service_capture('acb-caveman', ['python3', '-c', script]).strip()
        recovered = services[0].service_capture('acb-caveman', ['caveman-engine', 'retrieve', handle])
        assert recovered == original * 1000
        check = ("import subprocess; r=subprocess.run(['caveman-engine','retrieve'," + repr(handle) +
                 "],capture_output=True); assert r.returncode != 0")
        services[1].service_capture('acb-caveman', ['python3', '-c', check])
        (args.output / 'check.json').write_text(json.dumps({
            'passed': True, 'simultaneous_pods': pods,
            'own_store_retrieval': 'exact bytes', 'other_store_retrieval': 'rejected'}, indent=2))
    finally:
        for service in services:
            service.close()
        for pod in pods:
            pod_remove(pod)


if __name__ == '__main__':
    main()
