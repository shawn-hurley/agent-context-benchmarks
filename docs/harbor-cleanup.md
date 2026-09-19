# Harbor integration cleanup

This tracks cleanup after the accepted Harbor implementation. The migration
phases are closed; this work is tracked here rather than as a new phase gate.

Updated September 18, 2026 after the
[working-tree code review](code-review-2026-09-18.md). This is the current cleanup
backlog. The user's latest request authorizes C01, C04 and C05, superseding their
earlier acceptance without changes. C06 remains part of the next benchmark
migration; C02, C03, C07 and C08 retain permissive outcome handling. Task IDs C01–C08
correspond to review findings R01–R08; C09–C16 cover structural follow-ups and the
existing migration work.

Review verification: **428 tests passed, 4 opt-in live tests skipped**. The wheel
build succeeded but exposed a missing ScarfBench resource (C06). No fresh live
container/model or benchmark parity run was performed during the review.

Latest implementation verification: **465 passed, 4 skipped**. Tests include
real local worker-process termination and interleaved ScarfBench validation using
a deterministic engine double, six-process run reservation, permission-sensitive
snapshot reuse and missing-usage comparison coverage. No new live container/model
run was performed.

## Runtime and logs

- [x] **Simplify the run directory after preserving the useful records (accepted
  for now).** ACB
  now hides Harbor's native `.harbor/<trial>/` tree and exposes live, durable
  evidence at `<harness>/<trial-id>/`. `<harness>/instances/<trial-id>` is a
  compatibility link for existing reports. This is the accepted layout for the
  current cleanup; deeper native-evidence deduplication can be reconsidered when
  report compatibility changes. Existing run directories remain readable.

- [x] **Hide Harbor's native job directory in new runs.** New runs use
  `<run>/.harbor/` for native config, lock, logs and trial attempts. ACB's
  visible `<run>/job.log` links to the useful trial records. Readers still
  recognize older runs with `<run>/harbor/`; existing directories are not moved.

- [x] **Expose each trial in its harness directory from trial start.** New runs
  create `<run>/<harness>/<trial-id>/` before agent execution and link the
  current job log, trial log, transcript stream and Praxis log. The importer
  saves the completed logs and verifier evidence there; `instances/<trial-id>`
  is a compatibility link for existing reports. `transcript.log` is a JSONL
  alias.

- [x] **Stream Praxis diagnostics during model execution.** A bounded poll
  copies sidecar log bytes to the visible trial path while the model runs; the
  complete sidecar log replaces that copy at service stop. The poll stops
  before shutdown and does not touch request accounting.

- [x] **Put the meaningful job summary at the run root.** New Harbor runs now
  write `<run>/job.log` with task, harness, status, reward, measurement
  completeness, failure reason and paths to detailed evidence. ACB also prints
  this path after the worker exits. The previously recorded RH run has been
  backfilled. The native Harbor `.harbor/job.log` remains available for detail
  in new runs; older runs retain `harbor/job.log`.

- [x] **Use ACB's Python environment for Harbor.** ACB now requires Python 3.12+
  and pins Harbor 0.23.0 as a core dependency. The worker uses `sys.executable`;
  automatic venv creation, inventory/ready markers, and `benchmark.python` were
  removed. The worker remains a separate process.

- [x] **Avoid unsupported Compose copy commands.** When the selected Podman
  frontend lacks `compose cp`, uploads now go straight through Harbor's tar
  transfer. The first successful upload logs the selected route once, without
  a misleading `invalid choice: 'cp'` failure. A tar failure remains a real
  transfer error. Harbor already uses engine `cp` directly for downloads when
  the Compose capability is absent. Docker Compose behavior and the existing
  cancellation cleanup path are unchanged. Unit checks cover file and directory
  routing, success logging and failure propagation; a fresh live Podman run
  remains useful for end-to-end confirmation.

- [ ] **C13 — Deferred: validate the Harbor agent options contract.** Harbor logs that agent
  `acb` has no `options_model` and ignores leftover keyword arguments. ACB's
  constructor consumes `plan` and `harness`, so this message alone does not
  establish lost settings. Check the actual options contract and remove the
  warning only through a supported integration change.

  Review evidence: pinned Harbor 0.23.0's `BaseAgent.parse_options()` emits this
  debug message even for empty kwargs when `options_model` is absent. ACB consumes
  `plan` and `harness` before calling the base constructor. The investigation is
  complete; supported validation remains open. Define options through Harbor's
  public schema/preflight contract and test both preflight and construction with
  valid and unknown options. Do not mark this done merely by filtering the log.
  Coordinate with C11 so plan validation has one owner.

