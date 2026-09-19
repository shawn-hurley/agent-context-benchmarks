"""Run all four deterministic bridge trials and reconcile proxy usage with the server.

Run with the isolated Harbor Python from the repository root. Requires a bridge
plan and the fixture's locally built base image. No real model is contacted.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from acb.harbor.dataset import prepare_dataset
from acb.harbor.paths import job_dir
from acb.harbor.praxis import is_model_request


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan['model']['endpoint'] != 'fixture-model:18080' or plan['subset'] != ['bridge']:
        raise ValueError('requires the deterministic bridge fixture')
    if set(plan['harnesses']) != {'goose', 'pi', 'opencode', 'claude-code'}:
        raise ValueError('requires all four harnesses')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan.pop('task_plans', None)
    plan['attempts'] = 1
    plan['manifest'] = prepare_dataset(plan)
    requested = output / 'fixture-plan.json'
    requested.write_text(json.dumps(plan, indent=2))
    with (output / 'worker.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'acb.harbor.worker', 'run', str(requested), str(output)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    checks = {}
    for harness in plan['harnesses']:
        report = json.loads((output / harness / 'report.json').read_text())
        assert report['instances'] == 1 and report['resolved'] == 1, harness
        assert report['incomplete_measurements'] == 0, harness
        record = report['evaluations'][0]
        trial = job_dir(output) / record['trial_name']
        requests = rows(trial / 'artifacts/fixture-requests.jsonl')
        # The deterministic server supplies exactly 100 input + 20 output tokens
        # per inference. Discovery GETs are not in this POST request ledger.
        assert requests and all(request.get('messages') is not None for request in requests), harness
        metrics = rows(trial / 'agent/acb/model_metrics.jsonl')
        usage = rows(output / harness / 'usage.jsonl')
        assert len(requests) == len(metrics) == len(usage), harness
        assert all(is_model_request(row) for row in usage), harness
        assert len({row['request_id'] for row in usage}) == len(usage), harness
        assert all(row['input_tokens'] == 100 and row['output_tokens'] == 20 for row in usage), harness
        assert report['avg_total_tokens'] == len(requests) * 120, harness
        assert report['avg_turns'] == len(requests), harness
        assert json.loads((trial / 'verifier/proxy-stopped.json').read_text())['stopped'], harness
        checks[harness] = {'model_requests': len(requests), 'total_tokens': len(requests) * 120,
                           'reward': 1, 'proxy_stopped_before_verifier': True}
    (output / 'accounting-check.json').write_text(json.dumps({'passed': True, 'harnesses': checks}, indent=2))
    print(f'Accounting check passed: {output}')


if __name__ == '__main__':
    main()
