# Design: agent-context-benchmarks (acb)

Measure and compare **context/token usage** of different agent harnesses
(claude-code, goose, opencode, pi, …) running the same coding benchmarks
(SWE-bench, LiveCodeBench, ScarfBench, …) against the same model — cloud or
local.

## Core invariant

> The harness only ever talks to the **proxy**. The proxy is the only thing that
> connects to a model.

The runner hands each harness `base_url = <proxy>` and a placeholder key. The
harness never receives a provider URL or a real key. Every LLM call therefore
passes through the proxy, which records it. Model backends — including local
ones — are declared in the proxy config and owned by the proxy.

## Two phases

```
                 ┌──────────────── generation (measured) ─────────────────┐
Benchmark ─▶ Instance ─▶ container ─▶ Harness ──http──▶ Proxy ──▶ Model
                                         │                 │       (cloud/local)
                                         │                 └▶ usage.jsonl (per request)
                                         ▼
                                    git diff / output ─▶ Prediction
                 └────────────────────────────────────────────────────────┘
                 ┌──────────────── evaluation ────────────────────────────┐
   predictions ─▶ Benchmark.evaluate ─▶ {instance_id: resolved}
                 └────────────────────────────────────────────────────────┘
   usage.jsonl + resolved ─▶ Report (metrics.jsonl + report.json)
```

## Why a per-instance proxy, tagged at launch

CLI harnesses can't inject custom per-request headers, so a shared proxy can't
attribute requests to instances. Instead the runner launches **one proxy per
instance run**, bound to an ephemeral port, tagged with
`(run, harness, model, benchmark, instance_id)`. Every request it sees belongs
to that instance; arrival order is the `turn_index`. Harness-agnostic, and it
isolates concurrency cleanly.

## The atomic measurement: one row per LLM request

`usage.jsonl` (`acb/usage.py:UsageRecord`) holds one row per LLM call with
`input / output / cache_read / cache_creation` tokens plus `turn_index`. All
requested metrics are **derived** from this stream (no re-runs to add a metric):

| Metric              | Derivation                                            |
|---------------------|-------------------------------------------------------|
| Total tokens        | sum of all token fields                               |
| Peak context        | `max(input + cache_read + cache_creation)` over turns |
| Per-turn growth     | prompt size ordered by `turn_index`                   |
| Cache efficiency    | `cache_read / total prompt tokens sent`               |

Cache fields are cloud-only; local OpenAI-compatible backends record `0`.

## Proxy backends (pluggable)

Both implement `acb/proxy/base.py:ProxyBackend` and emit the same `UsageRecord`.
Only `praxis` has a container-mode implementation (`PraxisContainerBackend`);
generation is container-only (see "Container-mode generation" below), so
`recording` currently can't be selected for a real run.

- **`praxis`** (primary) — the Praxis Rust gateway
  (https://github.com/praxis-proxy/praxis). Per run we generate a YAML:
  `access_log` + `request_id` observability, model routed to the single owned
  backend cluster, `credential_injection` from the backend's `key_env`
  (skipped for keyless local). Praxis stdout/access-log is parsed into usage
  rows.
  *Known limitations (verified against the real 0.5.3 binary, see
  `acb/proxy/praxis.py`'s module docstring):* no `anthropic_to_openai`
  translation filter exists -- harness and model API must match, or
  `build_config()` raises early; no `token_usage_headers` filter either, so
  usage is parsed straight out of whatever fields real access-log lines
  contain (`TOKEN_FIELD_CANDIDATES`) -- *open item:* confirm those field names
  on a real run and adjust as needed.
- **`recording`** (fallback / cross-check, host-mode only) — a
  zero-dependency stdlib proxy that forwards to the backend and parses
  Anthropic SSE / OpenAI usage directly. Same-API only (no cross-API
  translation). No container-mode implementation yet.

## Container-mode generation

The harness always runs inside the same eval image evaluation will grade the
patch in, not a plain host checkout -- so its dev environment (Python
version, installed deps, OS) matches evaluation exactly instead of whatever
happens to be on the machine running `acb`. Per instance, `acb/runner.py`
creates a Podman **pod** (shared network namespace) holding two sibling
containers: the benchmark's testbed (e.g. SWE-bench's per-instance eval
image, built/resolved on demand -- `acb/benchmarks/image_builder.py`) and a
Praxis proxy instance (`PraxisContainerBackend`). The harness reaches Praxis
over the pod's shared loopback; Praxis reaches the host's model server via
Podman's `host.containers.internal` gateway. See the README's "SWE-bench
generation" section for the full picture and how to build the images by hand.

Only `goose` has a container-mode port today (`HarnessAdapter.run_container()`)
-- it ships a single static-ish Linux binary that's trivial to `podman cp`
into the container. `claude-code`/`opencode`/`pi` are stubs
(`acb/harnesses/stubs.py`) pending their own port (claude-code's CLI is a
Node.js package, not a standalone binary, so it needs more than a binary
copy).

## Model registry (owned by the proxy)

`config/proxy.yaml` `models:` maps a model name → `{api, endpoint, tls, key_env,
reports_cache}`. A run just names a model; the proxy resolves it to a backend it
connects to. Local models are OpenAI-compatible (vLLM/Ollama/LM Studio), keyless,
http, no cache reporting.

## Extending

- **New harness** — add an adapter in `acb/harnesses/` (implement
  `run_container()`, and `build_container_env()` if it doesn't use the
  standard base-url env vars) + a registry entry. See
  `acb/harnesses/goose.py` for a worked example.
- **New benchmark** — add an adapter in `acb/benchmarks/` implementing
  `load_instances` / `prepare_container` / `collect_prediction_container` /
  `evaluate`. Everything else (proxy, measurement, reporting) is shared.
- **New metric** — re-aggregate `usage.jsonl` in `acb/report.py`; no re-runs.
