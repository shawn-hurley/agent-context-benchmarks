import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from acb.harbor.transport import HarborTransport


def test_podman_uploads_skip_unsupported_compose_cp_and_log_tar_success(monkeypatch):
    from acb.harbor.environment import ACBPodmanEnvironment

    calls, messages = [], []

    class Platform:
        async def _upload_file_with_tar(self, source, target):
            calls.append(('file', source, target))

        async def _upload_dir_with_tar(self, source, target):
            calls.append(('dir', source, target))

    monkeypatch.setattr(ACBPodmanEnvironment, 'runtime',
                        classmethod(lambda cls: SimpleNamespace(supports_compose_cp=False)))
    environment = object.__new__(ACBPodmanEnvironment)
    environment._platform = Platform()
    environment._reported_tar_upload = False
    environment.logger = SimpleNamespace(info=lambda message: messages.append(message))

    async def scenario():
        await environment.upload_file('source.txt', '/work/source.txt')
        await environment.upload_dir('source-dir', '/work/dir')

    asyncio.run(scenario())
    assert calls == [('file', 'source.txt', '/work/source.txt'),
                     ('dir', 'source-dir', '/work/dir')]
    assert messages == ['Compose cp is unavailable; Podman task uploads use tar successfully']


def test_podman_tar_upload_failure_is_not_logged_as_success(monkeypatch):
    from acb.harbor.environment import ACBPodmanEnvironment

    class Platform:
        async def _upload_file_with_tar(self, source, target):
            raise RuntimeError('tar extract failed')

    monkeypatch.setattr(ACBPodmanEnvironment, 'runtime',
                        classmethod(lambda cls: SimpleNamespace(supports_compose_cp=False)))
    environment = object.__new__(ACBPodmanEnvironment)
    environment._platform = Platform()
    environment._reported_tar_upload = False
    messages = []
    environment.logger = SimpleNamespace(info=lambda message: messages.append(message))
    with pytest.raises(RuntimeError, match='tar extract failed'):
        asyncio.run(environment.upload_file('source.txt', '/work/source.txt'))
    assert not messages


def test_cancel_pending_upload_waits_for_transfer_cleanup(tmp_path):
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        class Environment:
            async def upload_file(self, *args):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    await asyncio.sleep(0)
                    cleaned.set()
        transport = HarborTransport(Environment(), asyncio.get_running_loop())
        upload = asyncio.create_task(asyncio.to_thread(transport.upload, tmp_path / 'file', '/file'))
        await asyncio.wait_for(started.wait(), 1)
        transport.closed.set()
        await transport.cancel_pending()
        with pytest.raises(asyncio.CancelledError):
            await upload
        assert cleaned.is_set() and not transport.pending
    asyncio.run(scenario())


def test_cancelled_compose_copy_stops_parent_and_child(tmp_path):
    pytest.importorskip('harbor')
    from harbor.environments.docker.docker import DockerEnvironment
    from acb.harbor.processes import compose_command

    async def scenario():
        marker = tmp_path / 'processes.json'
        script = (
            'import subprocess,sys,time,os,json; from pathlib import Path; '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(300)"]); '
            f'Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid])); '
            'time.sleep(300)'
        )
        environment = SimpleNamespace(
            runtime=lambda: SimpleNamespace(compose=(sys.executable, '-c', script), supports_compose_project_directory=False),
            session_id='fixture', environment_dir=tmp_path, _docker_compose_paths=[],
            _compose_env_vars=lambda **kwargs: os.environ.copy(),
            _collect_buffered_output=DockerEnvironment._collect_buffered_output,
        )
        task = asyncio.create_task(compose_command(environment, ['cp', 'source', 'main:/target']))
        try:
            for _ in range(200):
                if marker.exists():
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('copy subprocess did not start')
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 12)
        import subprocess
        for pid in json.loads(marker.read_text()):
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
            assert result.returncode != 0 or result.stdout.strip().startswith('Z')
    asyncio.run(scenario())
