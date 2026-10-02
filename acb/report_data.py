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


def read_object(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def names(items):
    """Take display names from configured components without exposing their config."""
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        name = item if isinstance(item, str) else item.get('name') if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip() and name not in result:
            result.append(name)
    return result


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

    def source_changes(self, trial_id):
        """Return a saved directory diff, or derive one for older Harbor runs."""
        directory = self.trial_directory(trial_id)
        if directory is None:
            return None
        artifact = directory / 'source-changes.diff'
        if artifact.is_file():
            return {'diff': artifact.read_text(), 'artifact': artifact, 'source': None, 'candidate': None}
        prediction = read_object(directory / 'prediction.json')
        if prediction.get('model_patch') is not None or not isinstance(prediction.get('output'), str):
            return None
        native_root = (directory / 'verifier/native').resolve()
        candidate_run = Path(prediction['output']).resolve()
        harbor_root = (self.directory.parent / '.harbor').resolve()
        if not candidate_run.is_relative_to(native_root) and not candidate_run.is_relative_to(harbor_root):
            return None
        before, after = candidate_run / 'input', candidate_run / 'output'
        if not before.is_dir() or not after.is_dir():
            return None
        from acb.tree_diff import source_tree_diff
        return {'diff': source_tree_diff(before, after), 'artifact': None,
                'source': before, 'candidate': after}


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

    @cached_property
    def run_info(self):
        """Selected run facts for HTML headings; never embed the full effective plan."""
        requested = read_object(self.root / 'requested.json')
        resolved = read_object(self.root / 'resolved.json')
        reports = [harness.report for harness in self.harnesses.values()]
        if not requested and not resolved:
            # A report command may point at one harness inside a saved suite.
            parent_resolved = read_object(self.root.parent / 'resolved.json')
            parent_requested = read_object(self.root.parent / 'requested.json')
            report_ids = {report.get('run_id') for report in reports if report.get('run_id')}
            if report_ids and (parent_resolved.get('run_id') in report_ids or
                               parent_requested.get('run_id') in report_ids):
                requested, resolved = parent_requested, parent_resolved
        if not requested and isinstance(resolved.get('requested_config'), dict):
            requested = resolved['requested_config']

        def report_values(key):
            return list(dict.fromkeys(str(report[key]) for report in reports if report.get(key)))

        model = resolved.get('model')
        if isinstance(model, dict):
            model = model.get('name')
        model = model or requested.get('model') or ', '.join(report_values('model')) or None
        benchmark = resolved.get('benchmark') or requested.get('benchmark') or ', '.join(report_values('benchmark')) or None
        workflow = resolved.get('workflow')
        workflow_name = workflow.get('name') if isinstance(workflow, dict) else workflow
        workflow_name = workflow_name or requested.get('workflow')
        harness_settings = resolved.get('harnesses') if isinstance(resolved.get('harnesses'), dict) else {}
        harness_names = report_values('harness') or list(harness_settings)
        harnesses = [name + (f" {harness_settings[name]['version']}" if isinstance(harness_settings.get(name), dict)
                             and harness_settings[name].get('version') else '') for name in harness_names]

        benchmark_config = resolved.get('benchmark_config') if isinstance(resolved.get('benchmark_config'), dict) else {}
        overrides = requested.get('overrides') if isinstance(requested.get('overrides'), dict) else {}
        benchmark_overrides = overrides.get('benchmark') if isinstance(overrides.get('benchmark'), dict) else {}
        source = benchmark_config.get('source') or benchmark_overrides.get('source')
        target = benchmark_config.get('target') or benchmark_overrides.get('target')

        skills = names(requested.get('skills'))
        mcp_servers = names(requested.get('mcp_servers'))
        extensions = names(requested.get('extensions'))
        integrations = []
        for settings in harness_settings.values():
            if not isinstance(settings, dict):
                continue
            for name in names(settings.get('skills')):
                if name not in skills:
                    skills.append(name)
            for key, destination in (('mcp_servers', mcp_servers), ('extensions', extensions),
                                     ('execution_integrations', integrations)):
                for name in names(settings.get(key)):
                    if name not in destination:
                        destination.append(name)
        if isinstance(workflow, dict):
            environment = workflow.get('environment')
            assets = environment.get('assets', []) if isinstance(environment, dict) else []
            for asset in assets if isinstance(assets, list) else []:
                if not isinstance(asset, str):
                    continue
                parts = Path(asset).parts
                if len(parts) >= 3 and parts[-1] == 'SKILL.md' and 'skills' in parts:
                    skill = parts[parts.index('skills') + 1] + ' (workflow)'
                    if skill not in skills:
                        skills.append(skill)
        skill_delivery_recorded = False
        for harness in self.harnesses.values():
            for evaluation in harness.report.get('evaluations', []):
                trial_id = evaluation.get('trial_id')
                if not trial_id:
                    continue
                directory = harness.trial_directory(str(trial_id))
                delivery = read_object(directory / 'skill-delivery.json') if directory else {}
                if delivery:
                    skill_delivery_recorded = True
                for name in names(delivery.get('task_skills')):
                    label = name + ' (task)'
                    if label not in skills:
                        skills.append(label)
                for name in names(delivery.get('configured_skills')):
                    if name not in skills:
                        skills.append(name)

        return {
            'run_id': resolved.get('run_id') or requested.get('run_id') or
                      (report_values('run_id')[0] if report_values('run_id') else self.root.name),
            'benchmark': benchmark, 'model': model, 'harnesses': harnesses,
            'workflow': workflow_name, 'skills': skills, 'mcp_servers': mcp_servers,
            'extensions': extensions, 'integrations': integrations,
            'proxy': resolved.get('proxy') or requested.get('proxy') or
                     (report_values('proxy')[0] if report_values('proxy') else None),
            'source': source, 'target': target,
            'configuration_recorded': bool(requested or resolved or skill_delivery_recorded),
        }
