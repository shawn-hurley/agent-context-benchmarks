"""Load each harness report once and index observations by trial identity."""
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def by_trial(rows):
    indexed = defaultdict(list)
    for row in rows:
        indexed[row['instance_id']].append(row)
    return dict(indexed)


@dataclass
class HarnessReport:
    directory: Path
    report: dict

    @cached_property
    def metrics(self):
        return {row['instance_id']: row for row in read_jsonl(self.directory / 'metrics.jsonl')}

    @cached_property
    def usage(self):
        return by_trial(read_jsonl(self.directory / 'usage.jsonl'))

    @cached_property
    def benchmark_metrics(self):
        return by_trial(read_jsonl(self.directory / 'benchmark_metrics.jsonl'))

    def trial_directory(self, trial_id):
        from acb.utils import normalize_instance_id_for_path
        name = normalize_instance_id_for_path(trial_id)
        if name in ('', '.', '..'):
            return None
        return self.directory / name

    def prediction(self, trial_id):
        directory = self.trial_directory(trial_id)
        if directory is None:
            return ''
        path = directory / 'prediction.json'
        try:
            return json.loads(path.read_text()).get('model_patch') or ''
        except (OSError, ValueError):
            return ''


class ReportSource:
    """One input run/suite, shared by comparison and HTML section builders."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f'run directory does not exist: {root}')

    @cached_property
    def harnesses(self):
        result = {}
        # Current output has one report at each harness root (or the supplied
        # root itself). Do not descend into raw verifier evidence for reports.
        paths = [self.root / 'report.json', *sorted(self.root.glob('*/report.json'))]
        for path in paths:
            if not path.is_file():
                continue
            report = json.loads(path.read_text())
            if isinstance(report.get('harness'), str) and 'instances' in report:
                result[path.parent] = HarnessReport(path.parent, report)
        return result

    @cached_property
    def benchmarks(self):
        from acb.comparison import benchmark_records
        return benchmark_records(self.harnesses.values())