- [x] **Show recorded trial errors at the CLI boundary.** The worker and launcher
  now include each recorded exception type and message, such as
  `AgentTimeoutError: Agent execution timed out after 600.0 seconds`, plus its
  traceback path. Keep this behavior when changing progress output.

## Terminal experience

- [x] **Restore the pre-Harbor terminal UI for Harbor runs.** The existing
  `ProgressTracker` and `LiveTrackerDisplay` now receive structured
  Harbor lifecycle events for queued, environment, agent, verifier, success and
  failure states. The same summary is shown and saved as `summary.txt`; timeout
  causes remain visible. Events are retained under `.harbor/events.jsonl` and no
  human-readable Harbor output is parsed. Harbor's own `Running Trials` display
  appeared even with `quiet: true`; worker stdout/stderr now go to
  `.harbor/worker.log` so they cannot overwrite ACB's display.

  Preparation now has an ACB status spinner that names the dataset, inspection,
  asset, provider-image and probe stages before the work queue display starts;
  non-interactive terminals receive plain status lines. Accepted by the user
  after the worker-output and preparation-status changes. Further UI differences
  can be handled as bugs.

## Scripts and tests

- [x] **Audit migration scripts before removing them.** Removed the completed
  Phase 4 pilot runner/summarizer and the overlapping local-model legacy Caveman
  helper. `check_legacy_context.py` retains the stronger deterministic legacy
  contracts. Renamed Phase 6 checks for dataset metrics and multi-step behavior.
  `scripts/README.md` identifies the remaining opt-in integration checks and
  supported shell runners. The checks were inventoried; moving them into one
  shared-fixture integration suite remains an optional future refactor.

- [x] **Prune tests by behavior, not by age.** Removed tests for automatic Harbor
  venv installation, inventory drift, marker publication and interpreter
  overrides with their deleted implementation. Added direct coverage for the
  shared interpreter and terminal lifecycle adapter. Existing cancellation,
  verifier isolation, resource, cache, accounting, metrics and comparison tests
  remain because they cover maintained behavior.

## Code structure and reports

- [ ] **C09 — Separate shared run helpers from the legacy runner.** Harbor currently
  imports `_resolve_run_dir` and `_fetch_vertex_token` from `acb.runner`.
  Move shared output naming and authentication to neutral modules, then retain
  the same collision behavior and credential handling on both paths.

  C04 now extracts output reservation into `acb.run_paths`, used by both backends.
  The remaining work is provider authentication: put it in a neutral module that
  imports neither backend. Verify both paths use the
  effective run ID, missing credentials still fail clearly, and credential values
  are not added to saved plans or logs. Remove Harbor's imports from `acb.runner`
  before C14 removes the runner. Preserve existing patch/import compatibility only
  where it is part of a maintained API.

- [ ] **C10 — Consolidate HTML rendering.** The comparison report uses private
  functions from the older HTML renderer and replaces fragments of generated
  HTML strings. Introduce shared rendering components for individual benchmark
  detail, tool trajectories and charts so standalone and comparison pages use
  one implementation. Preserve the per-benchmark comparison semantics and
  incomplete-measurement labels.

  Load each harness run once, select its task records, and pass them to public
  page/section components. Replace whole-document string replacement and body
  splitting with explicit composition. Keep chart IDs unique, escape embedded
  JSON and evidence, and link or bound large artifacts. Test standalone, one-run,
  multi-harness and multiple-candidate pages, partial measurements and multi-step
  evidence. Verify per-task rendering does not repeatedly parse the entire run.

- [ ] **C11 — Reduce nested plan and result dictionary coupling.** Introduce typed
  structures or validated boundaries for resolved plans, prepared plans and
  imported trial results. Refactor one boundary at a time while preserving
  serialized provenance and the worker protocol.

  Start with prepared worker input, lifecycle events and imported native results,
  which are involved in C02, C03 and C08. Validate required fields and identities
  before side effects; retain JSON as the interchange format. Define protocol
  version rejection and historical result-reading behavior explicitly. Use a
  small set of boundary models rather than converting every internal dictionary
  at once. Cover malformed, missing, unknown and valid fields at each boundary.

- [ ] **C12 — Add more report charts.** This is a presentation follow-up requested
  after Phase 6. Use saved real-run data and show missing or partial values as
  such; never plot incomplete captured tokens as comparable full-run totals.

  Depends on C10. Retain C01's missing-versus-zero distinction. Add per-benchmark grade and
  comparable token-bucket views, with coverage shown beside each comparison. Retain distinct agent-turn,
  model-request and tool-call diagnostics. Use saved real runs plus complete
  fixtures; the saved RH baseline/treatment timeout pair must show unavailable
  comparable totals. Do not create a universal quality score across datasets.
  Verify chart data as well as labels and inspect the generated pages visually.

