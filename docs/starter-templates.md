# Starter templates

Choose a starter explicitly with `acb init DIRECTORY --template NAME`.
From a source checkout, prefix commands with `uv run`. Existing files are kept,
so rerunning `init` does not reset model definitions, machine settings or tasks.

| Template | Purpose | Initial selections |
| --- | --- | --- |
| `quickstart` | Check installation with an included repair task before running a model | One Goose harness, one worker, one task, 300-second timeout; provider details in `config/models.yaml` |
| `rh-swe-bench` | Start an RH SWE-bench experiment using a local model server | Four harnesses, one worker, `task-0000`, pinned remote dataset |

Plain `acb init DIRECTORY` retains the `rh-swe-bench` template for existing scripts.
`--environment docker` or `--environment podman` sets the engine in a newly
created machine file; Podman is the default. Existing machine files are kept.

## Included setup task

```sh
acb init experiment --template quickstart --environment docker
acb resolve --config experiment/run.yaml
acb tasks --config experiment/run.yaml
acb run --config experiment/run.yaml --control oracle
acb run --config experiment/run.yaml --control nop
```

The generated layout is:

```text
experiment/
  run.yaml
  config/
    models.yaml
    machine.yaml
    benchmarks.yaml
  tasks/smoke/
    task.toml
    instruction.md
    environment/Dockerfile
    solution/solve.sh
    tests/test.sh
```

`resolve` and `tasks` need no containers or model calls. Controls require the
container engine and may build/download prerequisites, but make no model calls.
Expected grades are oracle **1** and nop **0**. The task checks setup, not
migration quality; controls do not establish model access or measurement coverage.

For a measured run, edit `experiment/config/models.yaml` with your provider's
model ID and endpoint, set its credential environment variable (initially
`OPENAI_API_KEY`), then prepare and run:

```sh
acb prepare --config experiment/run.yaml
acb run --config experiment/run.yaml
```

Model execution may incur charges. Inspect the actual result directory printed
by the run. Output initially goes under `runs/quickstart/`, relative to the
invocation directory. The starter's cache is `experiment/.cache/`, relative to
its machine file. Both settings are editable.

The task is included in installed packages. The source checkout's existing
`config.example/quickstart` remains available and also includes a ScarfBench
example. Follow the [full Quick Start](quick-start.md) for container installation,
measurement acceptance and ScarfBench setup; the named starter does not include
that separate migration configuration.

## RH SWE-bench

```sh
acb init experiment --template rh-swe-bench --environment podman
```

This creates `run.yaml`, `config/models.yaml` and `config/machine.yaml`.
Edit the local server's model ID and endpoint before running. The tasks come
from the pinned remote RH dataset rather than the included repair task.
`acb tasks --config experiment/run.yaml --download` fetches task metadata;
`prepare` downloads/builds and inspects execution prerequisites.

To edit either starter interactively, open `acb run`, choose **Load YAML file**,
and enter `experiment/run.yaml`. See [interactive setup](interactive-run.md).
