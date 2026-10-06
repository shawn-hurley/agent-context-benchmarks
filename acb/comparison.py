"""Per-benchmark comparisons shared by machine-readable, text and HTML reports.

A benchmark here is one dataset task, not a model request or a suite average.
Application inputs and grading define compatibility. Experimental setup changes
are comparison context; incomplete measurements remain unavailable.
"""
from __future__ import annotations

from collections import defaultdict
import math

from acb.telemetry import trajectory
from acb.report_data import ReportSource

TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens')


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


def benchmarks(root):
    return ReportSource(root).benchmarks


def benchmark_records(harnesses):
    """Derive benchmark grades and coverage from loaded harness reports."""
    rows = {}
    for source in harnesses:
        directory, report = source.directory, source.report
        metrics, usage = source.metrics, source.usage
        evaluations = report.get('evaluations', [])
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
                trial_directory = source.trial_directory(iid)
                observed = (trajectory(trial_directory / 'transcript.jsonl', report['harness'])
                            if trial_directory else {'turns': None, 'tool_calls': None, 'events': [], 'definition': None})
                if observed['definition'] is None and trial_directory:
                    step_traces = sorted((trial_directory / 'steps').glob('*/transcript.jsonl'))
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
            if task_provenance is not None:
                task_provenance = dict(task_provenance)
                if not task_provenance.get('benchmark_contract'):
                    contract = source.benchmark_contracts.get(task_id)
                    if contract is not None:
                        task_provenance['benchmark_contract'] = contract
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


def benchmark_identity(provenance):
    task = provenance.get('task') or {}
    contract = task.get('benchmark_contract')
    if isinstance(contract, dict) and contract.get('version') == 1 and contract.get('inputs') and contract.get('grading'):
        return contract
    if task.get('revision'):
        return {'revision': task['revision']}
    if task.get('sha256'):
        # Older tasks without recoverable semantic identity require the same
        # frozen bundle; do not infer compatibility from matching task names.
        return {'sha256': task['sha256']}
    return None


def setup_differences(before, after, prefix=''):
    """Keep differing experiment settings visible without blocking comparisons."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [difference for key in sorted(before.keys() | after.keys())
                for difference in setup_differences(before.get(key), after.get(key),
                                                    f'{prefix}.{key}' if prefix else key)]
    if before != after:
        return [{'setting': prefix, 'baseline': before, 'candidate': after}]
    return []


def compare(baseline, candidate):
    return compare_records(benchmarks(baseline), benchmarks(candidate), str(baseline), str(candidate))


def compare_records(before, after, baseline, candidate):
    """Compare already loaded benchmark records without reading files."""
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
            identities = [benchmark_identity(record['provenance']) for record in (left, right)]
            for side, identity in zip(('baseline', 'candidate'), identities):
                if identity is None:
                    reasons.append(f'{side}: benchmark input and grading identity unavailable')
            if all(identities) and identities[0] != identities[1]:
                reasons.append('Benchmark application inputs or grading criteria differ')
            grader = [(record['provenance'].get('conditions') or {}).get('native_grader')
                      for record in (left, right)]
            if grader[0] != grader[1]:
                reasons.append('Native grader definitions differ')
            if left['definition'] != right['definition']:
                reasons.append('Grade definitions differ')
        differences = (setup_differences(left['provenance'], right['provenance'])
                       if left and right else [])
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
                     'setup_differences': differences,
                     'tokens': delta(left['tokens'], right['tokens']) if usable else delta(None, None),
                     'measurement_comparable': usable,
                     'diagnostic_comparability': {
                         field: bool(usable and left[field] is not None and right[field] is not None
                                     and (field != 'turns' or left['harness'] == right['harness']))
                         for field in ('turns', 'model_requests', 'tool_calls')},
                     'telemetry_notes': (['Turn definitions differ across harnesses; turn counts are shown without a delta.']
                         if left and right and left['harness'] != right['harness'] else []) +
                         ([f"Attempt counts differ ({len(left['trials'])} vs {len(right['trials'])}); grades are means and tokens are totals across attempts."]
                          if left and right and len(left['trials']) != len(right['trials']) else [])})
    judged = [r for r in rows if r['quality'] in ('same', 'better', 'worse')]
    measured = [r for r in rows if r['measurement_comparable']]
    totals = {side: sum(r[side]['tokens'] for r in measured) if measured else None for side in ('baseline', 'candidate')}
    return {'baseline': str(baseline), 'candidate': str(candidate), 'rows': rows,
            'quality': quality([r['quality'] for r in rows]),
            'coverage': {'graded': len(judged), 'total': len(rows), 'complete': bool(rows) and len(judged) == len(rows),
                         'measured': len(measured)},
            'matched_tokens': {**totals, **delta(totals['baseline'], totals['candidate']),
                               'benchmarks': [r['identity'] for r in measured]}}