## Decisions and dependencies

- [x] The example RH baseline and RTK + Caveman configs now use the task's
  1,800-second agent budget. The earlier 600-second results remain historical
  timeout evidence.
- [x] Relative `output_dir` values now resolve from the invocation directory
  for both `acb run` and `acb clean --config`; existing outputs were not moved.
- [ ] **C14 — Remove the legacy runner after both grading-parity gates.** Move SWE-bench and ScarfBench execution and
  grading onto Harbor while preserving their existing configuration and
  extension behavior. Check known passing and failing tasks against the current
  runner's results, then remove `acb/runner.py` and its duplicate scheduling,
  pod orchestration, progress path and obsolete tests. Preserve the public
  `acb run` command, output naming and reports. This is cleanup work; the
  completed migration phases remain closed.

  `acb/harbor/` contains the integration code. Its removal is separate from
  removing the legacy runner or reorganizing Harbor-generated run artifacts.

  The user selected this migration as the next major task. Include C06's installed
  runtime resources in its acceptance checks. Execute the existing H05 tasks as
  cleanup, without reopening migration phases:

  - [ ] **C14a / H05-01 — ScarfBench export and verifier.** Export the complete
    generated project, source task text and migration guidance. Keep grading
    files isolated from agent work. Define the verifier's container-engine
    arrangement explicitly, preserve native `scarf validate`/`make test` semantics,
    and retain compile, deployment and smoke-test evidence. Carry C07's image
    isolation into the exporter.
  - [ ] **C14b / H05-02 — ScarfBench parity.** Use frozen known passing and failing
    generated projects as inputs to both graders. Include build failure, startup
    failure, smoke failure and misleading success-marker output. Compare detailed
    outcomes and artifacts, not model-dependent re-generation. Record image IDs,
    verifier commands and fixture identities with the parity result.
  - [ ] **C14c / H05-03 — SWE-bench export and parity.** Preserve repository/base
    commit, patch application/isolation, official test scripts and per-test
    grading. Compare known passing/failing patches, invalid patches and execution
    errors through both graders. Preserve selection/exclusion and language-runtime
    behavior. Record task, grader and image revisions.
  - [ ] **C14d / H05-04 — Switch execution and retire duplication.** After C14b,
    C14c and C09 pass, route supported existing configurations through Harbor.
    Verify all four harnesses and maintained extension/skill/MCP selections,
    public `acb run`, effective IDs and old report readability. Remove the legacy
    runner, duplicate scheduling/pod/progress code, obsolete compatibility modules
    and tests only after their maintained behavior has replacement coverage.
    Update examples and user-facing migration guidance together. Optional pricing
    is a separate follow-up, not a prerequisite for runner removal.

## Review follow-up: September 18, 2026

