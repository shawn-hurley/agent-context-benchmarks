"""Per-benchmark comparisons shared by machine-readable, text and HTML reports.

A benchmark here is one dataset task, not a model request or a suite average.
Missing provenance never establishes compatibility; diagnostics retain partial
observations without turning them into comparable measurements.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path

from acb.telemetry import trajectory
from acb.utils import normalize_instance_id_for_path

TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens')


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def delta(before, after):
    if before is None or after is None:
        return {'absolute': None, 'percent': None}
    return {'absolute': after - before,
            'percent': (after - before) / abs(before) * 100 if before else None}


def quality(changes):
    known = set(changes) - {'unknown', 'not_comparable'}
    if not known:
        return None
    if 'better' in known and 'worse' in known:
        return 'mixed'
    return 'better' if 'better' in known else 'worse' if 'worse' in known else 'same'


def leaf_reports(root):
    root = Path(root)
    paths = [root / 'report.json'] if (root / 'report.json').exists() else []
    paths.extend(sorted(root.rglob('report.json')))
    seen = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        report = json.loads(path.read_text())
        if isinstance(report.get('harness'), str) and 'instances' in report:
            yield path.parent, report


def benchmarks(root):
    if not Path(root).is_dir():
        raise FileNotFoundError(f"run directory does not exist: {root}")
    rows = {}
    for directory, report in leaf_reports(root):
        metrics = {m['instance_id']: m for m in jsonl(directory / 'metrics.jsonl')}
        usage = defaultdict(list)
        for item in jsonl(directory / 'usage.jsonl'):
            usage[item['instance_id']].append(item)
        evaluations = report.get('evaluations')
        if evaluations is None:
            evaluations = [{'task_id': m['instance_id'], 'trial_id': m['instance_id'],
                            'resolved': m.get('resolved'), 'status': 'completed' if m.get('resolved') is not None else 'error'}
                           for m in metrics.values()]
        groups = defaultdict(list)
        for record in evaluations:
            groups[record['task_id']].append(record)
        for task_id, records in groups.items():
            dataset = report.get('dataset') or report.get('benchmark')
            key = (dataset, task_id, report['harness'])
            if key in rows:
                raise ValueError(f'duplicate benchmark identity in comparison input: {key}; select one configuration per side')
            definition = report.get('grade_definition', {'metric': 'resolved', 'direction': 'higher', 'tolerance': 0})
            grades = []
            trial_data = []
            complete = True
            for record in records:
                value = record.get('resolved') if definition['metric'] == 'resolved' else (record.get('rewards') or {}).get(definition['metric'])
                valid = record.get('status') == 'completed' and isinstance(value, (int, float)) and math.isfinite(value)
                grades.append(float(value) if valid else None)
                iid = str(record.get('trial_id') or '')
                requests = usage.get(iid, [])
                complete = (complete and record.get('measurement_complete') is True
                            and (directory/'usage.jsonl').exists()
                            and all(type(u.get(field)) is int and u[field] >= 0
                                    for u in requests for field in TOKEN_FIELDS))
                observed = trajectory(directory/'instances'/normalize_instance_id_for_path(iid)/'transcript.jsonl', report['harness'])
                if observed['definition'] is None:
                    step_traces = sorted((directory/'instances'/normalize_instance_id_for_path(iid)/'steps').glob('*/transcript.jsonl'))
                    if step_traces:
                        order = {step['step_name']: i for i, step in enumerate(record.get('step_results') or [])}
                        step_traces.sort(key=lambda p: order.get(p.parent.name, len(order)))
                        observed_steps = [{'step':p.parent.name, **trajectory(p, report['harness'])} for p in step_traces]
                        observed = {
                            'turns':sum(t['turns'] for t in observed_steps) if all(t['turns'] is not None for t in observed_steps) else None,
                            'tool_calls':sum(t['tool_calls'] for t in observed_steps) if all(t['tool_calls'] is not None for t in observed_steps) else None,
                            'definition':'sum of explicit per-step trajectory counts', 'steps':observed_steps,
                        }
                trial_data.append({'id': iid, 'evaluation': record, 'metrics': metrics.get(iid),
                                   'requests': requests, 'trajectory': observed})
            all_usage = [u for trial in trial_data for u in trial['requests']]
            buckets = {field: sum(u[field] for u in all_usage if type(u.get(field)) is int and u[field] >= 0) for field in TOKEN_FIELDS}
            provenance = report.get('comparison_provenance')
            task_provenance = (provenance or {}).get('tasks', {}).get(task_id)
            rows[key] = {'dataset': dataset, 'benchmark': task_id, 'harness': report['harness'],
                         'dataset_metrics': report.get('dataset_metrics'),
                         'model': report.get('model'), 'directory': str(directory.resolve()),
                         'definition': definition, 'grades': grades,
                         'grade': sum(grades) / len(grades) if grades and all(g is not None for g in grades) else None,
                         'grade_aggregation': 'mean across attempts', 'trials': trial_data,
                         'provenance': {'conditions': (provenance or {}).get('conditions'), 'task': task_provenance},
                         'measurement_complete': complete,
                         'tokens': sum(buckets.values()) if complete else None,
                         'captured_tokens': sum(buckets.values()), 'token_buckets': buckets,
                         'captured_model_requests': len(all_usage),
                         'captured_turns': sum(t['trajectory']['turns'] for t in trial_data) if all(t['trajectory']['turns'] is not None for t in trial_data) else None,
                         'captured_tool_calls': sum(t['trajectory']['tool_calls'] for t in trial_data) if all(t['trajectory']['tool_calls'] is not None for t in trial_data) else None,
                         'model_requests': len(all_usage) if complete else None,
                         'turns': sum(t['trajectory']['turns'] for t in trial_data) if complete and all(t['trajectory']['turns'] is not None for t in trial_data) else None,
                         'tool_calls': sum(t['trajectory']['tool_calls'] for t in trial_data) if complete and all(t['trajectory']['tool_calls'] is not None for t in trial_data) else None,
                         'telemetry_notes': ['Agent turns and tool calls require explicit trajectory telemetry; model requests are counted separately.']}
    return rows


def provenance_available(provenance):
    conditions, task = provenance.get('conditions'), provenance.get('task')
    if not conditions or not task or conditions.get('timeout') is None:
        return False
    if not (task.get('sha256') or task.get('revision')):
        return False
    # Optional defaults such as cache policy can legitimately be null. Runtime
    # identity for Harbor cannot: it captures the inspected execution contract.
    if conditions.get('backend') == 'harbor' and not task.get('runtime'):
        return False
    return True


def compare(baseline, candidate):
    before, after = benchmarks(baseline), benchmarks(candidate)
    # A single configuration on either side may intentionally use a different
    # harness. Multi-harness suites match by harness to avoid ambiguous pairing.
    if len({k[2] for k in before}) == len({k[2] for k in after}) == 1:
        before = {k[:2]: v for k, v in before.items()}
        after = {k[:2]: v for k, v in after.items()}
    rows = []
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key), after.get(key)
        reasons = []
        if left is None or right is None:
            reasons.append('Benchmark is absent from baseline' if left is None else 'Benchmark is absent from candidate')
        else:
            for side, record in [('baseline', left), ('candidate', right)]:
                if not provenance_available(record['provenance']):
                    reasons.append(f'{side}: comparison provenance unavailable')
            if left['provenance'] != right['provenance']:
                reasons.append('Task revision, inputs, grading, budget or resource conditions differ')
            if left['definition'] != right['definition']:
                reasons.append('Grade definitions differ')
            if len(left['trials']) != len(right['trials']):
                reasons.append('Attempt counts differ')
        direction = 'not_comparable' if reasons else 'unknown'
        if not reasons and left['grade'] is not None and right['grade'] is not None:
            definition = left['definition']
            if definition.get('direction') not in ('higher', 'lower'):
                reasons.append('Grade direction is unspecified')
                direction = 'not_comparable'
            else:
                change = right['grade'] - left['grade']
                tolerance = definition.get('tolerance', 0)
                direction = 'same' if abs(change) <= tolerance else (
                    'better' if change * (1 if definition['direction'] == 'higher' else -1) > 0 else 'worse')
        usable = not reasons and left['measurement_complete'] and right['measurement_complete']
        rows.append({'identity': list(key), 'baseline': left, 'candidate': right,
                     'comparable': not reasons, 'reasons': reasons, 'quality': direction,
                     'tokens': delta(left['tokens'], right['tokens']) if usable else delta(None, None),
                     'measurement_comparable': usable,
                     'diagnostic_comparability': {
                         field: bool(usable and left[field] is not None and right[field] is not None
                                     and (field != 'turns' or left['harness'] == right['harness']))
                         for field in ('turns', 'model_requests', 'tool_calls')},
                     'telemetry_notes': ['Turn definitions differ across harnesses; turn counts are shown without a delta.']
                         if left and right and left['harness'] != right['harness'] else []})
    judged = [r for r in rows if r['quality'] in ('same', 'better', 'worse')]
    measured = [r for r in rows if r['measurement_comparable']]
    totals = {side: sum(r[side]['tokens'] for r in measured) if measured else None for side in ('baseline', 'candidate')}
    return {'baseline': str(baseline), 'candidate': str(candidate), 'rows': rows,
            'quality': quality([r['quality'] for r in rows]),
            'coverage': {'graded': len(judged), 'total': len(rows), 'complete': bool(rows) and len(judged) == len(rows),
                         'measured': len(measured)},
            'matched_tokens': {**totals, **delta(totals['baseline'], totals['candidate']),
                               'benchmarks': [r['identity'] for r in measured]}}


def legacy_provenance(report_path, plan, harness, instances):
    """Capture resolved inputs rather than infer compatibility from directory names."""
    from dataclasses import asdict
    import hashlib
    report_path=Path(report_path)
    report=json.loads(report_path.read_text())
    config=plan['benchmark_config']
    report['dataset']=config.get('dataset',plan['benchmark'])
    report['grade_definition']={'metric':'resolved','direction':'higher','tolerance':0}
    report['comparison_provenance']={
        'conditions':{'backend':'legacy','environment':plan.get('environment'),
                      'timeout':plan['harnesses'][harness].get('timeout'),
                      'attempts':plan.get('attempts',1),'cache_policy':plan.get('cache_policy'),
                      'benchmark_config':config},
        'tasks':{item.instance_id:{'sha256':hashlib.sha256(json.dumps(asdict(item),sort_keys=True,default=str).encode()).hexdigest()}
                 for item in instances}}
    evaluations=[]
    for metric in jsonl(report_path.parent/'metrics.jsonl'):
        iid=metric['instance_id'];folder=report_path.parent/'instances'/normalize_instance_id_for_path(iid)
        prediction=json.loads((folder/'prediction.json').read_text()) if (folder/'prediction.json').exists() else {}
        failed=bool(prediction.get('error')) or (folder/'error.json').exists()
        measurement=json.loads((folder/'measurement.json').read_text()) if (folder/'measurement.json').exists() else {}
        evaluations.append({'task_id':iid,'trial_id':iid,'resolved':None if failed else metric.get('resolved'),
                            'status':'error' if failed or metric.get('resolved') is None else 'completed',
                            'measurement_complete':not failed and measurement.get('complete') is True and (folder/'usage.jsonl').exists()})
    report['evaluations']=evaluations
    report_path.write_text(json.dumps(report,indent=2))
