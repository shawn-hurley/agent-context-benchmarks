import json
import os
import signal
import subprocess
import sys
import time

import pytest

from acb.harbor.backend import _worker


@pytest.mark.parametrize("kind,message", [
    ("AgentTimeoutError", "Agent execution timed out after 600.0 seconds"),
    ("RuntimeError", "Verifier container failed to start"),
])
def test_worker_exit_surfaces_trial_cause(tmp_path, monkeypatch, kind, message):
    trial = tmp_path / "harbor" / "task-0000__trial"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({"exception_info": {
        "exception_type": kind, "exception_message": message,
    }}))
    evidence = trial / "exception.txt"
    evidence.write_text("traceback")
    class Process:
        def wait(self):
            return 1
    monkeypatch.setattr("acb.harbor.backend.subprocess.Popen", lambda *a, **kw: Process())
    with pytest.raises(RuntimeError) as error:
        _worker("python", "run", tmp_path / "input.json", tmp_path)
    assert kind in str(error.value)
    assert message in str(error.value)
    assert str(evidence) in str(error.value)


def test_worker_forwards_structured_events(tmp_path, monkeypatch):
    observed = []
    launched = {}

    class Process:
        pid = 123
        calls = 0

        def poll(self):
            self.calls += 1
            if self.calls == 1:
                path = tmp_path / '.harbor/events.jsonl'
                path.parent.mkdir(exist_ok=True)
                path.write_text('{"event":"trial-started","trial_id":"id-1"}\n')
                return None
            return 0

    def launch(*args, **kwargs):
        launched.update(kwargs)
        return Process()
    monkeypatch.setattr('acb.harbor.backend.subprocess.Popen', launch)
    monkeypatch.setattr('acb.harbor.backend.time.sleep', lambda *_: None)
    _worker('python', 'run', tmp_path / 'input.json', tmp_path, on_event=observed.append)
    assert observed == [{'event': 'trial-started', 'trial_id': 'id-1'}]
    assert launched['stdout'].name == str(tmp_path / '.harbor/worker.log')
    assert launched['stderr'] == __import__('subprocess').STDOUT
    assert (tmp_path / '.harbor/worker.log').is_file()


@pytest.mark.parametrize("content", ["{", "null", "[]", '{"exception_info": null}'])
def test_unreadable_or_missing_cause_preserves_worker_exit(tmp_path, content):
    from acb.harbor.backend import _worker_failure
    trial = tmp_path / "harbor" / "trial"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(content)
    message = _worker_failure("probe", 7, tmp_path)
    assert "worker exit 7" in message
    assert "No trial failure details were recorded" in message
    assert "AgentTimeoutError" not in message


@pytest.mark.parametrize("malformed", [True, False])
def test_event_failure_stops_and_reaps_real_worker(tmp_path, monkeypatch, malformed):
    launched = []
    popen = subprocess.Popen

    def launch(command, **kwargs):
        script = ("import os,time,pathlib; "
                  "pathlib.Path(os.environ['ACB_HARBOR_EVENT_PATH']).write_text(" +
                  repr('bad json\n' if malformed else '{"event":"test"}\n') +
                  "); time.sleep(60)")
        process = popen([sys.executable, "-c", script], **kwargs)
        launched.append(process)
        return process

    def callback(event):
        raise RuntimeError("display failed")

    monkeypatch.setattr("acb.harbor.backend.subprocess.Popen", launch)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            _worker(sys.executable, "run", tmp_path / "input.json", tmp_path, on_event=callback)
        assert launched[0].returncode is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(launched[0].pid, 0)
    finally:
        if launched and launched[0].poll() is None:
            os.killpg(launched[0].pid, signal.SIGKILL)
            launched[0].wait()


def test_shutdown_escalates_unresponsive_worker(tmp_path):
    from acb.harbor.backend import _stop_worker
    ready = tmp_path / "ready"
    script = ("import signal,time,pathlib; "
              "signal.signal(signal.SIGINT,signal.SIG_IGN); "
              "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
              f"pathlib.Path({str(ready)!r}).touch(); time.sleep(60)")
    process = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        _stop_worker(process, timeouts=(.05, .05, 5))
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_repeated_interrupt_during_shutdown_keeps_escalating(monkeypatch):
    from acb.harbor.backend import _stop_worker
    signals = []

    class Process:
        pid = 123
        calls = 0

        def wait(self, timeout):
            self.calls += 1
            if self.calls < 3:
                raise KeyboardInterrupt()
            return -signal.SIGKILL

    monkeypatch.setattr("acb.harbor.backend.os.killpg", lambda pid, sig: signals.append(sig))
    _stop_worker(Process())
    assert signals[:3] == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]
