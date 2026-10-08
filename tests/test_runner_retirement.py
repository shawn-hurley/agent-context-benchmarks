import importlib.util

import pytest

from acb.config import Registries, RunConfig
from acb.resolver import resolve


def test_legacy_execution_is_rejected_during_resolution(tmp_path):
    cfg = RunConfig("retired", {"name": "swebench", "execution_backend": "legacy"}, "goose", "model")
    with pytest.raises(ValueError, match="Harbor is the sole execution backend"):
        resolve(cfg, Registries({}, {}, {}))
    assert not list(tmp_path.iterdir())


def test_retired_modules_are_absent():
    for module in ("acb.runner", "acb.integrations.legacy", "acb.integrations.runtime", "acb.proxy.recording", "acb.proxy.record_server", "acb.harnesses.stubs", "acb.integrations.tamp", "acb.benchmarks.stubs"):
        assert importlib.util.find_spec(module) is None


def test_cli_dispatches_normal_runs_and_controls_to_harbor(monkeypatch):
    from acb.cli import main
    calls = []
    monkeypatch.setattr("acb.harbor.backend.run", lambda cfg, **kwargs: calls.append((cfg, kwargs)))
    for extra in ([], ["--control", "oracle"]):
        main(["run", "--benchmark", "swebench", "--harness", "goose", "--model", "fixture", "--run-id", "dispatch", *extra])
    assert [kw["control"] for cfg, kw in calls] == [None, "oracle"]
    assert all(cfg.benchmark == "swebench" for cfg, kw in calls)
