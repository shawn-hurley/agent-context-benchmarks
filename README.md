# agent-context-benchmarks (`acb`)

Run coding benchmarks through Goose, Pi, OpenCode, and Claude Code, then compare
application grades, token use, model requests, and tool interactions. ACB runs
agents in Harbor containers and routes model traffic through a measurement proxy.

Supported benchmarks include ScarfBench, SWE-bench, SWE-bench Lite, RH SWE-bench,
and local Harbor tasks. Runs can vary models, skills, extensions, and staged
workflows while retaining the application inputs and behavior tests for comparison.

Use the [documentation index](docs/README.md) to find setup, configuration,
workflow authoring, reporting and development guides.

## Quick Start

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Python 3.12+, and a running Docker or Podman engine with Compose
(and Buildx/BuildKit for Docker builds). On macOS,
containers run in a VM; allow space for image downloads and source builds.

```sh
git clone https://github.com/shawn-hurley/agent-context-benchmarks.git
cd agent-context-benchmarks
uv sync --locked
mkdir -p config
cp -R config.example/quickstart config/quickstart
```

The starter uses Podman. For Docker, set `environment: docker` in
`config/quickstart/machine.yaml`. Check your setup without making model calls:

```sh
uv run acb resolve --config config/quickstart/run.yaml
uv run acb run --config config/quickstart/run.yaml --control oracle
uv run acb run --config config/quickstart/run.yaml --control nop
```

Oracle must receive grade **1**; nop must receive grade **0**. This small included
task checks setup, not migration quality. Neither control verifies model access
or measurements.

Edit `config/quickstart/models.yaml` with your provider's model ID and endpoint.
Credentials are supplied through the configured environment variable:

```sh
export OPENAI_API_KEY='your-provider-key'
uv run acb prepare --config config/quickstart/run.yaml
uv run acb run --config config/quickstart/run.yaml
```

This schedules one Goose trial with one worker and a 300-second agent timeout.
`prepare` makes no model requests, but downloads/builds prerequisites. A normal
`run` contacts your provider and may incur charges. The first proxy source build
can take substantially longer than subsequent runs.

ACB prints the reserved run directory under `runs/quickstart/`. Use that actual
path to render and share the results:

```sh
uv run acb report ACTUAL_RUN --html --bundle runs/quickstart/report.zip
```

Open the run's `report.html`, or unzip the bundle and open `index.html`. Charts,
task navigation, tool interactions, and saved evidence work offline.

**[Follow the full Quick Start](docs/quick-start.md)** for container installation,
expected results, troubleshooting, repeatable acceptance checks, and the corrected
ScarfBench fork. The starter has separate registries, results, and cache; it does
not require resetting existing configuration or initializing SWE-bench.

## Run a benchmark or comparison

- **ScarfBench:** The Quick Start includes a pinned checkout of
  [the corrected benchmark fork](https://github.com/shawn-hurley/scarf-benchmark/pull/1),
  native Cart behavior validation, and expansion to the full application set.
- **SWE-bench / SWE-bench Lite:** Initialize the SWE-bench submodule and install
  the datasets extra. Follow [native grader setup](docs/harbor-benchmark-migration.md).
  Under Podman, ACB supplies the native graders with a generated `docker` shim
  on their `PATH`.
- **Staged migration workflows:** Select `kantra-controller`, `kantra-rgctl`, or
  `migiq`. See [workflow configuration and bundled skills](docs/workflows.md).
- **Other harnesses and treatments:** See the
  [feature configuration matrix](config.example/feature-tests/README.md),
  [RTK guide](docs/rtk.md), and [execution operations](docs/harbor-operations.md).

For [interactive configuration](docs/interactive-run.md), run `uv run acb run`
in a terminal. The editor can save configuration without running, browse task
IDs, and review effective settings. For scripts, use `--config`.
`acb init experiment --template quickstart` creates the included setup task;
`--template rh-swe-bench` creates a remote benchmark/local-server starter.
See [starter templates](docs/starter-templates.md) for the generated layout and
engine selection. Plain `acb init` retains the RH starter for existing scripts.
See the [CLI command guide](docs/harbor-operations.md#choosing-a-command) for
task selection, treatment configuration, comparisons, exports and cleanup.
`acb tasks --config YOUR_RUN.yaml` lists local/cached task IDs; add `--download`
to permit fetching remote metadata/data. See [task discovery](docs/task-discovery.md).
`resolve`, `prepare`, `tasks`, `report` and `compare` show summaries in a terminal
and JSON when piped; `--text` and `--json` override that choice.

```sh
uv run acb compare BASELINE_RUN CANDIDATE_RUN --html \
  --bundle runs/comparison.zip
```

Comparisons match application tasks and report grades alongside token changes.
Workflow, skill, image, timeout, and cache changes are experimental context.
Changed application inputs, behavior tests, or grading definitions can prevent
grade comparisons. Incomplete measurements prevent affected token comparisons.

## Results and verification

Each run retains `requested.json`, `resolved.json`, an overview `report.json` and
`report.html`, plus a directory for each harness. Harness directories contain
aggregate usage and metrics, their report, and per-trial transcripts, measurements,
and evaluation records. Raw Harbor evidence is retained in `.harbor/`.

Native ScarfBench and SWE-bench trials retain their submission and source diff.
The native verifier grades compilation, deployment, and the benchmark's behavior
tests separately from agent execution. A successful process exit alone does not
mean the task passed: inspect grade, evaluation status, and measurement coverage.
Token pricing is optional; unavailable pricing is not reported as a zero cost.

See [report rendering](docs/report-rendering.md),
[Maven download caching](docs/scarfbench-maven-cache.md), and
[maintenance checks](scripts/README.md) for details.

## Development checks

```sh
uv sync --locked --group dev
uv run pytest tests -q
uv build --out-dir dist
uv run python scripts/check_package.py dist/agent_context_benchmarks-0.1.0-py3-none-any.whl
```

The automated suite makes no paid model calls. Container acceptance checks are
opt-in; the Quick Start documents a model-free check and a one-task real-model
check. [Architecture](docs/architecture.md) and [current limits](docs/harbor-cleanup.md)
describe the implementation and evidence boundaries.
