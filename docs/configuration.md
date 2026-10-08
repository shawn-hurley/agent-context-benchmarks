# Run configuration reference

One YAML file describes an experiment. Registries define the available models,
benchmarks and components; the resolver combines them with packaged defaults.
Start with `acb init experiment --template quickstart`, then edit its `run.yaml`
or load it in the [interactive editor](interactive-run.md).

## Run file

This example uses the quickstart's generated registries and task:

```yaml
schema_version: 2
config_dir: config
run_id: baseline
benchmark: smoke
harness: [goose, pi]
model: quickstart-model
subset: [smoke]
skills: []
extensions: []
mcp_servers: []
execution:
  max_workers: 1
  timeout: 300
output_dir: runs/experiment
```

Schema 2 is the default and the only supported version. `harness` accepts one
name or a list. `subset` is a nonempty list of unique IDs; omit it to select all
eligible tasks. `limit` is a positive task cap per harness. Attempts come from
benchmark `attempts`. Concurrency limits trials across the whole run, including
all harnesses and attempts; it is separate from the task cap.

Select staged instructions with `workflow: bundled-name` or a directory path.
See the [workflow authoring guide](workflows.md).

```sh
acb resolve --config experiment/run.yaml
acb tasks --config experiment/run.yaml
acb prepare --config experiment/run.yaml
acb run --config experiment/run.yaml
```

`resolve` validates without engine/model requests. `tasks` reads local/cached
IDs unless `--download` is supplied. `prepare` checks engine/Compose and prepares
assets without model requests. Ordinary execution contacts the provider.
`--config` cannot be mixed with run selection flags; edit the YAML instead.
CLI `--config-dir` may override the registry directory.

## Registries and models

Registry files are optional except for definitions needed by the selected
experiment. An absent harness registry uses packaged exact version defaults.

| File | Contents |
| --- | --- |
| `models.yaml` | Model aliases, provider IDs, API, endpoint and credential environment references |
| `machine.yaml` | `environment: docker` or `podman`, and `cache_dir` |
| `benchmarks.yaml` | Task source, revision, grading, attempts and native grader settings |
| `harnesses.yaml` | Versions, timeouts and named component defaults |
| `skills.yaml` / `extensions.yaml` | Component definitions/options supplementing the built-in catalog |
| `mcp.yaml` | Named MCP configurations within harness delivery limits |

For example, `config/models.yaml` can define:

```yaml
quickstart-model:
  model: YOUR_MODEL_ID
  api: openai
  endpoint: api.openai.com:443
  tls: true
  key_env: OPENAI_API_KEY
```

The alias selects a definition; `model` is the ID sent to the provider. `endpoint`
is a host and optional port, without a scheme or API path. Use `api: anthropic`
for that API. A local compatible server can use `tls: false` and omit `key_env`.
Credentials stay in the named environment variable; review/resolution does not
read its value. Harbor always executes trials; Praxis always captures model traffic.

Without an explicit directory, registry discovery starts beside the loaded YAML
(or at the invocation directory for a new draft). It walks ancestors and checks
`.acb/` before `config/` at each level, selecting a directory containing a known
registry file. `acb list CATEGORY --config-dir DIR` lists definitions.
`resolve --json` shows the complete plan and loaded sources.

## Precedence and component selections

Precedence runs from left to right:

| Setting | Resolution order |
| --- | --- |
| Benchmark | Packaged defaults → registry → structured `benchmark: {name: ..., ...}` → `overrides.benchmark` |
| Harness scalars | Packaged defaults → registry → `overrides.harness` → `overrides.harnesses.NAME` |
| Harness timeout | Harness precedence, then `execution.timeout` applies to every selected harness |
| Shared components | Harness registry → shared override or top-level selection → per-harness selection |
| Component options | Catalog defaults → selection options; versions must match the catalog |
| Engine | Default Podman → machine → benchmark → `execution.environment` |
| Global concurrency | Default 4 → `execution.max_workers` |
| Offline | Default false → benchmark `offline` → `execution.offline` |
| Model definition | `models.yaml` provides the complete alias definition and optional wire ID |

