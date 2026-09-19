"""Bridge synchronous ACB adapters onto Harbor's asynchronous environment API."""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
from pathlib import Path
import shlex
import threading
import uuid

from acb.harnesses.base import HarnessResult
from acb.transport import EnvironmentCommand


class HarborTransport:
    def __init__(self, environment, loop):
        self.environment = environment
        self.loop = loop
        self.closed = threading.Event()
        self.pidfile = "/tmp/acb-agent-process.pid"
        self.setup_pidfiles = set()
        self.pending = set()

    def call(self, coroutine):
        if self.closed.is_set():
            coroutine.close()
            raise CancelledError("Harbor trial was cancelled")
        async def guarded():
            if self.closed.is_set():
                coroutine.close()
                raise asyncio.CancelledError()
            task = asyncio.current_task()
            self.pending.add(task)
            try:
                return await coroutine
            finally:
                self.pending.discard(task)
        return asyncio.run_coroutine_threadsafe(guarded(), self.loop).result()

    async def cancel_pending(self):
        tasks = list(self.pending)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def capture(self, argv, workdir=None):
        async def run():
            pidfile = "/tmp/acb-setup-" + uuid.uuid4().hex + ".pid"
            self.setup_pidfiles.add(pidfile)
            payload = f"echo $$ > {pidfile}; exec {shlex.join(argv)}"
            try:
                return await self.environment.exec("setsid --wait bash -c " + shlex.quote(payload),
                                                   cwd=workdir, timeout_sec=120)
            finally:
                try:
                    await self._kill_group(pidfile)
                finally:
                    self.setup_pidfiles.discard(pidfile)
        result = self.call(run())
        if result.return_code:
            raise RuntimeError(f"environment command failed ({result.return_code}): {result.stderr or result.stdout}")
        return result.stdout or ""

    def service_capture(self, service, argv):
        result = self.call(self.environment.service_exec(shlex.join(argv), service=service, timeout_sec=60))
        if result.return_code:
            raise RuntimeError(f"service {service} failed: {result.stderr or result.stdout}")
        return result.stdout or ""

    def service_download(self, service, source, destination, directory=False):
        method = self.environment.service_download_dir if directory else self.environment.service_download_file
        self.call(method(source, destination, service=service))

    def stop_service(self, service):
        self.call(self.environment.stop_service(service))

    def upload(self, source: Path, destination: str):
        if source.is_dir():
            self.call(self.environment.upload_dir(source, destination))
        else:
            self.call(self.environment.upload_file(source, destination))

    def download(self, source: str, destination: Path):
        if self.call(self.environment.is_dir(source)):
            self.call(self.environment.download_dir(source, destination))
        else:
            self.call(self.environment.download_file(source, destination))

    async def kill_agent(self):
        # Kill the process group inside the environment, not only Compose's client.
        for pidfile in [self.pidfile, *self.setup_pidfiles]:
            await self._kill_group(pidfile)

    async def _kill_group(self, pidfile):
        result = await self.environment.exec(
            f"p=$(cat {pidfile} 2>/dev/null) || exit 0; "
            "case \"$p\" in ''|*[!0-9]*) exit 1;; esac; "
            "if kill -0 -- -\"$p\" 2>/dev/null; then kill -TERM -- -\"$p\" 2>/dev/null || true; "
            "sleep 1; kill -KILL -- -\"$p\" 2>/dev/null || true; fi; "
            f"rm -f {pidfile}",
            timeout_sec=10,
        )
        if result.return_code:
            raise RuntimeError("failed to stop the agent process group")

    def execute(self, command: EnvironmentCommand, *, transcript_path, timeout,
                describe_event, tracker=None, tracker_key=None):
        async def run():
            from acb.harnesses._streaming import ANSI_ESCAPE_PATTERN
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            chunks = []
            with transcript_path.open("w") as output:
                async def callback(text, stream):
                    clean = ANSI_ESCAPE_PATTERN.sub(b"", text.encode()).decode(errors="replace")
                    chunks.append(clean)
                    output.write(clean)
                    output.flush()
                payload = f"echo $$ > {self.pidfile}; exec {shlex.join(command.argv)}"
                shell = "setsid --wait bash -c " + shlex.quote(payload)
                try:
                    with self.environment.scoped_output_callback(callback):
                        result = await self.environment.exec(shell, cwd=command.workdir,
                                                             env=command.env, timeout_sec=timeout)
                    if not chunks:
                        await callback((result.stdout or "") + (result.stderr or ""), "stdout")
                    return HarnessResult("".join(chunks), result.return_code)
                except (TimeoutError, asyncio.TimeoutError):
                    return HarnessResult("".join(chunks), -1, True)
                finally:
                    await self.kill_agent()
        return self.call(run())
