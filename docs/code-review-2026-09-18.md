# Code review: Harbor integration and cleanup

Reviewed September 18, 2026. Scope: the working-tree changes relative to `HEAD`,
including new, untracked implementation, tests, assets, examples and documents.
The findings below describe the original review, before the subsequent decisions
and implementation recorded here.
The [cleanup plan](harbor-cleanup.md) owns the remaining work. Accepted migration
phases stay closed, and historical live checkpoints retain their original scope.

## Subsequent user decisions and implementation

These supersede the original recommendations below:

- R01: initially accepted unchanged, then authorized for implementation. Model
  input/output counts are now required before normalization; missing usage marks
  measurements incomplete without failing execution. Explicit zero and optional
  cache-bucket defaults remain valid.
- R02: execution success is independent of grading success. Failed or unavailable
  grades, agent timeouts and missing control slots normally return zero; actual
  setup/infrastructure or orchestration failures still return nonzero. Implemented
  with separate execution-status evidence and retained grading diagnostics.
- R03: implemented worker-session shutdown and reaping on launcher exceptions.
- R04 and R05: initially accepted unchanged, then authorized and implemented.
  Both backends reserve run directories atomically; version 2 task inventories
  include file/directory permissions and require old plans to be re-prepared.
- R06: fold installed resources into the next ScarfBench/SWE-bench Harbor migration.
- R07: implemented validation-specific build image IDs and upgrade of older
  ACB-generated Makefiles, preserving upstream Makefiles.
- R08: implemented permissive result recovery. Healthy results survive invalid
  siblings; import diagnostics do not automatically fail the command.

