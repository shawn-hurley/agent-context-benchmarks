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
| `harnesses.yaml` | Versions, timeouts, launch profiles and component defaults |
| `skills.yaml` / `extensions.yaml` | Component definitions/options supplementing the built-in catalog |
| `mcp.yaml` | Named MCP configurations within harness delivery limits |
| `proxy.yaml` | Existing model definitions under `models` and Praxis settings under `backends` |

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
read its value. `proxy: praxis` is the supported measurement route.

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
| Global concurrency | Default 4 → run `max_workers` → `execution.max_workers` |
| Offline | Default false → benchmark `offline` → `execution.offline` |
| Model definition | `models.yaml` resolves alias/wire ID; its fields override `proxy.yaml` fields for that wire ID |

Component lists replace lower layers; `[]` explicitly disables a selection.
Do not declare a top-level list and the same shared list under
`overrides.harness`. Named extensions also conflict with nonempty effective
`execution_integrations`/`model_middleware`; use one configuration route.

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
| Container workdir/interpreter | Path inside the task container |

Host paths support `~`. If no cache is configured, it is `.cache` under the
output directory; a benchmark cache setting overrides the machine cache.
Saving elsewhere re-evaluates relative input paths there, and the editor reviews
those settings before saving.

Runs retain `requested.json` and the effective prepared `resolved.json`, including
sources, aliases, versions and task/runtime identity. Existing run names receive
a suffix. See [operations](harbor-operations.md) for grading/measurement semantics.
Offline mode restricts ACB-controlled downloads; it does not guarantee engine
network isolation.
