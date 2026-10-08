"""Upgrade barriers for the private Harbor 0.23 provider APIs used by ACB."""
from importlib.metadata import version
import inspect
from pathlib import Path

from harbor.environments.docker.docker import DockerEnvironment, _sanitize_docker_compose_project_name
from harbor.environments.docker.docker_unix import UnixOps
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from acb.harbor import HARBOR_VERSION


def test_pinned_compose_and_transfer_call_contracts():
    assert version('harbor') == HARBOR_VERSION == '0.23.0'
    for method, required in [
        (DockerEnvironment._collect_buffered_output, {'process', 'timeout_sec', 'stdin_data'}),
        (DockerEnvironment._collect_streamed_output, {'process', 'timeout_sec', 'stdin_data', 'on_output'}),
        (UnixOps._resolve_service_container, {'service'}),
        (UnixOps._upload_file_with_tar, {'source_path', 'target_path'}),
        (UnixOps._upload_dir_with_tar, {'source_dir', 'target_dir'}),
    ]:
        assert required <= set(inspect.signature(method).parameters)
    assert _sanitize_docker_compose_project_name('ACB_fixture__env') == 'acb_fixture__env'


def test_real_provider_exposes_inspection_and_compose_contract_without_engine_work(tmp_path):
    paths = TrialPaths(tmp_path / 'trial')
    paths.mkdir()
    source = tmp_path / 'environment'
    source.mkdir()
    provider = DockerEnvironment(environment_dir=source, environment_name='fixture',
        session_id='fixture__env', trial_paths=paths, task_env_config=EnvironmentConfig(docker_image="fixture:local"))
    assert isinstance(provider._platform, UnixOps)
    assert isinstance(provider._docker_compose_paths, list)
    assert all(isinstance(path, Path) for path in provider._docker_compose_paths)
    variables = provider._compose_env_vars(include_os_env=True)
    assert isinstance(variables, dict)
    assert provider._enable_egress_control is False
    assert provider._EGRESS_CONTROL_SERVICE_NAME


def test_real_job_metric_binding_preserves_builtin_and_replaces_host_script(tmp_path):
    import asyncio
    from harbor.job import Job
    from harbor.models.job.config import JobConfig
    from harbor.metrics.uv_script import UvScript
    from acb.harbor.metrics import bind_container_metrics, ContainerMetric
    script = tmp_path / 'score.py'
    script.write_text('raise RuntimeError("must not execute on controller")')
    task = Path(__file__).resolve().parents[1] / 'config.example/quickstart/tasks/smoke'
    config = JobConfig.model_validate({
        'job_name': 'metric-contract', 'jobs_dir': str(tmp_path), 'quiet': True,
        'agents': [{'name': 'nop'}], 'tasks': [{'path': str(task), 'source': 'fixture'}],
        'metrics': [{'type': 'mean'}, {'type': 'uv-script', 'kwargs': {'script_path': str(script)}}],
    })
    job = asyncio.run(Job.create(config))
    try:
        metrics = next(iter(job._metrics.values()))
        script_metric = next(metric for metric in metrics if isinstance(metric, UvScript))
        builtin = next(metric for metric in metrics if not isinstance(metric, UvScript))
        assert script_metric._script_path == script
        runtime = {'engine': 'podman', 'image_id': 'sha256:fixture'}
        bind_container_metrics(job, {'metric_runtime': runtime})
        rebound = next(iter(job._metrics.values()))
        assert builtin in rebound
        container_metric = next(metric for metric in rebound if isinstance(metric, ContainerMetric))
        assert container_metric.script == script and container_metric.runtime == runtime
        assert not any(isinstance(metric, UvScript) for metric in rebound)
    finally:
        job._close_logger_handlers()
