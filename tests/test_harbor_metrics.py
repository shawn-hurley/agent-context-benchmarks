import pytest

pytest.importorskip('harbor')
from acb.harbor.metrics import aggregate_metrics, metric_definitions


def test_non_average_metrics_preserve_native_missing_reward_semantics():
    rewards=[{'score':2},{'score':4},None]
    values=aggregate_metrics([{'type':'sum'},{'type':'max'},{'type':'mean'}],rewards)
    assert [v['values'] for v in values]==[{'sum':6},{'max':4},{'mean':2}]
    # ACB's completed-only mean would be 3; it must not replace Harbor's 2.


def test_script_metrics_do_not_silently_execute_on_host():
    with pytest.raises(ValueError,match='container metric executor'):
        aggregate_metrics([{'type':'uv-script','kwargs':{'script_path':'metric.py'}}], [{'score':1}])


def test_harness_identity_separates_native_metric_groups(tmp_path):
    from acb.harbor.agent import ACBHarborAgent
    a=ACBHarborAgent(logs_dir=tmp_path/'a',model_name='provider/model',plan={},harness='pi')
    b=ACBHarborAgent(logs_dir=tmp_path/'b',model_name='provider/model',plan={},harness='goose')
    assert a.to_agent_info().name=='acb-pi'
    assert b.to_agent_info().name=='acb-goose'
    assert ACBHarborAgent.name()=='acb'


def test_frozen_metric_script_is_absolute_and_tamper_evident(tmp_path, monkeypatch):
    from acb.harbor.metrics import freeze_metrics, verify_metrics
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    Path('metric.py').write_text('print("fixture")')
    definitions, files = freeze_metrics([{'type':'uv-script','kwargs':{'script_path':'metric.py'}}], 'cache')
    script = Path(definitions[0]['kwargs']['script_path'])
    assert script.is_absolute()
    verify_metrics({'metric_files': files})
    script.write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        verify_metrics({'metric_files': files})


@pytest.mark.parametrize('failure', [TimeoutError, KeyboardInterrupt])
def test_custom_metric_always_removes_owned_container(tmp_path, monkeypatch, failure):
    import subprocess
    from acb.harbor.metrics import ContainerMetric
    commands = []
    def execute(command, **kwargs):
        commands.append(command)
        if command[1] == 'start':
            raise failure('cancelled')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', execute)
    metric = ContainerMetric(tmp_path/'script.py', {'engine':'docker','image_id':'sha256:fixture','offline':True})
    with pytest.raises(failure):
        metric.compute([{'score':1}])
    assert commands[0][1] == 'create'
    assert commands[0][commands[0].index('--network')+1] == 'none'
    assert commands[-1][:3] == ['docker','rm','-f']
    assert commands[-1][-1] == commands[0][commands[0].index('--name')+1]


def test_job_receives_dataset_metrics_and_timeout_cap(tmp_path):
    from acb.harbor.worker import job_config
    from acb.harbor import PROTOCOL_VERSION
    plan = {'protocol_version':PROTOCOL_VERSION,
        'environment':'podman','harnesses':{'pi':{'timeout':120}},
        'benchmark_config':{},'attempts':1,'max_workers':1,'model':{'name':'fixture'},
        'manifest':{'metrics':[{'type':'sum'}],'tasks':[],'source':'fixture'},
    }
    config=job_config(plan,tmp_path)
    assert config.metrics[0].type.value=='sum'
    assert config.agents[0].max_timeout_sec==120
    assert config.agents[0].override_timeout_sec is None
