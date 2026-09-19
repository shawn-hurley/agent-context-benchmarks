"""Live dataset-metric acceptance using a prepared combined-extension fixture."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from acb.harbor.metrics import freeze_metrics
from acb.preparation import freeze_metric_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prepared', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = json.loads(args.prepared.read_text())
    base['harnesses'] = {'pi': base['harnesses']['pi']}
    script = args.output / 'metric.py'
    script.write_text("import argparse,json\n"
                      "p=argparse.ArgumentParser();p.add_argument('-i');p.add_argument('-o');a=p.parse_args()\n"
                      "rewards=[json.loads(line) for line in open(a.i)]\n"
                      "json.dump({'twice_sum':2*sum((r or {}).get('reward',0) for r in rewards)},open(a.o,'w'))\n")
    checks = {}
    for kind in ('sum', 'custom'):
        plan = deepcopy(base)
        plan['attempts'] = 2 if kind == 'sum' else 1
        definitions = [{'type': 'sum'}] if kind == 'sum' else [
            {'type': 'uv-script', 'kwargs': {'script_path': str(script.resolve())}}]
        plan['manifest']['metrics'], plan['manifest']['metric_files'] = freeze_metrics(definitions, plan['cache_dir'])
        plan = freeze_metric_runtime(plan)
        prepared = args.output / (kind + '-prepared.json')
        prepared.write_text(json.dumps(plan, indent=2))
        output = args.output / kind
        with (args.output / (kind + '.log')).open('w') as log:
            subprocess.run([sys.executable, '-m', 'acb.harbor.worker', 'run', str(prepared), str(output)],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        report = json.loads((output / 'pi/report.json').read_text())
        expected = {'sum': 2} if kind == 'sum' else {'twice_sum': 2}
        native = list(report['dataset_metrics']['native_results'].values())
        assert len(native) == 1 and native[0]['metrics'] == [expected], native
        assert report['reward_aggregates']['reward']['mean'] == 1
        assert report['evaluation_errors'] == report['incomplete_measurements'] == 0
        checks[kind] = {'passed': True, 'native_metric': expected, 'acb_completed_mean': 1,
                        'attempts': plan['attempts']}
        (args.output / 'check.json').write_text(json.dumps(checks, indent=2))
        print(kind, checks[kind], flush=True)


if __name__ == '__main__':
    main()