Latest regression: **465 passed, 4 skipped**, including real local worker
process cleanup, concurrent reservations, permission-sensitive snapshots,
measurement-completeness comparisons and interleaved Make/engine-double validation. No fresh live
container/model run was performed. See the
[current cleanup dispositions](harbor-cleanup.md#review-follow-up-september-18-2026)
for acceptance and remaining work. The original severity/order below is historical.

## Assessment

Keep the current architecture: one configuration resolver, preparation that
records execution inputs, adapters shared across backends, and Harbor responsible
for task execution. The existing code has substantial coverage for cancellation,
isolation, cached assets, multi-step trials and incomplete measurements. A new
orchestration framework would add work without resolving the defects below.

Before extending the implementation, address the measurement, grading and process
lifecycle findings. Then extract shared run services and report components, add
validated boundaries, and migrate the retained benchmarks with grading parity.
Do not delete the legacy runner before both benchmark migrations are verified.

## Verification performed

- `uv run --offline --with pytest python -m pytest tests -q`: **428 passed,
  4 skipped**. The skipped tests are opt-in live checks. The existing `.venv`
  does not include pytest; using cached pytest allowed the suite to run.
- `uv build --offline --wheel --out-dir /tmp/acb-review-20260918-wheel`:
  succeeded. Inspection of the resulting wheel confirmed the missing ScarfBench
  Containerfile described in R06. A successful build is not a successful
  installed-runtime check.
- Temporary, container-free reproductions confirmed R01, the configured-metric
  mismatch in R02, R03, R04 and R05. These were diagnostic scripts, not new
  checked-in regression tests.
- Read saved RH baseline/treatment and multi-step timeout results through the
  current comparison code. The RH comparison has zero comparable grades and
  zero comparable token measurements; its matched totals are `None`. Captured
  tokens (463,243 and 902,475) remain partial observations. The multi-step timeout
  similarly retains 840 captured tokens without claiming a complete measurement.
- Reviewed configuration dispatch, provenance, preparation/download/cache paths,
  Harbor worker/provider/transport/agent boundaries, integrations and harness
  changes, ScarfBench discovery/grading, result import, reporting, tests, package
  declarations and migration/cleanup documents.

No fresh container, provider, live-model, Docker/Podman, or benchmark parity run
was performed. R07 is established by the generated commands and their shared tag;
R08 by the unguarded result-loading path. Their failure scenarios still need
dedicated regression tests. This review does not establish treatment effectiveness
or reproduce every historical live checkpoint.

## Findings

P1 means fix before relying on affected measurements or concurrent executions.
P2 means a concrete correctness or distribution defect to fix before declaring
cleanup complete. Inherited defects are identified separately from new behavior.

### R01 — P1: Missing usage fields become complete zero-token measurements

Locations: `acb/usage.py:155`, `acb/proxy/praxis.py:865`,
`acb/harbor/praxis.py:167`.

The new `parse_measurements()` validates token fields only when they are present.
The record `{"endpoint":"/v1/chat/completions","status_code":200}` passes
without errors. `_read_metrics_file()` then supplies zero for all four token
buckets. Harbor collection can mark the measurement complete, and comparison
validation sees ordinary nonnegative integers. Missing observations therefore
become apparently comparable zero usage.

Reproduction: the record above returned an empty validation-error list and
produced a usage row with four zero token buckets. This is a new completeness
contract defect around pre-existing normalization defaults.

Fix: define the required measured-request schema, distinguish absent usage from
observed zero, and make both backends consume the same validated records. Preserve
raw records and mark missing required usage incomplete. Explicitly define which
optional cache fields may default to zero for a supported collector schema.

Acceptance: missing required usage cannot contribute a comparable total;
genuine zero usage and non-model discovery traffic retain their intended behavior.

### R02 — P2: Worker success and recorded grading status can disagree

Locations: `acb/harbor/worker.py:158`, `acb/harbor/results.py:38`,
`acb/harbor/progress.py:60`.

The measured worker checks `evaluation(item, None)`, while import uses the
configured reward metric and success value. With `reward_metric: reward`,
`success_value: 1`, and rewards `{"other": 1}`, import records an error but the
worker's evaluation says completed. The command can therefore exit successfully
with grading errors. Progress also treats an error status without exception text
as ordinary unsuccessful verification.

The control branch has a related coverage gap: it checks returned rewards but
does not require every scheduled task/attempt to have a result. A nonempty passing
subset can satisfy its success predicate even though import adds missing trials.

Fix: share one configured grading/coverage decision among import, worker exit,
job log and progress. Require the expected task/harness/attempt identities for
controls as well as measured runs. Keep a valid zero reward distinct from an
evaluation error. A numeric metric without a binary success threshold must remain
usable as a numeric grade.

Acceptance: wrong reward key, missing control slot, step error and malformed reward
produce consistent error status and nonzero exit; valid failed tasks remain valid
evaluations. Exercise the worker exit decision, not only `evaluation()` in isolation.

### R03 — P1: Progress-processing errors abandon the live worker

Location: `acb/harbor/backend.py:77`.

`read_events()` parses JSON and invokes the display callback inside a wait loop
that handles only `KeyboardInterrupt`. A malformed event, callback exception or
event-file read error exits the launcher without terminating or reaping its
separate-session worker. Containers and model requests can continue after the CLI
has reported failure. Even the interrupt path has no final kill if its second
wait times out.

Reproduction: a fake live process wrote a malformed complete event line. `_worker()`
raised `JSONDecodeError` without requesting process termination or calling `wait()`.

Fix: give the worker process an explicit lifecycle owner with bounded interrupt,
terminate, kill and reap on every exceptional exit. Decide whether malformed
telemetry is recoverable; preserve its diagnostics without letting UI failure
orphan execution. Keep the initiating exception if cleanup also fails.

Acceptance: malformed events, callback failures, repeated interruption and a
worker that ignores termination all leave no owned process group behind. Include
a local subprocess regression and an opt-in container cancellation check.

### R04 — P2: Output allocation is not atomic across invocations

Locations: `acb/runner.py:436`, `acb/harbor/backend.py:205`.

`_resolve_run_dir()` selects an available path but does not reserve it. Both
callers later use `mkdir(..., exist_ok=True)`. Two overlapping invocations with the
same run ID can choose the same directory and overwrite configuration, worker
logs and results. This is inherited from the retained runner and newly shared
with Harbor; unique preparation directories do not protect the final run output.

Reproduction: two allocations before either caller creates its directory return
the same path and effective ID.

Fix: extract neutral output allocation and reserve the directory using exclusive
creation with collision retry. Preserve the established unsuffixed/`-N` naming
and use the reserved effective ID consistently in provenance and results.

Acceptance: simultaneous processes using the same requested ID receive distinct
directories and matching effective IDs, including when existing suffixes have gaps.

### R05 — P2: Task snapshots ignore executable permissions

Location: `acb/harbor/dataset.py:14`.

Task checksums include file paths and contents but omit file modes and directory
entries. Changing a script from `0644` to `0755` leaves the checksum unchanged.
`snapshot_local_task()` then reuses the old snapshot, and `verify_manifest()`
cannot detect the change. Docker `COPY` and executable task/skill scripts can
behave differently even though ACB reports the same task input.

Reproduction: after changing a local script to executable, the checksum and cache
path were unchanged; the returned snapshot still contained the non-executable file.

Fix: use a versioned, unambiguous task inventory including entry type, relative
path, execution-relevant mode and content digest. Reject unsupported entries and
define how old manifests are handled; require re-preparation rather than silently
reinterpreting historical checksums.

Acceptance: content, permission and relevant directory changes invalidate the
snapshot; unchanged trees reuse it. Historical report readers remain usable.

### R06 — P2: The wheel omits ScarfBench's runtime build recipe

Locations: `pyproject.toml:23`, `acb/benchmarks/scarfbench.py:158`.

The package includes the new migration prompts but not
`acb/scarfbench/Containerfile`. `_ensure_image()` requires that file whenever the
ScarfBench image is absent. An installed wheel cannot perform this supported
first-use build even though source-checkout tests pass. The packaging omission
predates this change; the expanded asset packaging still leaves it unresolved.

Evidence: the wheel built during review contains the prompts and both provider
Containerfiles, but not the ScarfBench Containerfile.

Fix: include all runtime resources and test their access from the built package
outside the checkout. Inventory other remaining checkout-relative dependencies,
especially the retained SWE-bench grader, and document their installation contract.

Acceptance: a fresh wheel contains the ScarfBench recipe and all referenced build
context files; an isolated installed-package test reaches the image-build boundary
with an engine double, without a pre-existing image masking missing resources.

### R07 — P1: Concurrent ScarfBench grading can run another prediction's image

Location: `acb/benchmarks/scarfbench.py:290`.

The generated Makefile now gives each conversion a distinct container name, but
its image remains `scarfbench-<app>-<framework>:latest`. Concurrent validations
for different harnesses or runs build different generated projects into that
same tag. If B finishes building between A's build and `docker run`, A starts B's
image and can receive B's grade. Unique container names do not isolate build
artifacts. The shared-image hazard is inherited and remains in the revised grader.

Fix: assign validation-specific image identities or run the immutable image ID
produced by that validation's build. Tie cleanup and recorded grading provenance
to the owned image/container. Preserve official upstream Makefiles and the
unmodified target test script.

Acceptance: interleave two validations of the same app/target with deliberately
different output, and verify each runs its own image and records its own result.
Retain this isolation contract in the Harbor exporter.

### R08 — P2: One damaged native result prevents partial-results import

Location: `acb/harbor/worker.py:149`.

The `finally` block loads every result file with an unguarded list comprehension
before calling `import_results()`. A truncated JSON file from interrupted writing
or one invalid result structure prevents import of all healthy trials and can
replace the initiating execution exception. The importer already accounts for
missing scheduled slots, but this failure occurs before it can do so. The host's
defensive error reader does not repair missing reports.

Fix: load and validate native results individually, retain invalid-file evidence,
import healthy trials and represent unavailable slots explicitly. Preserve the
original error and ensure reporting failures remain visible as secondary errors.
Validate duplicate/unexpected trial identities at this boundary as well.

Acceptance: a directory with healthy, truncated and structurally invalid result
files still yields a report covering all scheduled slots, an explicit import-error
artifact and a failing command status without losing the original failure cause.

## Maintainability follow-ups

These are structural tasks, not additional claims of runtime defects:

- **Shared run services:** Harbor importing private functions from the legacy
  runner reverses the intended dependency direction. Extract allocation and
  authentication before retiring that runner; combine allocation with R04.
- **Rendering:** `comparison_html.detail_html()` imports private renderer helpers
  and inserts/replaces whole-document strings. `write_reports()` reloads the
  entire harness report for each task detail, multiplying parsing work with task
  count. Use a loaded run model, task selection, page shell and reusable chart/
  evidence components. Bound or link large evidence files. Preserve escaping,
  distinct chart IDs, request/turn semantics and incomplete-measurement labels.
- **Validated boundaries:** `ResolvedPlan` freezes JSON but does not give prepared
  plans, events or native results typed contracts. Introduce validation at worker
  entry and result import before undertaking broad typing changes. Keep the
  protocol and provenance versions explicit.
- **Harbor compatibility:** private `_platform`, `_metrics`, Compose and output
  collector access is spread across contracts, metrics, environment and process
  helpers. Inventory it as one compatibility boundary and require contract tests
  before changing the Harbor pin. Keep public agent/transport logic independent
  of those private attributes.
- **Agent options:** local inspection of pinned Harbor 0.23.0 shows that
  `BaseAgent.parse_options()` emits a debug message whenever `options_model` is
  absent, including empty kwargs. ACB consumes `plan` and `harness` in its
  constructor first. The message does not prove those settings were lost. A
  supported options schema should cover preflight and constructor behavior;
  suppressing the log alone would not provide validation.
- **Tests and packaging:** declare a reproducible test dependency/command,
  explicitly collect `tests/`, and test built resources outside the checkout.
  Keep live container/model checks opt-in with recorded prerequisites. Consolidate
  their duplicated fixtures only where it reduces maintenance while retaining
  existing coverage. The six duplicated Rust/Cargo files under
  `praxis-vertex-anthropic/` and `acb/assets/praxis/praxis-vertex-anthropic/` currently
  match byte-for-byte; designate a canonical source and verify generated/package
  copies so future measurement fixes cannot update only one build path.
- **Cleanup tracking:** keep one current checklist, stable task IDs and evidence
  links. Preserve old phase/checkpoint documents as history. Closed phase status
  must not imply that these newly identified defects or deferred migrations have
  been fixed.

## Recommended implementation order

1. R01–R03, R07 and R08: trustworthy measurements, grading and failure handling.
2. R04–R06: atomic run allocation, snapshot integrity and installed resources.
3. Shared services, renderer components, validated boundaries and compatibility
   tests. Add report charts after the shared renderer is in place.
4. ScarfBench and SWE-bench exporters, passing/failing parity fixtures and
   configuration/extension parity; then remove the legacy scheduler and pods.

The [cleanup plan](harbor-cleanup.md#review-follow-up-september-18-2026) translates
this order into tasks with completion criteria. The subsequent user decisions
above and the current cleanup plan supersede this original ordering.
