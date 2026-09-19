"""Absent usage must not become a comparable zero; it must not fail the run."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from acb.comparison import compare
from acb.config import ModelSpec
from acb.harbor.praxis import HarborPraxis
from acb.harbor.results import import_results
from acb.proxy.base import ProxyTags
from acb.proxy.praxis import PraxisContainerBackend
from acb.usage import parse_measurements


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/messages", "/v1/responses"])
@pytest.mark.parametrize("missing", ["input_tokens", "output_tokens"])
def test_model_usage_requires_explicit_input_and_output(endpoint, missing):
    record = {"endpoint": endpoint, "input_tokens": 0, "output_tokens": 0}
    assert parse_measurements(json.dumps(record)) == ([record], [])
    del record[missing]
    records, errors = parse_measurements(json.dumps(record))
    assert not records
    assert missing in errors[0]
    discovery = {"endpoint": "/v1/models"}
    assert parse_measurements(json.dumps(discovery)) == ([discovery], [])


def test_missing_usage_excludes_tokens_but_preserves_grades(tmp_path):
    plan = {"run_id": "run", "benchmark": "harbor", "proxy": "praxis", "proxy_config": {},
            "model": {"name": "model", "api": "openai", "endpoint": "fixture:8000", "tls": False},
            "benchmark_config": {"reward_metric": "reward", "success_value": 1},
            "harnesses": {"goose": {"timeout": 100}},
            "runtime_contracts": {"task": {"arch": "amd64"}},
            "manifest": {"source": "fixture", "revision": "a",
                         "tasks": [{"id": "task", "sha256": "hash"}]}}

    def collect(name, raw):
        output = tmp_path / name
        artifacts = output / ".harbor/trial/agent/acb"
        artifacts.mkdir(parents=True)

        class Environment:
            async def service_exec(self, *args, **kwargs):
                return SimpleNamespace(return_code=0)

            async def service_download_file(self, source, destination, **kwargs):
                destination.write_text(raw if source.endswith(".jsonl") else "proxy stopped")

        proxy = HarborPraxis(Environment(), plan, "goose", "trial-id", artifacts)
        proxy.started = True
        asyncio.run(proxy.stop())  # Missing usage is not an execution error.
        result = {"id": "trial-id", "task_name": "task", "trial_name": "trial",
                  "config": {"agent": {"kwargs": {"harness": "goose"}}},
                  "verifier_result": {"rewards": {"reward": 1}}}
        import_results(plan, output, [result])
        return output

    valid = '{"endpoint":"/v1/chat/completions","input_tokens":0,"output_tokens":0}\n'
    baseline = collect("baseline", valid)
    candidate = collect("missing", valid + '{"endpoint":"/v1/chat/completions"}\n')
    comparison = compare(baseline, candidate)
    assert comparison["quality"] == "same"
    assert comparison["coverage"]["graded"] == 1
    assert comparison["coverage"]["measured"] == 0
    row = comparison["rows"][0]
    assert row["baseline"]["tokens"] == 0
    assert row["candidate"]["tokens"] is None
    assert comparison["matched_tokens"]["absolute"] is None
    measurement = json.loads((candidate / "goose/instances/trial-id/measurement.json").read_text())
    assert measurement["complete"] is False
    assert measurement["collection_complete"] is True
    assert "missing required fields" in measurement["errors"][0]


def test_legacy_collection_uses_same_validation_without_raising(tmp_path, monkeypatch):
    backend = PraxisContainerBackend(
        tags=ProxyTags("r", "b", "goose", "model", "task"), usage_path=tmp_path / "usage.jsonl",
        config={}, model_spec=ModelSpec("model"), pod="pod", image="image")
    backend._metrics_path = tmp_path / "benchmark_metrics.jsonl"
    backend._log_path = tmp_path / "praxis.log"
    backend._metrics_path.write_text(
        '{"endpoint":"/v1/chat/completions","input_tokens":5,"output_tokens":2}\n'
        '{"endpoint":"/v1/chat/completions"}\n')
    monkeypatch.setattr("acb.proxy.praxis.container_cp_out", lambda *args: None)
    monkeypatch.setattr("acb.proxy.praxis.container_stop_rm", lambda *args: None)
    monkeypatch.setattr("acb.proxy.praxis.container_logs", lambda *args: "proxy stopped")
    backend.stop()
    measurement = json.loads((tmp_path / "measurement.json").read_text())
    assert measurement["complete"] is False
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 1