Component lists replace lower layers; `[]` explicitly disables a selection.
Do not declare a top-level list and the same shared list under
`overrides.harness`. Only named component selections are accepted. Put custom skill and MCP definitions
in `skills.yaml` and `mcp.yaml`; select their names in run/harness configuration.
Resolved integration arrays are internal worker inputs.

Use `skills: [caveman]` for response instructions and `extensions: [rtk]` for
shell-tool integration. Caveman middleware is a separate extension. See
[skills and integrations](integrations.md) for examples and behavior.

Per-harness overrides can vary settings without copying the whole configuration:

```yaml
overrides:
  harnesses:
    goose:
      timeout: 600
    pi:
      timeout: 900
```

Remove `execution.timeout` from the earlier example to use those separate
values. Resolution validates names, known fields, versions, list conflicts and
positive limits. Asset bytes and task/runtime compatibility are checked during
preparation. Pi rejects MCP delivery; see the
[feature examples](../config.example/feature-tests/README.md) for supported cases.

## Paths and saved evidence

| Path | Relative base |
| --- | --- |
| CLI paths, including `--config-dir` | Invocation directory |
| YAML `config_dir`, custom workflow directory | Run YAML directory |
| Run `output_dir` | Invocation directory |
| Machine `cache_dir` | `machine.yaml` directory |
| Benchmark task/data/cache paths | Declaring `benchmarks.yaml`, or run YAML for run overrides |
| Skill/RTK host assets | Declaring catalog, registry or run YAML |
| Bare grader command names | Controller PATH; explicit paths use their declaring file |
| Dataset `task_root` | Prefix inside the dataset, not a host path |
| Harness workdir / RTK option `python_path` | Path inside the task container |

Host paths support `~`. If no cache is configured, it is `.cache` under the
output directory; a benchmark cache setting overrides the machine cache.
Saving elsewhere re-evaluates relative input paths there, and the editor reviews
those settings before saving.

Runs retain `requested.json` and the effective prepared `resolved.json`, including
sources, aliases, versions and task/runtime identity. Existing run names receive
a suffix. See [operations](harbor-operations.md) for grading/measurement semantics.
Offline mode restricts ACB-controlled downloads; it does not guarantee engine
network isolation.

## Applicable controls and migration

`execution.max_workers` is the only YAML concurrency control (default four).
CLI `--max-workers` writes this execution setting. Claude Code alone accepts
`max_budget_usd`; Goose alone accepts `max_tool_repetitions`. A shared override
must apply to every selected harness; use `overrides.harnesses.NAME` otherwise.
Claude always launches with isolated explicit settings and hook support.

Native ScarfBench settings include `benchmark_cache_dir`, `source`, `target`,
`instances`, `scarf_binary`, validation timeout and Maven cache controls. Native
SWE-bench settings include dataset/revision/split, task repositories,
`swebench_python`, image architecture and patch exclusions. Selecting existing
Harbor tasks with `path` bypasses native export and grading, so native-only
settings are rejected. The pinned RH dataset accepts `task_root`; Harbor
registry/package datasets accept `registry` and `version`. Explicit fields are
validated at every layer, including fields replaced by later overrides.

For older YAML, move model definitions from `proxy.yaml` into `models.yaml`,
materializing any endpoint, API, TLS, key environment and Vertex settings that
aliases inherited. Keep provider IDs in `model` and pricing keys unchanged.
Move inline skill/MCP definitions into their registries. Replace internal RTK or
Caveman arrays with named `extensions` and their supported `options`.
Remove runtime/proxy selectors, host harness binaries, harness model/tuning
fields and `execution.cache_policy`; the latter never reset provider caches.
Custom Praxis builds use `benchmark.praxis_image`.

YAML remains schema 2. Prepared worker plans use protocol 2; re-prepare old plans.
Workers reject unsupported protocol versions before creating output, volumes or
provider services. Cache behavior in saved provenance describes the implemented
provider-managed behavior, with no reset operation. The full decisions are in
[the runtime cleanup record](runtime-cleanup.md).
