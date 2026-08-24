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
**Known gap:** even against a real cache-using Anthropic backend, Praxis-based
runs never populate them either (always `0`) -- praxis-ai's `token_count`
filter folds cache-read/cache-creation counts into a single combined `input`
total and discards the breakdown before it's ever logged (confirmed by
reading its real source). `cache_efficiency` in `report.json` therefore
always reads `0.0` for a Praxis-based run regardless of real cache reuse; see
`acb/proxy/praxis.py`'s `_parse_usage_log()` docstring. Raw token *totals*
aren't affected, only this one ratio metric.

## Proxy backends (pluggable)

Both implement `acb/proxy/base.py:ProxyBackend` and emit the same `UsageRecord`.
Only `praxis` has a container-mode implementation (`PraxisContainerBackend`);
generation is container-only (see "Container-mode generation" below), so
`recording` currently can't be selected for a real run.

- **`praxis`** (primary) — container-mode always runs
  [praxis-ai](https://github.com/praxis-proxy/ai), a superset gateway that
  registers core [praxis-proxy/praxis](https://github.com/praxis-proxy/praxis)'s
  own filters plus AI-specific ones -- not the core `praxis` binary alone,
  which lacks both features this project actually needs. Per run we generate
  a YAML: `access_log` + `request_id` observability, `token_count` (real
  per-request token accounting -- core praxis has no such filter at all),
  model routed to the single owned backend cluster, `credential_injection`
  from the backend's `key_env` (skipped for keyless local). When the harness
  and backend speak different APIs -- today, only claude-code (Anthropic)
  against an `api: openai` local model -- `anthropic_messages_format` /
  `anthropic_to_openai` / `anthropic_stream_events` translate between them;
  every other API mismatch still makes `build_config()` raise early (the
  reverse direction, an OpenAI-speaking harness against a real Anthropic
  backend, isn't wired up since no current harness needs it).
  *Real, live-verified gaps in praxis-ai itself (see
  `acb/proxy/praxis.py`'s module docstring for the full detail):*
  `token_count` + `access_log` in the wrong declared order crashes every
  POST request (response-phase hooks run in reverse declared order); its
  documented `token_usage_headers` mechanism never actually fires for any
  response (streaming or not) due to a phase-ordering bug in praxis-ai
  itself -- worked around by parsing `token_count`'s own DEBUG-level log
  line directly, correlated with `access_log` by `request_id`.
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
praxis-ai proxy instance (`PraxisContainerBackend`). The harness reaches
praxis-ai over the pod's shared loopback; praxis-ai reaches the host's model
server via Podman's `host.containers.internal` gateway, or the real
Anthropic/OpenAI API directly for cloud models. See the README's "How
generation works" section for the full picture and how to build the images
by hand.

Staging a harness's own runtime into the container (e.g. `podman cp`-ing in
a binary) is each harness's own job via `HarnessAdapter.setup_container()`,
called by the runner after the benchmark's `prepare_container()` returns and
before `run_container()` -- kept out of `Benchmark.prepare_container()`
itself so benchmark code stays agnostic to which harness is running (this
used to be a `goose_binary: Path` param baked into that method's signature;
it didn't survive a second container-mode harness needing a different
binary entirely).

`goose` and `claude-code` both have a container-mode port today
(`HarnessAdapter.run_container()`) -- goose ships a single static-ish Linux
binary, and claude-code -- despite this project's own earlier assumption
otherwise -- turns out to ship a standalone per-arch native executable too
(`@anthropic-ai/claude-code-linux-{arm64,x64}` on the public npm registry,
not a Node.js package needing a Node runtime; see
`acb/harnesses/claude_code.py`'s module docstring for how that was
confirmed). Both are trivial to `podman cp` into the container the same way.
`opencode`/`pi` are still stubs (`acb/harnesses/stubs.py`) pending the same
investigation.

## Model registry (owned by the proxy)

`config/proxy.yaml` `models:` maps a model name → `{api, endpoint, tls, key_env,
reports_cache}`. A run just names a model; the proxy resolves it to a backend it
connects to. Local models are OpenAI-compatible (vLLM/Ollama/LM Studio), keyless,
http, no cache reporting.

## Extending

- **New harness** — add an adapter in `acb/harnesses/` (implement
  `run_container()`; `setup_container()` if it needs its own binary staged
  into the container; `build_container_env()` if it doesn't use the
  standard base-url env vars) + a registry entry. See
  `acb/harnesses/goose.py` and `acb/harnesses/claude_code.py` for worked
  examples (the latter also shares stdout-tailing/heartbeat plumbing from
  `acb/harnesses/_streaming.py`, worth reusing for a third).
- **New benchmark** — add an adapter in `acb/benchmarks/` implementing
  `load_instances` / `prepare_container` / `collect_prediction_container` /
  `evaluate`. Everything else (proxy, measurement, reporting) is shared.
- **New metric** — re-aggregate `usage.jsonl` in `acb/report.py`; no re-runs.
