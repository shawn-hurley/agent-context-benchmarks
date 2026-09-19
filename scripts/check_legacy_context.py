"""Four-harness retained-runner Caveman acceptance using deterministic tools."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import signal
import threading
import time
from pathlib import Path
import subprocess
import uuid

from acb import runner
from acb.benchmarks import Instance, Prediction
from acb.config import ModelSpec, Registries, RunConfig
from acb.container import container_create, container_start, container_exec_capture, container_cp_in, container_cp_out, container_stop_rm
from acb.preparation import prepare
from acb.resolver import resolve
from scripts.check_harbor_caveman import ORIGINAL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path, help='prepared Harbor compression task directory')
    parser.add_argument('output', type=Path)
    parser.add_argument('--combined', action='store_true')
    parser.add_argument('--fail-service', action='store_true')
    parser.add_argument('--cancel', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    fixture_server = args.fixture / 'environment/server.py'
    actual_make_harness = runner.make_harness
    if args.cancel:
        fixture_server = args.output / 'server.py'
        fixture_server.write_text((args.fixture / 'environment/server.py').read_text().replace(
            '"cat /work/events.log",', '"printf ACB_CONTEXT_WAIT; sleep 600",', 1))

    def check(name):
        out = args.output / name
        out.mkdir()
        cfg = RunConfig(run_id='context-' + uuid.uuid4().hex[:12], benchmark='fixture', harness=name,
                        model='fixture-model', extensions=[{'name': 'caveman', 'options': {'mode': 'compress'}}],
                        execution={'timeout': 120}, source_file=str(Path('run.yaml').resolve()),
                        overrides={'harness': {'workdir': '/work'}})
        if args.combined:
            cfg.extensions.insert(0, {'name': 'rtk'})
        registry = Registries({}, {'fixture': {'execution_backend': 'legacy', 'praxis_image': 'acb-praxis-ai:latest'}}, {},
            models={'fixture-model': {'model': 'fixture-model', 'api': 'openai', 'endpoint': '127.0.0.2:18080', 'tls': False}},
            machine={'cache_dir': str(Path('runs/.cache').resolve())})
        plan = prepare(resolve(cfg, registry))
        model = ModelSpec(**plan['model'])
        (out / 'prepared.json').write_text(json.dumps(plan, indent=2))

        class Fixture:
            graded = False
            def prepare_container(self, instance, pod, build_dir, arch):
                self.pod = pod
                task = pod + '-task'
                container_create(pod, 'localhost/acb-rtk-smoke:0.48.0', task, command=['tail', '-f', '/dev/null'])
                container_start(task)
                model.endpoint = container_exec_capture(task, ['hostname', '-i']).split()[0] + ':18080'
                (out / 'fixture-endpoint.txt').write_text(model.endpoint)
                container_exec_capture(task, ['mkdir', '-p', '/work'])
                container_cp_in(task, args.fixture / 'environment/events.log', '/work/events.log')
                container_cp_in(task, fixture_server, '/fixture-server.py')
                container_exec_capture(task, ['bash', '-c',
                    'nohup /opt/miniconda3/bin/python /fixture-server.py </dev/null >/tmp/fixture.log 2>&1 &'])
                return task

            def collect_prediction_container(self, instance, task, model):
                assert container_exec_capture(task, ['cat', '/work/recovered.log']) == ORIGINAL
                assert container_exec_capture(task, ['cat', '/work/answer.txt']) == 'fixed\n'
                container_cp_out(task, '/tmp/acb-rtk-requests.jsonl', out / 'requests.jsonl')
                active = subprocess.check_output(['podman', 'ps', '--format', '{{.Names}}'], text=True)
                assert self.pod + '-caveman' not in active and self.pod + '-praxis' not in active
                return Prediction(instance.instance_id, model, model_patch='fixed\n')

            def evaluate(self, **kwargs):
                self.graded = True
                return {'tiny': True}

        fixture = Fixture()
        if args.fail_service or args.cancel:
            def make_harness(harness, settings):
                adapter = actual_make_harness(harness, settings)
                original = adapter.run_container
                def run(*a, **kw):
                    if args.cancel:
                        def cancel_when_running():
                            transcript = out / 'instances/tiny/transcript.jsonl'
                            for _ in range(600):
                                if transcript.exists() and 'sleep 600' in transcript.read_text():
                                    fixture.cancelled_at = time.monotonic()
                                    os.kill(os.getpid(), signal.SIGINT)
                                    return
                                time.sleep(0.1)
                        threading.Thread(target=cancel_when_running, daemon=True).start()
                        return original(*a, **kw)
                    result = original(*a, **kw)
                    # Loss between the final response and collection must still fail.
                    container_stop_rm(fixture.pod + '-caveman')
                    return result
                adapter.run_container = run
                return adapter
            runner.make_harness = make_harness
        cancelled = False
        try:
            prediction, resolved = runner._run_instance_pipeline(
                Instance('tiny', 'Execute the fixture commands supplied by the API.'), name, out,
                cfg, plan['harnesses'][name], fixture, plan['benchmark_config'],
                model, {}, Path(plan['cache_dir']))
        except KeyboardInterrupt:
            if not args.cancel:
                raise
            cancelled = True
        all_containers = subprocess.check_output(['podman', 'ps', '-a', '--format', '{{.Names}}'], text=True)
        assert fixture.pod not in all_containers
        if args.cancel:
            assert cancelled and not fixture.graded
            assert time.monotonic() - fixture.cancelled_at < 30
            return {'cancelled': True, 'cleanup': True, 'grading_skipped': True}
        if args.fail_service:
            assert prediction.error and not resolved and not fixture.graded
            return {'service_failure_rejected': True, 'cleanup': True, 'error': prediction.error}
        assert not prediction.error and resolved == {'tiny': True}, (prediction.error or '')[:200]
        directory = out / 'instances/tiny'
        manifest = json.loads((directory / 'integrations/caveman/manifest.json').read_text())
        assert manifest['verification']['compression_verified'] and manifest['verification']['agent_tool_verified']
        if args.combined:
            rtk = json.loads((directory / 'integrations/rtk/manifest.json').read_text())
            activity = rtk['metadata']['activity']
            assert activity['agent_tool_verified'] and activity['commands_rewritten'] >= 1 and activity['errors'] == 0, rtk
        requests = [json.loads(line) for line in (out / 'requests.jsonl').read_text().splitlines()]
        rows = [json.loads(line) for line in (directory / 'usage.jsonl').read_text().splitlines()]
        assert len(rows) == len(requests)
        assert all(r['input_tokens'] == 100 and r['output_tokens'] == 20 for r in rows)
        results = {m['tool_call_id']: m['content'] for r in requests for m in r.get('messages', []) if m.get('role') == 'tool'}
        assert 'acb-recall ' in results['fixture-call-0'] and ORIGINAL not in results['fixture-call-0']
        assert ORIGINAL in results['fixture-call-2'] and 'acb-recall ' not in results['fixture-call-2']
        assert ORIGINAL in results['fixture-call-3'] and 'ERROR critical diagnostic' in results['fixture-call-3']
        return {'passed': True, 'reward': 1, 'requests': len(rows), 'fixture_tokens': len(rows) * 120,
                'compressed': True, 'exact_recovery': True, 'failed_and_critical_preserved': True, 'cleanup': True}

    names = ['pi'] if args.fail_service or args.cancel else ['goose', 'pi', 'opencode', 'claude-code']
    if args.cancel:
        results = {'pi': check('pi')}
    else:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            results = dict(zip(names, pool.map(check, names)))
    (args.output / 'check.json').write_text(json.dumps({'passed': True, 'harnesses': results}, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
