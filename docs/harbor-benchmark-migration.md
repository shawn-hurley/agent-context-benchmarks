# SWE-bench and ScarfBench on Harbor

SWE-bench, SWE-bench Lite, and ScarfBench now default to Harbor. Existing
benchmark names, subset/limit selections, harness selections, skills, MCP
servers, and extensions use the shared resolver and Harbor agent adapter.
The legacy runner has been retired. Remove an explicit
`execution_backend: legacy` override from old configs and prepare a new plan.
Effective run ID collision behavior is unchanged. Current reporting targets the
current output format; old-format compatibility is not required.

## Running

Use the existing run configuration:

```sh
acb resolve --config run.yaml
acb prepare --config run.yaml
acb run --config run.yaml
```

For grading controls without model requests:

```sh
acb run --config run.yaml --control oracle
acb run --config run.yaml --control nop
```

SWE-bench's oracle applies the dataset's reference patch. ScarfBench's oracle
copies the target framework project. Neither guarantees a passing score:
reference projects still undergo the benchmark's actual grading.

### Grader dependencies

- ScarfBench requires its native `scarf` CLI, configurable with
  `benchmark.scarf_binary`, and a populated `benchmark_cache_dir`.
- SWE-bench uses the initialized repository submodule and its isolated Python
  environment by default. Installed-wheel users can set
  `benchmark.swebench_python` to an interpreter containing the same official
  task-repo-era SWE-bench harness. The tested submodule revision is
  `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`.
- The selected Docker or Podman engine and Compose frontend must be available
  on the controller; see [container prerequisites](harbor-operations.md#container-prerequisites-and-compose).
  SWE-bench's Docker SDK must reach that engine; `benchmark.docker_host` remains
  available. Preparation checks that it can see Harbor's prepared images.

Preparation fingerprints the ScarfBench executable or SWE-bench Python sources.
Execution rejects changed graders and changed task snapshots. Missing grader
dependencies fail before inference. The ScarfBench Containerfile and migration
prompts are included in the wheel; the Podman CLI shim is created per grading
trial and does not depend on a checkout's `bin/` directory.
`execution.environment` selects the engine for Harbor and native grading;
the legacy-only split between generation and grading engines is not used.

## Execution and grading boundary

Harbor owns task environments, scheduling, attempts, agent lifecycle, timeouts,
and native trial results. ACB exports immutable task bundles and selects
`acb.harbor.benchmark_verifier:BenchmarkVerifier` through Harbor's verifier API.
The verifier runs native grading in a cancellable controller process group.
It does not upload grading files or mount an engine socket into the agent.
Native grader logs and reports are retained under `verifier/native/`.

This is a controller-side custom verifier, not a Docker-in-Docker task.
The generated task's shell-verifier stub fails explicitly if someone runs the
bundle without ACB's custom verifier configuration.

### SWE-bench

The agent environment uses the task repository's Dockerfile and the existing
architecture adaptation. Repository and base-commit identity remain in task
metadata. Conda-based task recipes select the `testbed` language environment.
The exported snapshot preserves the full dataset row, official evaluation
script, test expectations, and binary test assets. Local JSON/JSONL datasets
are supported, as are instance and repository exclusions.

The verifier collects tracked edits and new untracked files, excluding the
image's pre-existing build artifacts and configured `patch_exclude_patterns`.
It preserves the existing behavior of applying exclusions to new untracked
files. The official `run_instance` creates a fresh grading container from the
exact image ID used by Harbor, applies the patch, runs the official evaluation
script, and produces per-test grading evidence. It does not grade the agent's
modified container or rely on a command's exit code as the score.

### ScarfBench

The agent receives the full source project, source `test.sh`, app-level task
text, and migration guidance. Top-level grading files remain excluded; nested
application files are preserved. The frozen target project stays outside the
agent image.

After generation, the verifier downloads the whole project and constructs the
native conversion layout. It rejects candidate symlinks that escape the
project, stages target validation inputs, then calls the existing
`ScarfBench.evaluate` / `scarf validate` path. Upstream Makefiles retain their
native controller-side behavior. The generated compatibility Makefile builds
and tests in fresh containers, using immutable build image IDs to avoid shared
tag races. Compile, deployment, smoke-test metadata and `make` failure evidence
retain their existing grading semantics.

## Verification evidence

`tests/test_harbor_benchmark_tasks.py` covers frozen source/target separation,
native-verifier selection, dependency checks, source preservation, exclusions,
and candidate-link containment. With `scarf` installed, it compares the bridge
and retained grader for passing projects, build failures, startup failures,
smoke failures, and misleading success markers, including detailed metadata.

The model-free live check is:

```sh
uv run python scripts/check_harbor_benchmark_migration.py \
  --image YOUR_LOCAL_LINUX_IMAGE_WITH_GIT_AND_BASH \
  --output runs/benchmark-migration-check
```

The September 18 check used fixed synthetic projects and real Podman containers,
Harbor controls, ScarfBench 0.1.2, and the official SWE-bench harness. Evidence:

- `runs/harbor-benchmark-migration-check-20260918-v2/checks.json`: both reference
  controls scored 1 and both unchanged-project controls scored 0, with no trial
  exceptions.
- `runs/harbor-benchmark-migration-check-20260918-v2/swebench-parity/parity.json`:
  passing, failing, invalid, and test-execution-error patches matched the
  official entrypoint, including detailed reports.

These checks establish fixed-fixture grading and lifecycle behavior. They are
not paid-model effectiveness runs or a new sweep of public benchmark instances.
Live Docker coverage was waived for that migration gate. Later
[Quick Start validation](validation/quick-start-validation.md) covers the repair
task and native Cart controls; the full migration/parity matrix remains unverified
on Docker.

Regression at that checkpoint: **478 passed, four skipped**. The rebuilt wheel was installed
into a temporary directory: ScarfBench task export and manifest verification
passed using only its packaged resources, and the installed bridge's official
SWE-bench SDK preflight could see the prepared Harbor image.

## Runner retirement: September 21

Harbor is the only execution backend. Shared authentication lives in `acb.auth`,
output reservation in `acb.run_paths`, and usage parsing in `acb.proxy.metrics`.
The old scheduler, pod lifecycle, proxy backends and legacy integration runtime
are removed. Native grading adapters and low-level adapter transports remain
maintained. Subsequent report cleanup removes old-format compatibility paths;
see [report rendering](report-rendering.md).

See [the cleanup record](harbor-cleanup.md) for verification and remaining work.
