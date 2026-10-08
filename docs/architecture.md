# Current architecture

ACB resolves an experiment, prepares immutable inputs, and delegates container
trials to pinned Harbor 0.23.0. Goose, Pi, OpenCode and Claude Code run inside task
containers. The worker is a separate process using the Python environment that
launched ACB. Harbor is the sole execution backend.

## Configuration and execution boundaries

| Layer | Responsibility |
| --- | --- |
| [`cli.py`](../acb/cli.py), [`interactive_run.py`](../acb/interactive_run.py) | Parse commands or edit a draft; save-only returns a saved-file result without executing |
| [`config.py`](../acb/config.py), [`resolver.py`](../acb/resolver.py) | Load registries and produce an immutable plan with validated names, precedence and declaring-file paths |
| [`task_discovery.py`](../acb/task_discovery.py) | Discover local/cached IDs; explicit downloads fetch remote listings without trial environments |
| [`harbor/backend.py`](../acb/harbor/backend.py), [`preparation.py`](../acb/preparation.py) | Check engine/Compose, prepare assets, inspect contracts, reserve output and own worker cancellation |
| [`harbor/worker.py`](../acb/harbor/worker.py), [`dataset.py`](../acb/harbor/dataset.py) | Validate the worker protocol, export/load tasks and build Harbor jobs |
| [`harbor/agent.py`](../acb/harbor/agent.py), [`transport.py`](../acb/harbor/transport.py) | Recheck runtime identity and bridge adapters to Harbor's environment API |
| [`harbor/results.py`](../acb/harbor/results.py), [`comparison.py`](../acb/comparison.py) | Import trials independently and derive grades, coverage and comparable results |

`resolve` and interactive Review need no containers or model requests. `prepare`
checks prerequisites, creates disposable environments, freezes task/runtime/image
identity and prepares assets. It makes no model requests. Ordinary `run` prepares
first, reserves a unique directory and passes its exact saved plan to the worker.
The worker does not reread registries.

Harbor schedules `(harness, task, attempt)` trials under one global concurrency
limit. Workflow stages start fresh agent sessions in the same workspace and
preserve the environment between stages. Exported SWE-bench/ScarfBench tasks use
a controller-side native verifier; local Harbor tasks use their verifier
definitions. See [native benchmark setup](harbor-benchmark-migration.md).

## Container lifecycle and measurement

Harbor's providers use Compose to build/start containers, manage services and
perform teardown. ACB subclasses them in
[`harbor/environment.py`](../acb/harbor/environment.py). Its compatibility layer
pins the Podman frontend, selects frozen images and handles log copying.
[`harbor/processes.py`](../acb/harbor/processes.py) replaces Compose command
execution with cancellation-safe process ownership. Those overrides still use
Compose; there is no separate direct-container runner.

Each measured trial has its own Praxis service and attribution tags. The harness
receives a local measurement endpoint and placeholder credential; the proxy
receives provider credentials by environment reference. Caveman middleware sits
between the harness and Praxis when selected. RTK changes selected shell-tool
output before it reaches the model. Response skills add instructions. See
[integrations](integrations.md) for these separate treatments.

Raw measurements become per-model-request `usage.jsonl` records, including
input/output and available cache buckets. Required missing/invalid usage leaves
measurement incomplete. Turns, tool interactions, grades and usage are separate
observations.

## Evidence and extension points

Output retains requested/resolved configuration, visible harness/trial evidence,
hidden native `.harbor/` results and a job summary. Import errors retain healthy
trials and explicit missing slots. Infrastructure failures fail execution; failed
grades, agent timeouts and incomplete measurements remain reportable outcomes.
A zero exit status does not establish task success.

Comparisons require compatible task inputs and grade definitions, and use complete
measurements for token deltas. HTML/ZIP reports use packaged local chart assets.
See [report architecture](report-rendering.md) for renderer/export boundaries.

Custom workflows use the [workflow schema](workflows.md). Harness adapters implement
`HarnessAdapter` and require factory, resolver and catalog registration. Integrations
implement the registered lifecycle described in [integrations](integrations.md);
run YAML does not load arbitrary Python integration classes. Native benchmark
exporters/verifiers live under `acb/harbor/`, with reusable benchmark adapters under
`acb/benchmarks/`.

[Runtime contracts](harbor-runtime-contract.md) describe execution checks.
[Maintenance checks](../scripts/README.md) cover regression and packaging.
[Harbor cleanup](harbor-cleanup.md) owns open work and accepted limits.
The [original design](archive/design-original.md) is retained as history.
