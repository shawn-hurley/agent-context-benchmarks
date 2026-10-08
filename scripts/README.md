# Maintenance and live checks

Current scope and remaining maintenance work: [cleanup record](../docs/harbor-cleanup.md).

The Python files named `check_*.py` are opt-in integration checks. They exercise
containers, cached harness binaries, local fixture services, or a prepared
Harbor plan and are intentionally excluded from the normal `pytest tests` run.
Each command requires a new output directory and retains a machine-readable
`check.json` where applicable.

Maintained capability checks include:

- Harbor accounting, cancellation, network phases, verifier isolation, service
  images, skill delivery, rgctl delivery, Caveman, and treatment composition.
- Dataset metrics in `check_harbor_dataset_metrics.py` and multi-step execution
  in `check_harbor_multistep.py`.
- Download cancellation, provider-image caching, and Caveman store isolation.
- Benchmark migration in `check_harbor_benchmark_migration.py`: model-free
  SWE-bench/ScarfBench Harbor controls and an official SWE-bench parity matrix.
  See [setup and evidence](../docs/harbor-benchmark-migration.md).

The Phase 4 pilot and legacy-runner checks were retired. Their frozen evidence
remains historical; maintained lifecycle and treatment checks use Harbor.

## Required local regression gate

```sh
uv sync --locked --group dev
uv run pytest tests -q
uv build --out-dir dist
uv run python scripts/check_package.py dist/agent_context_benchmarks-0.1.0-py3-none-any.whl
```

`uv build` builds the wheel from a fresh source distribution. This avoids stale
modules left in a previous build directory. The package check installs the wheel
into a temporary directory and checks imports/resources, packaged starter
creation and ScarfBench export from outside the checkout. It makes no model or
container requests.

For live recovery-store isolation, build the pinned Caveman image and run:

```sh
podman build -t acb-caveman-check -f acb/integrations/assets/caveman/Containerfile acb/integrations/assets/caveman
uv run python -m scripts.check_caveman_store_isolation acb-caveman-check runs/store-isolation-check
```

This starts two Harbor environments, checks exact recovery in the originating
store and rejection in the other, then deletes both environments. `check.json`
records session names, results and cleanup errors. It uses no model.

The shell scripts are user-facing smoke or example runners and remain supported.

For persistent ScarfBench Maven downloads and its model-free container check, see
[cache configuration and verification](../docs/scarfbench-maven-cache.md).

## Reset local feature configurations

`uv run python -m scripts.reset_config` installs
[`config.example/feature-tests`](../config.example/feature-tests/README.md) into
`config/`, archiving the old tree under `.config/backups/`. The local copy retains
current model settings, the selected engine, costs and a valid external
ScarfBench prerequisite directory. Supply RH task bundles and rgctl binaries
through explicit current settings; reset does not search historical runs or
import retired proxy/phase6 registries. The tracked
template contains no credentials or compiled binaries.

`uv run pytest tests/test_feature_configs.py -q` validates every matrix config,
local task export, custom metrics, and backup/reset behavior without model calls
or container startup. Use the feature guide for controls and live prerequisites.

## Fresh-clone Quick Start check

See [Quick Start](../docs/quick-start.md) for a dedicated starter configuration
and publication prerequisites for the corrected ScarfBench fork.

Public-clone acceptance requires the documented ACB revision and pinned benchmark
revision to be available from their public repositories. Verify both before
recording a public-download result; local assets only establish local acceptance.

```sh
uv run python scripts/check_quickstart.py /tmp/acb-quickstart-check --environment podman
uv run python scripts/check_quickstart.py /tmp/acb-quickstart-live --environment podman \
  --live --model-config config/quickstart/models.yaml
```

Each output directory must be new. The first command runs model-free oracle/nop
controls. `--live` adds exactly one model-driven task and may incur provider
costs. `--scarf` additionally verifies public download of the pinned fork and
native Cart controls; `--scarf-benchmark` explicitly uses local assets instead.
`check.json` retains grades, coverage, tool tracking, timings, and offline bundle
link checks. Cold-install validation needs a separate empty engine store; these
checks never prune existing experiment caches.

## Canonical Praxis and workflow sources

Praxis build sources are under `acb/assets/praxis`:

```sh
cargo test --locked --offline --manifest-path acb/assets/praxis/praxis-vertex-anthropic/Cargo.toml
podman build -t acb-praxis-ai:latest -f acb/assets/praxis/Containerfile acb/assets/praxis
```

Shared workflow helpers are under `acb/workflows/_shared`, including the pinned
`rgctl/v0.4.18` skill bundle and lockfile. Bundled workflows declare destination
paths; resolution maps them to canonical sources, hashes their contents and
export copies a complete build context. Custom workflows keep their own contained
asset paths. `scripts/check_package.py` checks installed resources and materializes
all bundled workflows outside the checkout. No generated duplicate source needs
a drift check; snapshot validation checks canonical source changes.

The metrics filter accepts and stores `max_body_bytes` for configuration
compatibility; that setting does not enforce a collection limit. Vertex and
request-classifier body limits remain separate operational controls.

The [Harbor API inventory](../docs/harbor-api-contracts.md) maps private provider
and job dependencies to focused upgrade checks. The
[runtime cleanup record](../docs/runtime-cleanup.md) tracks this removal batch.
