# Harbor operation and supported scope

Reviewed September 17, 2026. Podman has live acceptance evidence. Docker is
selectable through the same Harbor transport and passed installed-worker config
checks; live Docker testing is waived by user decision. Engine differences will
be handled as bugs. Legacy execution still contains Podman-specific code until
its planned retirement; this does not run on the Harbor transport path.

Current task status and scope are tracked in [Harbor cleanup](harbor-cleanup.md).

## Commands

Install the project using the existing project workflow, or install its wheel.
ACB requires Python 3.12 or newer and includes Harbor 0.23.0 as a pinned core
dependency. The worker remains a separate process but uses the same Python
environment that launched ACB. The former `benchmark.python` override is removed.

```sh
acb init experiment
acb list harnesses --config-dir experiment/config
acb resolve --config experiment/run.yaml --config-dir experiment/config
acb prepare --config experiment/run.yaml --config-dir experiment/config
acb run --config experiment/run.yaml --config-dir experiment/config
acb run --config experiment/run.yaml --config-dir experiment/config --control nop
acb run --config experiment/run.yaml --config-dir experiment/config --control oracle
```

Edit the generated model configuration to specify the actual model ID and endpoint.
Set `environment: podman` or `environment: docker` in `config/machine.yaml`.
An explicit run-level `execution.environment` overrides the machine setting.
Resolve validates configuration without starting containers or calling a model.
Preparation starts disposable environments and verifies prerequisites. Controls
exercise grading without a measured harness/model run.

## Offline semantics

When the machine cannot reach a registry, a required uncached image pull fails
naturally. It can take until the engine's connection timeout and may involve retries.
Cached images can still be used where the underlying task setup permits.

`execution.offline: true` additionally blocks ACB-controlled asset downloads and
rejects missing runtime/provider/skill/harness cache entries. It is **not a complete
network-isolation switch**: Harbor task-image builds and registry pulls are not
universally disabled by it, and task setup can have its own network requirements.
Do not describe it as an air-gap guarantee. No additional task-image network guard
is being added for the current disconnected-machine requirement. Strict prevention
of network attempts while connected would require a separate explicit guarantee.

## Supported and remaining scope

The verified profile is Linux/root task environments on Podman, with all four
harnesses, Praxis, task runtime checks, error reporting and controlled skill use.
RH no-op/oracle controls pass. Phases 2/3 are complete under the agreed scope;
Claude's amd64/QEMU startup failure is an accepted known issue, described below.
Separate-verifier and Caveman record-mode evidence are
additional capabilities with the limits described in the runtime-contract doc.
SWE-bench, SWE-bench Lite and ScarfBench now use Harbor with native grading;
the legacy runner is removed. All selected migration phases are complete.
Broader MCP work and full dependency locking remain deferred; existing adapter
selection/validation behavior is retained. See [benchmark setup and parity](harbor-benchmark-migration.md).

Prepared artifacts record task/runtime/image identity. Run directories retain
requested/resolved configuration, Harbor trial artifacts, per-harness reports,
usage, runtime/startup evidence and imported evaluations. Consult `measurement.json`
for collection/completeness status and `evaluation.json` for grading/error status;
a process exit or missing measurement is not automatically a zero task score.

ACB command success is independent of verification success. Failed grades,
unavailable grades, agent timeouts and missing/unreadable result files normally
leave a zero exit status when orchestration completed. Setup/infrastructure
failures, orchestration errors and explicit cancellation remain nonzero.
`execution-status.json` records execution status and outcome counts;
`evaluation.json` and the reports retain the actual grade or error. Controls use
the same exit policy: inspect their recorded outcomes to determine whether the
expected oracle/nop reward was obtained. Progress and `job.log` still show trial
failures even when ACB execution completed.

Native result files are loaded independently. Damaged files stay in place,
healthy results are imported, and unavailable scheduled slots remain explicit.
`import-errors.json` records unreadable/invalid results and report-import errors;
these do not automatically fail the command or replace an earlier execution
error. Model request input/output token counts must be explicitly present;
missing or invalid usage marks measurement incomplete without failing the command.
Explicit zero remains a valid observation. Optional cache buckets default to zero.
Raw evidence is retained, while incomplete measurements are excluded from token
comparisons. `collection_complete` separately records whether collection succeeded.

Both execution backends reserve run directories atomically before writing
configuration or logs. The existing run name and `-1`, `-2` suffix convention
remain; simultaneous invocations receive distinct directories and effective IDs.

New task manifests use `task_checksum_version: 2`. Their inventories include
file contents, file/directory types and permission bits. Permission changes
therefore invalidate cached snapshots. Local snapshots use a new `local/v2/`
cache namespace. Prepared plans with an older or absent checksum version must
be re-prepared before execution; existing saved reports remain readable.

### Reading a Harbor run directory

