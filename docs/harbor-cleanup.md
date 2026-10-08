# Harbor integration cleanup

Updated October 8, 2026. **This is the authoritative task/status record.**
The migration phases are closed. Phase checklists, reviews and checkpoints record
historical decisions and evidence; they do not define additional open gates.

## Current scope and status

- **Complete:** C01–C10, C14–C16 and the approved [runtime cleanup findings F01–F32](runtime-cleanup.md).
- **Open:** C11 (broader typed boundaries) and C12 (additional charts).
- **Deferred:** C13 (Harbor agent options schema). Effectiveness experiments are
  user-owned; full dependency locking and amd64 emulation repair remain deferred.
  Docker Quick Start checks now have [scoped evidence](validation/quick-start-validation.md);
  the full Docker harness/isolation matrix remains unverified. MCP selection/validation stays
  supported within its documented adapter limits; broader MCP work is deferred.
- **Backward compatibility is not required.** This supersedes earlier preservation
  requirements for old report formats, private rendering APIs and serialized
  boundaries. Current grade, measurement and execution semantics still apply.
  Historical artifacts may be retained as evidence without supporting their formats.

Latest runtime cleanup verification (October 8) is recorded in
[the runtime cleanup acceptance record](runtime-cleanup.md#verification-and-limits).
The [documentation index](README.md) groups current guides, validation records
and archived plans. Historical C10/C16 verification is detailed below.

## Open work, owners and acceptance

### C11 — Validate plan and result boundaries

**Owner/modules:** `acb/resolver.py`, `acb/harbor/worker.py`,
`acb/harbor/events.py`, `acb/harbor/progress.py`,
`acb/harbor/results.py`.

Introduce a small set of typed structures or validated boundaries for prepared
worker inputs, lifecycle events and imported native results. Validate fields and
identities before side effects. Keep JSON as the interchange format and reject
unsupported protocol versions explicitly. Convert one boundary at a time; old
formats need no compatibility layer.

**Acceptance:** malformed, missing, unknown and valid fields have focused tests;
normal grading/measurement failures retain the permissive behavior in C02/C08;
run provenance accurately describes the inputs used. Coordinate ownership of
agent option validation with C13.

### C12 — Additional report charts

**Owner/modules:** `acb/html_report.py`, `acb/report_metrics.py`,
`acb/comparison_html.py`, `acb/html_components.py`.

C10's shared components are ready. Add per-benchmark grade and comparable token
bucket views with coverage alongside each comparison. Keep agent turns, model
requests and tool calls distinct. Missing/partial measurements must not appear
as comparable full-run totals. Do not create a universal quality score across
datasets.

**Acceptance:** chart data and labels are tested and generated pages are visually
inspected. Use complete fixtures and saved partial/timeout runs; the latter must
show unavailable comparable totals. This work adds new charts; C10 retained and
consolidated the existing request/tool charts.

### C13 — Deferred: Harbor agent options contract

**Owner/modules:** `acb/harbor/agent.py` and Harbor agent schema/preflight integration.

The investigation is complete: pinned Harbor 0.23.0 logs that `acb` has no
`options_model`, even for empty kwargs. ACB consumes `plan` and `harness` before
calling the base constructor; the warning alone does not establish lost settings.
Supported validation remains deferred.

**Acceptance when resumed:** use Harbor's supported options contract, test both
preflight and construction with valid/unknown options, and coordinate with C11.
Filtering the log is not a fix.

### C15 — Reproducible maintenance checks (complete)

Private Harbor dependencies in contracts, environment, process ownership and
metrics are mapped to tests in [the API inventory](harbor-api-contracts.md).
The focused contracts inspect the pinned provider APIs and bind an actual Harbor
job's script metric to the container executor without running it on the host.

`acb/assets/praxis` is the sole Praxis source. Installed-wheel checks validate
runtime resources and complete bundled workflow contexts. Shared helpers and the
pinned rgctl bundle are canonical under `acb/workflows/_shared`; workflow snapshot
validation rejects changed canonical bytes. No duplicate Praxis source remains
to drift. Live RTK fixtures use Harbor transports, retain deterministic paired
assertions, preserve no-network policy and verify fixture-owned teardown.

Pytest remains in the dev dependency group; maintenance commands, live
prerequisites and acceptance limits are documented in [scripts](../scripts/README.md).
Full dependency locking and the live Docker harness matrix remain deferred.

## Completed cleanup

| Task | Implementation and acceptance evidence |
| --- | --- |
| C01 — Missing versus zero usage | `acb/usage.py` requires explicit model input/output counts; cache buckets default to zero. Missing measurement excludes comparisons without failing execution. `test_measurement_completeness.py`, `test_usage.py`. |
| C02 — Execution versus grading | Failed/missing grades, agent timeouts and incomplete measurements are normal outcomes. Infrastructure/setup/orchestration failures and cancellation fail execution. `test_harbor_execution_status.py`. |
| C03 — Worker process ownership | Launcher exceptions stop/reap the owned session with escalation and preserve the initiating error. Real process tests in `test_harbor_worker_errors.py`. |
| C04 — Atomic run names | `acb/run_paths.py` reserves directories exclusively and retries `-N` suffixes. Six-process coverage in `test_run_paths.py`. |
| C05 — Task snapshots | Version 2 inventory hashes contents, types and permissions. `test_harbor_results.py`. |
| C06 — Installed resources | ScarfBench Containerfile/prompts and native grader bridge are packaged. `scripts/check_package.py` validates installed imports/resources and offline export outside the checkout. |
| C07 — ScarfBench image isolation | Native grading uses the exact image ID from `--iidfile`; interleaved Make/engine-double tests in `test_scarfbench_validation.py`. |
| C08 — Partial results | Native records are validated independently; healthy records and missing slots remain reportable. Import errors alone do not fail execution. `test_harbor_execution_status.py`. |
| C09 — Shared helpers | Output naming, authentication and usage parsing live in `acb/run_paths.py`, `acb/auth.py`, `acb/proxy/metrics.py`. No runner imports remain. |
| C10 — HTML composition | Shared report loading, page/section/chart/evidence components and benchmark detail rendering replace private cross-module calls, whole-page string replacement and iframe composition. See [report architecture](report-rendering.md). |
| C15 — Reproducible maintenance | Pinned Harbor API inventory/contracts, one packaged Praxis source, shared pinned workflow assets, installed-resource and snapshot checks. [Runtime cleanup](runtime-cleanup.md). |
| C14 — Benchmark migration/runner retirement | SWE-bench, SWE-bench Lite and ScarfBench run through Harbor with native graders. Legacy scheduling, pod lifecycle and proxy/integration runtimes are removed. [Migration evidence](harbor-benchmark-migration.md). |
| C16 — Documentation consistency | This record owns current status. Migration/design checkpoints are marked historical; active operations, report architecture and maintenance commands agree with the implementation. Old-format compatibility requirements are superseded. |

Also complete: visible per-trial logs, hidden native `.harbor/` records, streaming
Praxis diagnostics, run-root job summaries, ACB terminal progress, shared Python
runtime, Podman copy fallback, migration-script retirement and test pruning.
Their operating behavior is documented in [operations](harbor-operations.md).

### C10/C16 verification

Focused rendering checks cover standalone and one-run bundles, multiple harnesses
and candidates, unique chart IDs, selected-trial data, one read per harness input,
partial measurements, multi-step trajectories, escaping, and bounded artifact
previews. No old-format or private API compatibility adapters were added.

Verification: `uv run --offline pytest -q` passed **474 tests, four skipped**.
The eight focused rendering tests also passed after strengthening the one-read
fixture. A local Chrome check rendered all ten inline fixture charts and six
saved RH detail charts, with no JavaScript errors, duplicate IDs or overview
horizontal overflow. Screenshots and browser results are retained under
`runs/html-rendering-check-20260921/`. The saved RH timeout comparison still
shows unavailable comparable grades/tokens. Relative documentation links and
`git diff --check` passed.

At that checkpoint, file links required the original run and charts required a
CDN. Those limitations have since been resolved: current exported reports bundle
Chart.js locally and copy linked evidence for offline sharing. See
[report rendering](report-rendering.md) for current export behavior.

### Migration verification retained

Commits `8d140e0` (implementation) and `2adda76` (examples/docs) completed C09/C14.
Podman controls and native failure matrices passed under
`runs/harbor-retirement-check-20260921/`. Caveman recovery isolation evidence in
`runs/harbor-store-isolation-20260921/check.json` records exact local recovery,
rejected cross-trial retrieval and clean teardown. Installed-wheel checks passed.
No paid model run or live Docker run was needed for those gates.

## Delivery order

1. C11: validated boundaries.
2. C12: additional charts using the shared renderer.

The protocol-2 worker now rejects unsupported plans before setup; the broader C11
typed input/event/result work remains open. Keep this document current as each task closes. Record focused/full regressions
and any required live evidence. Use live checks for actual container lifecycle or
provider claims; HTML/documentation changes do not require new paid-model runs.
C13 stays deferred until explicitly resumed.
