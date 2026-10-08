# Feature test configurations

A clean Harbor run matrix, shared by all four harnesses. Each run explicitly
selects its treatments; `harnesses.yaml` inherits the packaged harness versions
without old custom prompts. No baseline silently enables skills, MCP or extensions.

## Start here

From the repository root, install this template into local `config/`:

```sh
uv run python -m scripts.reset_config
uv run pytest tests/test_feature_configs.py -q
acb resolve --config config/runs/baseline.yaml
acb prepare --config config/runs/baseline.yaml
acb run --config config/runs/baseline.yaml --control oracle
acb run --config config/runs/baseline.yaml --control nop
```

The reset archives the complete previous `config/` under `.config/backups/` before
installing the new matrix. It preserves model definitions and optional prices,
uses the shared `runs/.cache`, and reuses locally available ScarfBench/RH inputs
and the cached ARM64 rgctl binary when present. It does not copy old harness
prompts or experiment overrides. Restore by moving the new config aside and moving
the archived directory back to `config/`.

Edit `models.yaml` for the model currently served at your endpoint. The template
uses the previously used local Qwen model name; it does not discover the running
server. Provider credentials belong in environment variables. No model requests
are made by reset, resolve or the automated configuration tests. `prepare` may
build/download images and binaries. Normal `run` commands contact the selected
model and may incur provider costs; oracle/nop controls do not.

## Coverage

`matrix.yaml` is the inventory. Run configurations are under `runs/`:

| Config(s) | Exercises |
| --- | --- |
| `baseline`, `baseline-<harness>` | All four harnesses together or independently; proxy accounting and reports |
| `rtk` | Prepared RTK integration across all four harnesses |
| `response-lite`, `response-full`, `response-ultra` | Caveman response instructions at each supported intensity |
| `local-skill`, `task-skill`, `rgctl` | Configured local instructions, task-provided instructions, binary-bearing skill |
| `caveman-record`, `caveman-compress` | Context service modes and recovery capability |
| `combined`, `combined-response` | RTK + context compression, optionally response instructions |
| `multi-step`, `multi-step-combined` | Workspace continuity and treatment lifecycle across two agent steps |
| `attempts` | Two attempts per task/harness |
| `numeric-metrics`, `custom-metrics` | Numeric reward dictionaries, native aggregates, custom metric script |
| `separate-verifier` | Independent verifier filesystem and credential isolation (not solution grading) |
| `resources`, `network-none` | Explicit CPU/memory limits and agent network policy |
| `docker`, `offline` | Alternate engine and prepared-cache-only operation |
| `mcp` | Filesystem stdio MCP on Goose, OpenCode and Claude Code |
| `verifier-crash`, `missing-reward`, `malformed-reward`, `verifier-timeout` | Normal failed/missing verification outcomes and error evidence |
| `swebench`, `swebench-lite`, `scarfbench`, `rh-swe-bench` | One selected native/registry benchmark instance per harness |

Smoke tasks have no dependency on old run output. Their image installs Python,
Git, curl, Bash, Node and npm from normal package sources. Oracle writes the
known solution and nop leaves it broken; expected smoke rewards are 1 and 0.
The separate-verifier case rewards successful isolation even under nop.

The matrix exposes features; selecting a treatment is not evidence of model-driven
compression, recovery or tool use. Use the deterministic integration checks in
[scripts/README.md](../../scripts/README.md) for those assertions. Cancellation,
service loss, accounting completeness, partial-result recovery, snapshot/cache
integrity and concurrency are covered by the automated/lifecycle test suite;
they are not expressible as a positive run YAML alone.

## Prerequisites and limits

- `rgctl`: place a **Linux binary for the task architecture** at
  `assets/skills/rgctl/rgctl`. The local reset reuses the known cached ARM64 asset
  on ARM64 hosts. The tracked template intentionally does not contain binaries.
- `mcp`: requires npm registry access to start the filesystem server via `npx`.
  Pin its package version before recording reproducible experimental results.
  Pi is deliberately excluded: its adapter rejects MCP runtime delivery.
- `offline`: run `prepare` online on `baseline` first. Caches and engine images
  must exist. This setting does not guarantee the container engine performs no
  network activity during its own setup.
- Native benchmarks require their datasets, image recipes and graders; see
  [benchmark migration](../../docs/harbor-benchmark-migration.md). Point ScarfBench's
  `benchmark_cache_dir` at an existing bundle. RH defaults to the pinned upstream
  dataset; unsupported disk quotas need an explicitly reviewed task copy. Local
  reset copies the previously approved bundle and deviation record into
  `assets/benchmarks/rh-swe-bench` when available; no new quota override is applied.
- Docker has [scoped Quick Start evidence](../../docs/validation/quick-start-validation.md)
  for the repair task and native Cart controls. The full Docker feature/harness
  and isolation matrix remains unverified.
- Verifier failures are expected outcomes, normally with a successful ACB process
  exit. Infrastructure/setup failures and cancellation still fail execution.

## Compare treatments

Run baseline and treatment sequentially against the same model and task inputs:

```sh
acb run --config config/runs/baseline.yaml
acb run --config config/runs/combined.yaml
acb compare runs/feature-tests/baseline runs/feature-tests/combined --html runs/feature-tests/comparison.html
```

If run names already exist, use the actual reserved `-N` output paths. Output paths
are relative to the invocation directory. Each config uses a dedicated
`runs/feature-tests` output root so it does not mix with earlier experiments.