The [review](code-review-2026-09-18.md#findings) provides the original findings.
The decisions below supersede its proposed fixes and severity-based sequencing.
The later request to implement C01, C04 and C05 supersedes their initial acceptance.

| Task | Disposition | Implementation and acceptance |
| --- | --- | --- |
| C01 / R01 | Implemented | Require explicit model input/output counts before normalization. Missing usage makes measurement incomplete and excludes token comparisons; explicit zero remains valid. Measurement validation alone does not fail execution. |
| C02 / R02 | Implemented | Keep execution, verification and measurement outcomes separate. Normal failed grades, agent timeouts, wrong/missing grades and incomplete control results do not fail the command. Setup/infrastructure and orchestration failures still do. |
| C03 / R03 | Implemented | Every launcher exception stops and reaps the owned worker session, escalating through interrupt/terminate/kill and preserving the initiating error. |
| C04 / R04 | Implemented | Reserve output with exclusive directory creation in `acb.run_paths`. Keep the original name and `-N` suffix naming; concurrent invocations retry on collisions and retain distinct provenance. |
| C05 / R05 | Implemented | Version 2 inventories include file contents, entry types and permissions, including directories. Old prepared manifests require re-preparation; historical report readers are unchanged. |
| C06 / R06 | Deferred into C14 | Verify required resources from the installed package during the ScarfBench/SWE-bench Harbor migration. The current uncached ScarfBench wheel limitation is accepted until then. |
| C07 / R07 | Implemented | Build with `--iidfile` and run the exact resulting image ID; each conversion retains its image-ID evidence. Older ACB-generated Makefiles are upgraded; upstream recipes are preserved. |
| C08 / R08 | Implemented, permissive | Read/validate native results independently. Import healthy records, represent missing slots, retain damaged files and write `import-errors.json`. Artifact errors alone do not make execution fail or replace an existing failure. |

- [x] **C01 — Missing-versus-zero usage.** Shared parsing in `acb.usage` requires
  model input/output token fields. Cache buckets remain optional and default to
  zero. Both proxy backends retain raw evidence and mark invalid/missing usage
  incomplete without failing execution. `test_measurement_completeness.py` covers
  both backends and verifies preserved grades but unavailable token comparisons.
- [x] **C02 — Separate execution and grading status.** `worker.execution_summary`
  records `execution-status.json`; progress and job logs retain grading errors.
  `tests/test_harbor_execution_status.py` covers failed/missing grades, timeouts,
  control mismatch/missing slots, setup failures and nonfatal report import.
- [x] **C03 — Launcher process ownership.** `backend._stop_worker` handles
  escalation and reaping on all launcher exceptions. `test_harbor_worker_errors.py`
  uses real local subprocesses for malformed events, display failure and ignored
  shutdown signals, plus a repeated-interrupt regression.
- [x] **C04 — Atomic output reservation.** Both backends use `acb.run_paths`.
  `test_run_paths.py` synchronizes six real processes and verifies separate
  directories, owners and effective IDs, plus suffix gaps and occupied names.
- [x] **C05 — Permission-sensitive task snapshots.** `dataset.checksum` hashes
  a versioned inventory; local snapshots use the `local/v2/` namespace.
  `test_harbor_results.py` covers changed executable bits, unchanged-cache reuse,
  directory changes, unsupported entries and rejection of old prepared manifests.
- [ ] **C06 — Installed runtime resources.** Deferred to C14 by user decision.
- [x] **C07 — Concurrent ScarfBench grading isolation.** The generated Makefile
  uses `.acb-validation-image-id`. `test_scarfbench_validation.py` interleaves two
  actual Make executions with a deterministic engine double, checking distinct
  built images and correct project selection. This is not a fresh live-engine run.
- [x] **C08 — Permissive partial-result recovery.** `results.load_results` validates
  native records individually against pinned Harbor's result schema and scheduled
  identities. Worker import errors remain diagnostics; healthy results and missing
  slots remain reportable. `test_harbor_execution_status.py` covers truncated and
  structurally invalid siblings and preservation of a primary orchestration error.

- [ ] **C15 — Make maintenance checks reproducible.** Declare test dependencies
  and document one command that explicitly collects `tests/`; do not collect
  vendored or saved-run tests by default. Add a built-wheel resource check outside
  the checkout and a small required regression gate. Keep live checks opt-in and
  document their engine/model/assets, expected evidence and cleanup checks.
  Inventory Harbor private API access in `contracts`, `environment`, `processes`
  and `metrics`; pin each relied-on contract with focused tests before upgrading
  Harbor. Choose one canonical source for the duplicated Praxis Rust/Cargo assets
  and check packaged/generated copies for drift. Reuse fixtures across live scripts when doing so removes duplication;
  preserve their current behavior coverage. Depends on C06 for the packaging gate.

- [ ] **C16 — Keep one authoritative cleanup record.** Use this document for
  current status and link implementation changes and test/live artifacts when
  closing tasks. Link historical phase/checkpoint documents here without rewriting
  their original outcomes. Mark superseded instructions explicitly, record
  remaining limitations, and remove temporary-file dependencies from active
  instructions. Completion means each open item has an owner/module boundary,
  acceptance criteria and a current status that agrees with the migration index.

## Delivery order and completion rules

1. The selected immediate fixes C01–C05, C07 and C08 are implemented. C06 remains
   deferred to the benchmark migration. Re-prepare old plans before executing
   them with the version 2 task inventory.
2. Next, complete C14a–C14d using frozen grading-parity fixtures. Extract shared
   helpers (C09) before removing the runner and address packaging (C06) within
   that migration, supported by C15's installation/test checks.
3. Complete C10 and incrementally C11/C13, then C12. Structural changes must keep
   existing serialized output and report semantics readable. Maintain C16 as
   implementation and accepted decisions evolve.

For each task, record the implementation reference, focused regression result and
any required live evidence before checking it off. Run the full `tests/` suite
after related changes are integrated. Use live checks for claims involving actual
container cleanup, image isolation, provider behavior or grading parity; mocks
alone do not establish those claims. No paid-model effectiveness experiment is
required to establish deterministic exporter/grader parity. Existing accepted
waivers, including live Docker testing, remain in effect; record the engine used
and do not imply broader provider coverage from that evidence.

Cleanup is complete when the selected tasks' acceptance criteria are met, both
benchmark parity gates pass, the legacy runner is retired, installed resources
work, and no P1/P2 review finding remains silently open. If an item is explicitly
deferred, retain its reason and consequences here rather than checking it off.
