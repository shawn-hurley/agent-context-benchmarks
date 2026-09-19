"""Frozen dataset metrics; custom scripts execute in disposable containers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import signal
import threading
import tempfile
import uuid


def metric_definitions(values):
    from harbor.models.metric.config import MetricConfig
    return [MetricConfig.model_validate(value).model_dump(mode="json") for value in values]


def freeze_metrics(values, cache):
    definitions=metric_definitions(values)
    files={}
    for definition in definitions:
        if definition['type']!='uv-script':continue
        source=Path(definition['kwargs']['script_path']).resolve()
        data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
        directory=Path(cache).resolve()/'dataset-metrics'/digest;directory.mkdir(parents=True,exist_ok=True)
        target=directory/'metric.py'
        if target.exists() and target.read_bytes()!=data:
            raise ValueError('cached dataset metric script was modified')
        target.write_bytes(data)
        definition['kwargs']['script_path']=str(target)
        files[str(target)]=digest
    return definitions,files


def verify_metrics(manifest):
    for path,digest in manifest.get('metric_files',{}).items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:
            raise ValueError('prepared dataset metric script changed')


class ContainerMetric:
    """Harbor metric protocol using UV inside a bounded, credential-free container."""
    def __init__(self, script, runtime):
        self.script=Path(script);self.runtime=runtime

    def compute(self,rewards):
        engine=self.runtime['engine'];name='acb-metric-'+uuid.uuid4().hex[:16]
        def command(*args,**kwargs):
            return subprocess.run([engine,*args],check=True,capture_output=True,text=True,
                                  timeout=kwargs.pop('timeout',30),**kwargs)
        old_handlers = {}
        if threading.current_thread() is threading.main_thread():
            def interrupted(signum, frame):
                raise KeyboardInterrupt("metric execution cancelled")
            for sig in (signal.SIGINT, signal.SIGTERM):
                old_handlers[sig] = signal.signal(sig, interrupted)
        try:
            return self._compute(rewards, name, command)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)

    def _compute(self, rewards, name, command):
        engine = self.runtime['engine']
        with tempfile.TemporaryDirectory(prefix='acb-metric-') as tmp:
            root=Path(tmp);input_path=root/'rewards.jsonl'
            input_path.write_text(''.join(json.dumps(r)+'\n' for r in rewards))
            try:
                command('create','--name',name,'--cpus','1','--memory','1g',
                        '--network','none' if self.runtime.get('offline') else 'bridge',
                        '--entrypoint','uv',self.runtime['image_id'],
                        'run','/metric.py','-i','/rewards.jsonl','-o','/metric-result.json')
                command('cp',str(self.script),name+':/metric.py')
                command('cp',str(input_path),name+':/rewards.jsonl')
                command('start','-a',name,timeout=300)
                command('cp',name+':/metric-result.json',str(root/'result.json'))
                values=json.loads((root/'result.json').read_text())
                import math
                if not isinstance(values,dict) or any(type(v) not in (int,float) or not math.isfinite(v) for v in values.values()):
                    raise ValueError('custom metric must return finite numeric values')
                return values
            finally:
                subprocess.run([engine,'rm','-f',name],capture_output=True,timeout=30)


def make_metric(definition,runtime=None):
    from harbor.metrics.factory import MetricFactory
    from harbor.models.metric.config import MetricConfig
    config=MetricConfig.model_validate(definition)
    if config.type.value=='uv-script':
        if runtime is None:
            raise ValueError('dataset script metrics require a prepared container metric executor')
        return ContainerMetric(config.kwargs['script_path'],runtime)
    return MetricFactory.create_metric(config.type,**config.kwargs)


def aggregate_metrics(definitions,rewards,runtime=None):
    """Keep Harbor's handling of None, including its aggregate denominator."""
    definitions=metric_definitions(definitions or [{'type':'mean'}])
    return [{'definition':d,'values':make_metric(d,runtime).compute(rewards) if rewards else None}
            for d in definitions]


def bind_container_metrics(job,plan):
    """Replace script executors before Harbor can calculate any live/final stats."""
    from harbor.metrics.uv_script import UvScript
    for dataset,metrics in job._metrics.items():
        job._metrics[dataset]=[ContainerMetric(metric._script_path,plan['metric_runtime'])
                              if isinstance(metric,UvScript) else metric for metric in metrics]


def comparison_definitions(manifest):
    """Compare frozen script content, independent of the controller cache path."""
    from copy import deepcopy
    definitions = deepcopy(manifest.get('metrics', []))
    for definition in definitions:
        if definition['type'] == 'uv-script':
            path = definition['kwargs']['script_path']
            definition['kwargs']['script_path'] = 'sha256:' + manifest['metric_files'][path]
    return definitions