`<run>/job.log` is ACB's concise job summary. It names each task and harness,
reports grades and failures, and links to the detailed records. ACB creates
`<run>/<harness>/<trial-id>/` when a trial starts, with links to the run log,
trial log, agent transcript and Praxis log. Use `tail -F` because the agent
files may appear after the directory is created. The transcript stream is
JSONL; `transcript.log` is an alias for `transcript.jsonl`. Praxis diagnostics
are polled during model execution, then replaced with the complete sidecar
log when the service stops. After execution, ACB saves trial logs and evidence as
regular files. The writer also creates `<harness>/instances/<trial-id>` aliases,
but current report rendering reads the visible trial directories directly. The harness report is `<harness>/report.json`.

Harbor creates `<run>/.harbor/` as its native job directory for new runs.
Older runs retain `<run>/harbor/`. The native directory's top-level
`config.json` declares the native job and `lock.json` records resolved job
inputs. Each `<task>__<attempt>/` is one trial and has its own config and
resolved lock. Harbor also creates `agent/` for agent logs, `verifier/` for
grading output and `artifacts/` for files collected from the task environment.
ACB writes its own evidence under `agent/acb/`, then imports it into the
harness directory. Harbor's `trial.log` covers one attempt; many messages also
appear in Harbor's `job.log`. ACB's root `job.log` is the starting point for
diagnosis. Harbor worker stdout and stderr, including its own progress display,
are captured in `<run>/.harbor/worker.log` so they do not overwrite ACB's
terminal UI. Preparation writes `worker-prepare.log` beside its prepared plan.
With `podman-compose`, task uploads use Harbor's tar transfer directly because
that frontend does not support `compose cp`. The first successful transfer is
noted once in the native job log; downloads use the engine's `cp` capability.

## Known issue: Claude Code on emulated amd64 benchmarks

On an ARM64 host running Linux amd64 benchmark images through QEMU, the
`claude-code` harness can fail before any model request. This is reproduced with
Claude Code 2.1.241 (bundled Bun 1.4.0) on the RH SWE-bench task image:
`claude --version` exits 139 with a Bun segmentation fault. The same Claude
version starts successfully on native ARM64. Shared libraries resolve in both
diagnostic images; neither has Node installed. The npm package installs a native
executable, so installing system Node does not address this observed failure.
Evidence: `runs/harbor-claude-packaging-check/packaging-check.json`.

Related upstream reports:

