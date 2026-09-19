"""Regression checks for failures observed in the live Harbor fixture."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from acb.harbor.transport import HarborTransport
from acb.transport import EnvironmentCommand


class FakeEnvironment:
    def __init__(self):
        self.commands = []
        self.callback = None

    @contextmanager
    def scoped_output_callback(self, callback):
        self.callback = callback
        yield
        self.callback = None

    async def exec(self, command, **kwargs):
        self.commands.append(command)
        if command.startswith('setsid'):
            # setsid forks when invoked as a process-group leader. Without
            # --wait, the container exec can complete before the harness does.
            assert command.startswith('setsid --wait ')
            await self.callback('first\n', 'stdout')
            await asyncio.sleep(0)
            await self.callback('second\n', 'stderr')
            return SimpleNamespace(stdout='', stderr='', return_code=7)
        return SimpleNamespace(stdout='', stderr='', return_code=0)


def test_transport_waits_collects_streams_and_stops_group(tmp_path):
    async def scenario():
        environment = FakeEnvironment()
        transport = HarborTransport(environment, asyncio.get_running_loop())
        result = await asyncio.to_thread(
            transport.execute, EnvironmentCommand(transport, ('bash', '-c', 'exit 7'), {}, '/work'),
            transcript_path=tmp_path / 'transcript.jsonl', timeout=30, describe_event=None,
        )
        assert result.exit_code == 7
        assert result.output == 'first\nsecond\n'
        assert (tmp_path / 'transcript.jsonl').read_text() == result.output
        assert 'kill -TERM' in environment.commands[-1]
        assert 'kill -KILL' in environment.commands[-1]
    asyncio.run(scenario())


def test_copy_log_mounts_preserves_task_data():
    import pytest
    pytest.importorskip('harbor')
    from acb.harbor.environment import copy_log_mounts
    logs = {'type': 'bind', 'source': '/host/logs', 'target': '/logs/agent'}
    data = {'type': 'bind', 'source': '/host/data', 'target': '/workspace/data', 'read_only': True}
    volume = {'type': 'volume', 'source': 'cache', 'target': '/cache'}
    assert copy_log_mounts([logs, data, volume]) == [data, volume]


def test_installation_cancellation_stops_command_before_teardown():
    import pytest
    pytest.importorskip('harbor')
    from acb.harbor.agent import ACBHarborAgent

    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()
        class Environment:
            async def exec(self, command, **kwargs):
                if command.startswith('setsid'):
                    started.set()
                    await stopped.wait()
                    return SimpleNamespace(return_code=137, stdout='', stderr='cancelled')
                if 'acb-setup-' in command and 'kill -TERM' in command:
                    stopped.set()
                return SimpleNamespace(return_code=0)
        agent = object.__new__(ACBHarborAgent)
        agent.plan = {'offline': True}
        agent.transport = HarborTransport(Environment(), asyncio.get_running_loop())
        task = asyncio.create_task(agent._thread(agent.transport.capture, ['sleep', '600']))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert stopped.is_set() and not agent.transport.setup_pidfiles
    asyncio.run(scenario())


def test_podman_frontend_does_not_change_when_docker_compose_is_installed(monkeypatch):
    import pytest
    pytest.importorskip('harbor')
    from acb.harbor.environment import ACBPodmanEnvironment
    monkeypatch.setattr('acb.harbor.environment.shutil.which', lambda name: '/bin/' + name)
    ACBPodmanEnvironment.runtime.cache_clear()
    try:
        runtime = ACBPodmanEnvironment.runtime()
        assert runtime.compose == ('podman-compose', '--in-pod=false')
        assert runtime.supports_compose_project_directory is False
    finally:
        ACBPodmanEnvironment.runtime.cache_clear()


def test_policy_exec_translates_docker_tty_flag_without_changing_command_args(monkeypatch):
    import pytest
    pytest.importorskip('harbor')
    from harbor.environments.podman import PodmanEnvironment
    from acb.harbor.environment import ACBPodmanEnvironment
    commands = []
    async def execute(self, command, **kwargs):
        commands.append(command)
    monkeypatch.setattr('acb.harbor.environment.compose_command', execute)
    environment = object.__new__(ACBPodmanEnvironment)
    asyncio.run(environment._run_docker_compose_command(['exec', '--no-TTY', 'policy', 'allow', 'fixture']))
    asyncio.run(environment._run_docker_compose_command(['exec', '-T', 'main', 'echo', '--no-TTY']))
    assert commands == [['exec', '-T', 'policy', 'allow', 'fixture'],
                        ['exec', '-T', 'main', 'echo', '--no-TTY']]


def test_praxis_startup_failure_keeps_evidence_and_failure_phase(tmp_path):
    import json
    import pytest
    from acb.harbor.praxis import HarborPraxis, PraxisStartupError
    from acb.harbor.results import failure_phase

    class Environment:
        async def service_exec(self, *args, **kwargs):
            return SimpleNamespace(return_code=1)

    plan = {'model': {'name': 'fixture', 'api': 'openai', 'endpoint': 'fixture:18080', 'tls': False},
            'run_id': 'test', 'benchmark': 'fixture', 'proxy_config': {}}
    praxis = HarborPraxis(Environment(), plan, 'goose', 'trial', tmp_path)
    with pytest.raises(PraxisStartupError):
        asyncio.run(praxis.start())
    evidence = json.loads((tmp_path / 'praxis-startup.json').read_text())
    assert evidence['passed'] is False
    assert evidence['error'] == 'failed to configure Praxis sidecar'
    assert failure_phase({}, {'exception_type': 'PraxisStartupError'}) == 'proxy_startup'


def test_praxis_log_streams_bounded_chunks_and_stops(tmp_path):
    import asyncio
    import base64
    from acb.harbor.praxis import HarborPraxis

    source = bytearray(b"first line\n")
    observed = asyncio.Event()

    class Environment:
        async def service_exec(self, command, **kwargs):
            if "tail -c" in command:
                offset = int(command.split("tail -c +", 1)[1].split(" ", 1)[0]) - 1
                observed.set()
                return SimpleNamespace(return_code=0, stdout=base64.b64encode(source[offset:offset + 65536]).decode())
            return SimpleNamespace(return_code=0)

        async def service_download_file(self, source_path, destination, **kwargs):
            destination.write_bytes(source if source_path.endswith("praxis.log") else b"")

    plan = {'model': {'name': 'fixture', 'api': 'openai', 'endpoint': 'fixture:18080', 'tls': False},
            'run_id': 'test', 'benchmark': 'fixture', 'proxy_config': {}}
    praxis = HarborPraxis(Environment(), plan, 'goose', 'trial', tmp_path)

    async def scenario():
        praxis._log_task = asyncio.create_task(praxis._stream_log())
        await asyncio.wait_for(observed.wait(), 1)
        await asyncio.sleep(0)
        assert (tmp_path / 'praxis.log').read_bytes() == b"first line\n"
        source.extend(b"second line\n")
        await asyncio.sleep(1.1)
        assert (tmp_path / 'praxis.log').read_bytes() == b"first line\nsecond line\n"
        await praxis.stop()
        assert praxis._log_task is None

    asyncio.run(scenario())


def test_measurements_preserve_valid_rows_and_report_corruption():
    from acb.harbor.praxis import parse_measurements, is_model_request
    records, errors = parse_measurements(
        '{"endpoint":"/v1/models","input_tokens":0}\n'
        '{"endpoint":"/v1/chat/completions","input_tokens":100,"output_tokens":0}\n'
        '{"endpoint":"/v1/messages/count_tokens","input_tokens":25}\n'
        '{"endpoint":')
    assert len(records) == 3
    assert len(errors) == 1
    assert errors[0].startswith('metrics line 4:')
    assert [row['input_tokens'] for row in records if is_model_request(row)] == [100]


def test_truncated_collection_preserves_usage_and_marks_measurement_incomplete(tmp_path):
    import json
    import pytest
    from acb.harbor.praxis import HarborPraxis

    class Environment:
        async def service_exec(self, *args, **kwargs):
            return SimpleNamespace(return_code=0)

        async def service_download_file(self, source, destination, **kwargs):
            if source.endswith('.jsonl'):
                destination.write_text(
                    '{"endpoint":"/v1/models","input_tokens":0,"output_tokens":0}\n'
                    '{"endpoint":"/v1/chat/completions","input_tokens":100,"output_tokens":20}\n'
                    '{"endpoint":')
            else:
                destination.write_text('proxy stopped')

    plan = {'model': {'name': 'fixture', 'api': 'openai', 'endpoint': 'fixture:18080', 'tls': False},
            'run_id': 'test', 'benchmark': 'fixture', 'proxy_config': {}}
    praxis = HarborPraxis(Environment(), plan, 'goose', 'trial', tmp_path)
    praxis.started = True
    asyncio.run(praxis.stop())
    measurement = json.loads((tmp_path / 'measurement.json').read_text())
    assert measurement['complete'] is False
    assert measurement['non_model_requests'] == 1
    usage = [json.loads(line) for line in (tmp_path / 'usage.jsonl').read_text().splitlines()]
    assert len(usage) == 1
    assert usage[0]['input_tokens'] == 100
    assert usage[0]['output_tokens'] == 20


def test_harness_startup_rejects_crash_and_wrong_version_before_model_work(tmp_path):
    import json
    import pytest
    from acb.harbor.preflight import HarnessStartupError, verify_harness_startup

    class Environment:
        async def exec(self, command, **kwargs):
            assert '--version' in command
            return self.result

    environment = Environment()
    for code, output in [(139, 'Bun segmentation fault'), (0, '2.1.2410 (Claude Code)')]:
        environment.result = SimpleNamespace(return_code=code, stdout=output, stderr='')
        with pytest.raises(HarnessStartupError):
            asyncio.run(verify_harness_startup(environment, 'claude-code', '2.1.241', tmp_path))
        assert json.loads((tmp_path / 'harness-startup.json').read_text())['passed'] is False
    environment.result = SimpleNamespace(return_code=0, stdout='2.1.241 (Claude Code)', stderr='')
    asyncio.run(verify_harness_startup(environment, 'claude-code', '2.1.241', tmp_path))
    assert json.loads((tmp_path / 'harness-startup.json').read_text())['passed'] is True
