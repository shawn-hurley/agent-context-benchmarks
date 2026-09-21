# Configuration UX and structure review

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

> Subsequent scope decision: the review break is over and Phase 4 has resumed.
> It includes extension support for the existing runners. Phase 5 conversion and
> retirement are deferred; Phase 6 is narrowed to four concrete tasks. The
> [revised checklist](harbor-migration-tasks.md#phase-4-shared-extensions-on-harbor-and-existing-runners)
> supersedes the phase sequencing in this completed review.

Reviewed September 17, 2026, after H01-01 through H01-05 and the final regression
and installed-package checks. This assessment supersedes the September 16 review
retained below. See the [task checklist](harbor-migration-tasks.md).

## Phase 4 structure checkpoint

The shared configuration design still fits. Normal runs select named treatments;
preparation handles verified assets, and each backend owns its task lifecycle.
Caveman adapters and the gateway are shared. The retained runner adds only a
small service transport instead of importing Harbor's scheduler. Provider usage
now uses one model-request predicate on both paths, excluding readiness,
discovery and token-count probes. Native hook/wrapper differences stay inside
integration assets. No second configuration framework was introduced.

The portable examples in `config.example/phase4/` resolve for both backends with
identical treatment selections. RTK needs no manually supplied binary for normal
runs: amd64 uses its checksum-pinned static release to avoid QEMU compilation;
ARM64 uses the verified source-build cache. Baseline, response skill, RTK and
context compression remain separate arms; composition/effectiveness stay in
Phase 6. Full dependency locking and MCP remain deferred.

Resource requirements still fail explicitly when unsupported. The Phase 4 pilots
use separately approved task copies omitting unenforceable disk quotas. The
Terminal native pilot additionally builds the unchanged task Dockerfile instead
of selecting its published amd64 image. Both deviations are recorded rather
than hidden in config resolution; task tests and CPU/memory limits are preserved.
The RH source identity/revision is retained on its local copy so the pinned
`testbed` language profile still applies. This is a useful boundary: filesystem
location and benchmark identity are distinct.

The historical assessment below describes the Phase 1 checkpoint; its old Phase
4 status and explicit-binary limitation are superseded by the current checklist.

## Assessment: keep the current design

**Phase 1 is complete under the agreed scope.** The run/catalog/model/machine
separation still fits the intended experiments. One resolver selects settings,
preparation validates what the selected backend can support, and execution
consumes the resulting settings. Removing schema v1 simplified paths and launch
defaults. A broader configuration framework or another orchestration layer is
not warranted by the current implementation.

The six-phase migration remains unfinished. Phases 2/3 retain their accepted
scope and known limitations. This is the requested review break before phase 4.

## What the user configures

| Location | Purpose |
| --- | --- |
| Run YAML | Benchmark/task selection, harnesses, model alias, skills/extensions, concurrency and timeout |
| Catalogs / project registries | Available components, reviewed versions, adapter defaults, benchmark settings |
| `models.yaml` | Alias → provider model ID, endpoint, API mode, credential environment-variable reference |
| `machine.yaml` | Container environment and cache directory; these are its two supported fields |

For an RH baseline, `acb init project` creates a small starter run and model/machine
files. Set the model to the existing local Qwen endpoint configuration. Then:

```sh
acb resolve --config project/run.yaml
```

The resolved document shows exact harness versions, model alias and wire ID,
source files, normalized paths, concurrency, and checks pending preparation.
Task count remains pending until the task manifest is prepared. Resolution makes
no container or model requests. The pinned RH task runtime profile supplies
`testbed` conda activation during preparation; the run no longer needs the interim
shared conda override from the historical review.

For the skill/extension treatment, copy the run and change only its identity and
selected treatment:

```yaml
run_id: rh-treatment
skills: [caveman]
extensions: [rtk]
```

The installed-package check verified that this retains all four harnesses' version,
timeout, launch profile, workdir, conda and prompt settings during resolution.
Preparation still supplies architecture-specific runtime assets. This config check
does not claim successful measured treatment execution; that belongs to phase 4.

Schema v2 is the only format. Relative input paths use the declaring YAML file; relative output paths use the invocation directory;
CLI config paths use the invocation directory. Per-harness lists replace shared
lists, including empty lists. The [precedence and path tables](configuration-ux-plan.md#implemented-precedence-and-validation)
document the exact behavior. Both backends save requested configuration and the
effective execution plan, including original/effective run identities.

## Code boundaries

| Responsibility | Home | Review result |
| --- | --- | --- |
| YAML loading and schema identity | `acb/config.py` | One schema; clear loader errors; shared registry discovery |
| Pure normalization and validation | `acb/resolver.py`, `acb/catalog/` | Central precedence, paths, selections, model identity; no startup |
| Preparation dispatch and assets | `acb/preparation.py` | Shared backend dispatch and explicit legacy limitations |
| Harbor runtime preparation/execution | `acb/harbor/backend.py`, worker/adapters | Receives immutable resolved input; preserves prepared runtime evidence |
| Retained benchmark execution | `acb/runner.py` | Consumes normalized copies without registry merges; scheduler retained for phase 5 parity |
| Configuration records | `acb/provenance.py` | Shared requested/effective records; snapshot isolation and collision identity tested |
| Harness/extension behavior | Existing adapters and integration manager | Reuses the established lifecycle |
| Measurement and grading | Harbor Praxis/results modules | Remain separate from configuration decisions |

Prepared worker payloads remain dictionaries with protocol/runtime validation.
The resolver is a substantial module, but its responsibility is coherent; no
additional framework or speculative module split is needed for this checkpoint.
If future component growth makes it difficult to maintain, extract focused
validators without creating a second resolution path.

## Evidence

- Full regression suite: **354 passed, 4 skipped**.
- Offline source distribution and wheel build succeeded.
- Fresh wheel installed outside the checkout: init (including no overwrite),
  list/help, baseline and Caveman-skill/RTK-treatment resolve, schema-v1 rejection,
  legacy prepare, and packaged-resource checks passed.
- Execution boundary tests cover one-time resolution, plan handoff, requested
  snapshot isolation, model alias/wire ID, credential references, run-directory
  collisions, and saved-plan consistency.
- Saved evidence: `runs/harbor-phase1-review/`. Wheel SHA-256:
  `c19bd12d2ef33cf0d2a6ed4a81996d35d0f0deb0b9c2316f74456415e9b7854f`.

No live container or model rerun was part of this configuration checkpoint.
Existing phases 2/3 runtime evidence remains the acceptance basis.

## Remaining scope, without reopening completed phases

- Phase 4: extension delivery/treatment validation and the RH measured pilot.
- Phase 5: ScarfBench and SWE-bench export/grading parity, later configuration
  completion such as optional pricing, and retirement of the old scheduler.
- Phase 6: broader Harbor capabilities and Caveman protocol/composition coverage.
- Legacy execution still requires Podman, online mode, one attempt, and explicit
  local assets for treatments needing preparation. Its prepare result says
  `probed: false`; Harbor owns the fuller preparation contract.
- MCP and full dependency locking remain deferred. Live Docker testing remains
  waived; affected Claude amd64-emulation startup is an accepted known issue.

No additional Phase 1 task is introduced by this review. Pause here for the user
before starting phase 4.

---

## Historical September 16 assessment (superseded)

The following records the original concerns. Its outstanding-work list is not a
current task list; the assessment above and migration checklist govern scope.

Reviewed September 16, 2026, after the accounting checks and RH baseline
investigation. See the
[implementation checkpoint](harbor-migration-status.md) and
[acceptance checklist](harbor-migration-tasks.md).

## Assessment

**Keep the overall design. Finish its boundaries before expanding the migration.**
The recent failures reinforce the need for pure configuration resolution, explicit
preparation, runtime checks before inference, and independent measurement/grading
records. They do not justify exposing Harbor implementation details in every run.

The configuration UX is partially implemented. It is not yet consistent across
the Harbor and legacy execution paths, and preparation does not yet establish
every promised runtime requirement. The six-phase migration remains unfinished.

## User-facing configuration

Keep ordinary experiment choices in the run: benchmark/task selection, harnesses,
model alias, extensions, and execution budget. Keep endpoints and credential
variable names in model configuration; keep artifact pins and adapter selection
in the catalog/prepared plan.

This is the intended shape for an RH baseline using the existing parser. It is
not a claim that all four harnesses work on this host:

```yaml
schema_version: 2
run_id: rh-baseline
benchmark: rh-swe-bench
harness: [goose, pi, opencode, claude-code]
model: local-qwen
subset: [task-0000]
skills: []
extensions: []
execution:
  environment: podman
  max_workers: 1
  timeout: 1800
overrides:
  harness:
    conda_env: testbed
```

The `local-qwen` model definition supplies the wire model ID, OpenAI API mode,
and `127.0.0.1:8000` endpoint. A remote engine needs an endpoint reachable from
that engine's containers; host loopback cannot be assumed portable.

The shared conda override is an interim, explicit requirement for this RH task.
The first pilot omitted activation and Pi repeatedly failed to import `yaml`.
The task's verifier activates `testbed`. The diagnostic rerun removed those errors
but still timed out at 600 seconds with verifier reward 0. A future benchmark/task runtime profile
should declare the appropriate language environment, preparation should verify
it, and every harness should receive the same result. Do not assume every Harbor
task uses conda or infer requirements from hidden verifier scripts at runtime.

The 1800-second example budget matches this task's declared agent budget. The
diagnostic pilots used 600 seconds; their timeout outcomes must retain that
distinction. Raising a budget is an experiment change, not an infrastructure fix.

## Code responsibilities

| Boundary | Current home | Keep / finish |
| --- | --- | --- |
| Parse user configuration | `acb/config.py` | Keep compatibility parsing separate from normalization |
| Resolve selections and defaults | `acb/resolver.py`, `acb/catalog/` | Keep pure; finish precedence, provenance, path handling, and legacy consumption |
| Prepare data, assets, and runtime | `acb/preparation.py`, `acb/harbor/backend.py`, `dataset.py`, `preflight.py` | Preserve prepared evidence; reject incompatible matrix entries before model calls |
| Schedule task environments | Harbor worker and environment adapter | Keep scheduling in Harbor; provider compatibility belongs here |
| Launch harnesses and extensions | Existing harness adapters, transport, integration manager | Reuse lifecycle; avoid a second extension framework |
| Collect model usage | `acb/harbor/praxis.py` | Preserve raw requests; distinguish collection from complete accounting |
| Import grading and failures | `acb/harbor/results.py` | Preserve rewards, failed steps, missing trials, and unknown resolution independently |

The transport bridge remains useful: it lets existing synchronous adapters use
Harbor's environment API without duplicating every harness. Its cancellation
boundary needs continued coverage, but the live interrupted-trial check now
demonstrates the expected behavior for a long-running tool.

## Changes to prioritize when implementation resumes

1. **One normalized execution contract.** The legacy runner still bypasses parts
   of the resolver. Do not add more user syntax until resolve/prepare/run agree
   about existing selections and precedence.
2. **Explicit stages for internal plans.** `ResolvedPlan` is immutable canonical
   JSON, but preparation and workers then exchange mutable dictionaries. Validate
   resolved versus prepared payloads at their boundaries, including task runtime
   and provenance. Prefer small internal schemas over a new workflow framework.
3. **Complete the task runtime contract.** Architecture/workdir/user/Python are
   insufficient when the benchmark also needs an activated language environment.
   Preserve exact binary startup evidence and identify unsupported combinations
   during preparation. Image identity, resources, and network constraints remain
   outstanding.
4. **Make preparation errors actionable.** Show the failing task/harness and the
   artifact path directly. The new startup artifact explains the Claude crash;
   the outer preparation error still requires inspecting the trial directory.
5. **Keep extension details internal.** RTK mode selection and Caveman runtime
   assembly belong in catalog/adapters. Validate actual skills/MCP loading before
   declaring shared configuration supported.
6. **Retain independent benchmark parity gates.** ScarfBench's build/deploy/test
   semantics and SWE-bench's official grading must survive export. Existing paths
   remain until those comparisons pass.

## Acceptance before calling the UX complete

- A small baseline config resolves without containers or model requests.
- Resolve output explains selections, sources, versions, budget, and pending checks.
- Prepare rejects unusable binaries and missing language environments before inference.
- Run consumes that prepared experiment without another settings merge.
- Baseline/treatment differ only in declared treatment settings.
- A packaged invocation outside the checkout behaves the same way.
- Both existing benchmark families retain independently verified grading behavior.

No broader configuration or architecture refactor is part of this checkpoint.
The next implementation pass should address these concrete gaps while retaining
the agreed user-facing model.