- [Bun #26635](https://github.com/oven-sh/bun/issues/26635): immediate crash under
  QEMU during an amd64 container build, with AVX/AVX2 advertised as in our log.
  It uses Bun 1.3.8/musl and ultimately reports SIGILL, unlike our glibc/SIGSEGV.
- [Claude Code #87974](https://github.com/anthropics/claude-code/issues/87974):
  reporter describes a startup SIGSEGV from a memory overread and supplies QEMU
  reproduction commands; their CPU feature profile differs from ours.
- [Bun #11626](https://github.com/oven-sh/bun/issues/11626): segfault with a QEMU
  CPU model on an x86 VM. Its reported host-CPU workaround does not directly
  apply to x86 emulation on an ARM host.

These are related reports, not a confirmed diagnosis or promised upstream fix.
By user decision on September 17, further debugging and local workarounds are
deferred. Affected benchmark/harness combinations are expected to fail startup;
retain the preparation failure and its artifacts rather than counting it as a
model failure or a valid zero reward. Native amd64 Claude execution has not been
validated here, and this observation does not imply every emulated image fails.

This limitation does not block phases 2/3 or require a Claude emulation fix for
later pilots. Record affected pilot combinations as unavailable with this known
issue. Revisit when an upstream change is available to validate or the user
explicitly resumes the investigation.

## Package verification

The repeatable regression and installed-wheel gates are documented in
[maintenance checks](../scripts/README.md). The earlier
`runs/harbor-final-package-review` record is historical. Current benchmark export
and native grader verification are recorded in [the migration guide](harbor-benchmark-migration.md).

## Shared treatment preparation

SWE-bench and ScarfBench use the same Harbor preparation and runtime as other
tasks. `extensions: [rtk]`, `extensions: [caveman]`, and `skills: [caveman]`
select distinct treatments. Preparation inspects task architecture and helper
interpreters before staging assets. Claude RTK hooks and the Caveman recovery
client require Python >=3.8; explicit interpreter selections are verified and
no interpreter is installed automatically. Task workdir/conda settings remain
part of the inspected runtime contract.

Caveman has a service container and recovery store per trial, listening on 18881
and forwarding model traffic to Praxis on 18880. Provider credentials stay in
Praxis. Services are collected and stopped before verification; teardown also
handles partial startup and cancellation. Recovery-store isolation and cleanup
were checked with two Harbor environments; see the cleanup record for evidence.

Historical Phase 4 native-adapter and Harbor checks are recorded in
`runs/phase4-native-treatments/check.json` and
`runs/harbor-phase4-treatments/treatment-check.json`. They use cached binaries and
a deterministic API service on ARM64 Podman, not paid-model effectiveness runs.
The old pod-based runner and its reproduction scripts are retired.

### Functional Caveman compression checks

Goose, Pi and OpenCode carry explicit successful shell status through reviewed
adapters; Claude uses the status in its Anthropic tool-result message. Only repetitive INFO logs without critical
diagnostics are eligible. Failed tool calls, unknown status, mixed media and
recovery-command results pass through. This avoids inferring success from the
absence of an error string. All four harnesses passed functional compression
acceptance on Harbor and the former runner at that historical checkpoint.

Historical evidence `runs/legacy-caveman-34b0c32b31/check.json`
shows actual compression, model-selected recovery using the supplied handle,
exact recovered bytes, five accounted requests and cleanup. The prompt
explicitly asks for recovery; this does not measure unprompted recovery behavior.

The legacy-runner scripts are retired. Their recorded results below are
historical. Current commands are listed in [maintenance checks](../scripts/README.md).

For Harbor, `scripts.check_harbor_caveman` takes a deterministic bridge plan and
a new output directory. `runs/harbor-caveman-compression-all-3/check.json`
and `runs/legacy-context-all-4/check.json` cover all four harnesses: reward 1,
exact agent-driven recovery, preservation of failed/critical output and complete
accounting. Harbor also verifies both service endpoints are unavailable during
grading. Fixture token counts are synthetic accounting checks.

Required-service loss invalidates the treatment; successful fallback does not
make a broken required service a valid experiment. Service-loss, cancellation
and cross-trial recovery isolation evidence is recorded in
`runs/legacy-context-service-failure`, `runs/legacy-context-cancellation`,
`runs/harbor-context-service-failure`, `runs/harbor-context-cancellation` and
`runs/caveman-store-isolation`.

Manifests distinguish preflight recovery, measured successful retrieval and
actual compression. Repeated history can generate multiple compression events
for the same output; event counts are not unique compressed-result counts or
provider token savings. Shared socket tests cover incremental SSE forwarding and
client cancellation even while upstream is silent.


### Phase 4 pilot resource and image deviations

Pilot-only task copies omit disk quotas this Podman host cannot enforce:
RH 20 GiB, Terminal-Bench/Aider 10 GiB each. Production preparation continues
to reject unsupported limits. In the selected RH task, the reported bug and
verifier concern YAML parsing; the disk quota is an environment resource budget,
not an instruction to repair disk exhaustion. This assessment is task-specific.

The selected Terminal task's published amd64 image crashes in its `uvx` verifier
under emulation, including oracle runs. Reward 0 from that verifier is not a
valid model-quality result. A separately approved native pilot builds the
unchanged task Dockerfile on ARM64, preserves CPU/memory/tests, and pins the
inspected image across arms. Its no-op/oracle controls pass with rewards 0/1.
The original amd64 artifacts remain available. See the
[Phase 4 checkpoint](harbor-phase4-checkpoint.md) for results and provenance.


## Dataset metrics and comparisons

Harbor registry/package metric definitions are retained automatically. Benchmark
configuration can add explicit metric definitions and select the primary grade:

```yaml
reward_metric: reward
grade_direction: higher       # higher or lower
grade_tolerance: 0
success_value: 1              # optional binary resolution mapping
metrics:
  - type: sum
```

For a custom metric use `type: uv-script` with `kwargs.script_path`. Relative
script paths resolve against the declaring config file. Preparation freezes the
script and metric runtime image; execution happens in a disposable container.
Metric scripts use Harbor's `-i rewards.jsonl -o result.json` interface and must
return a JSON mapping of names to finite numeric values. Native dataset aggregates
remain secondary detail; select a primary reward metric and direction for grades.

```bash
acb report runs/example --html
acb compare runs/baseline runs/candidate --html runs/comparison.html
```

The first comparison input is the baseline. The HTML bundle includes individual
benchmark pages in `<report-name>-benchmarks/`, and comparisons have a companion
`<report-name>.comparison.json`. Keep those files together when sharing the bundle. Bounded evidence previews
are embedded; full-artifact links require the original local run directory. See
[report rendering](report-rendering.md) for component boundaries and limits.
CLI comparisons use the same data and rules. For a comparison between different
harnesses, select the individual harness directories. Full suites match by harness.
Unmatched benchmarks and changed/unknown conditions are visible, and only comparable
complete measurements contribute to token totals. Turn counts have harness-specific
definitions; model requests and tool calls are counted separately.

For multiple attempts, each attempt remains inspectable; the benchmark grade is
its mean only when all attempts have valid grades. A partial coverage summary does
not claim the same result across the whole suite. Missing comparison provenance
or measurement status makes a current record unverified. Old report formats
are not a compatibility requirement.

Combined context configuration uses `extensions: [rtk, {name: caveman, options:
{mode: compress}}]`. Harbor supports this selection.
Recovery returns exact post-RTK output. Multi-step Harbor tasks use fresh harness
sessions per step, retain workspace/recovery state within a trial, honor declared
step budgets up to the configured timeout, and retain step-specific evidence.
