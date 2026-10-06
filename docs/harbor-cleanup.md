# Harbor integration cleanup

Updated October 6, 2026. **This is the authoritative task/status record.**
The migration phases are closed. Phase checklists, reviews and checkpoints record
historical decisions and evidence; they do not define additional open gates.

## Current scope and status

- **Complete:** C01–C10, C14 and C16.
- **Open:** C11 (validated boundaries), C12 (additional charts), and the remaining
  C15 maintenance work.
- **Deferred:** C13 (Harbor agent options schema). Effectiveness experiments are
  user-owned; full dependency locking and amd64 emulation repair remain deferred.
  Docker Quick Start checks now have [scoped evidence](quick-start-validation.md);
  the full Docker harness/isolation matrix remains unverified. MCP selection/validation stays
  supported within its documented adapter limits; broader MCP work is deferred.
- **Backward compatibility is not required.** This supersedes earlier preservation
  requirements for old report formats, private rendering APIs and serialized
  boundaries. Current grade, measurement and execution semantics still apply.
  Historical artifacts may be retained as evidence without supporting their formats.

Latest regression: **635 passed, four opt-in skips**, with two existing
multiprocessing/fork deprecation warnings. C10/C16 verification is detailed below.

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

### C15 — Finish reproducible maintenance checks

**Owner/modules:** `scripts/`, `pyproject.toml`, Harbor adapters in
`acb/harbor/`, and the root/packaged Praxis Rust/Cargo assets.

Done: pytest is declared in the `dev` dependency group, collection defaults to
`tests/`, and the [maintenance guide](../scripts/README.md) documents the regression
and installed-wheel gates. Live checks are opt-in.

Remaining:

1. Inventory private Harbor API use in contracts, environment, processes and
   metrics. Pin relied-on behavior with focused contract tests before upgrades.
2. Choose one canonical source for duplicate Praxis Rust/Cargo assets and enforce
   drift checks for packaged/generated copies.
3. Keep live-check prerequisites, expected evidence and teardown checks documented;
   share fixtures where this removes duplication without losing behavior coverage.

**Acceptance:** the API inventory maps each dependency to a test; a drift check
fails when a generated Praxis copy diverges; documented commands work from a
fresh development setup. Full transitive dependency locking is outside this task.

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

Artifact previews include links to their original local files. Those links require
the original run directory; embedded evidence remains available when a bundle is
copied elsewhere. Chart.js is loaded from a CDN; tables and patch previews work
offline. These are explicit output limitations, not new cleanup gates.

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
3. Finish C15's Harbor API contracts and Praxis drift enforcement.

Keep this document current as each task closes. Record focused/full regressions
and any required live evidence. Use live checks for actual container lifecycle or
provider claims; HTML/documentation changes do not require new paid-model runs.
C13 stays deferred until explicitly resumed.
