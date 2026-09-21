# Harbor migration tasks

Updated: September 21, 2026.

**Benchmark migration update:** SWE-bench, SWE-bench Lite, and ScarfBench now
default to Harbor. Task export, controller-side native verifiers, fixed-fixture
grading parity, and C06 packaging are implemented. See
[benchmark migration and evidence](harbor-benchmark-migration.md). H05-04 is complete: Harbor is the sole runner, shared helpers are extracted,
and legacy scheduling is removed. [Cleanup status](harbor-cleanup.md) is
authoritative; deferred-phase notes below describe earlier checkpoints.

**Phase 6 implementation complete under the selected scope.** See the [Phase 6 checkpoint](harbor-phase6-checkpoint.md) and [post-integration cleanup](harbor-cleanup.md). H06-05 experiments are user-owned; Phase 5 was subsequently completed as cleanup C14. Phase 6 checkpoint regression: 413 passed, four existing skips. All controlled checks were rerun after the user cleaned `runs/`; a fresh Qwen RH baseline/combined pair and real-data reports are retained. Both RH agents timed out at 600 seconds, explicitly shown as incomplete comparisons.

**September 18 cleanup review:** [review findings](code-review-2026-09-18.md) and
[prioritized cleanup tasks](harbor-cleanup.md#review-follow-up-september-18-2026)
track the review findings and existing structural work. Subsequent user decisions
initially accepted C01/C04/C05 unchanged, then authorized their implementation:
missing-versus-zero measurement validation, atomic output reservation and versioned
permission-sensitive task inventories are now implemented. Packaging C06 remains
deferred into the next Harbor benchmark migration; C02/C03/C07/C08 retain permissive
execution/result handling.
Those fixes are implemented: **465 passed, four skipped**, including local process
cleanup, concurrent reservations, task/measurement integrity and interleaved
Make/engine-double checks. No new live container/model gate
was run. H05-01 through H05-04 are mapped to cleanup C14a–C14d, retaining both grading
parity requirements before legacy-runner retirement. Accepted phase closures and
existing live-test waivers remain in effect.

**Phase 4 complete.** See the [completion checkpoint](harbor-phase4-checkpoint.md). All functional gates and approved pilots are complete. Regression: 388 passed, four skipped; package and cleanup checks passed.

**September 17 review completed under revised scope.** See the
[phase audit](harbor-phase-2-3-audit.md) and [operation/support notes](harbor-operations.md).
Live Docker testing is waived by user decision; provider-neutral Harbor routing
and installed-worker configuration were reviewed for both engines. Offline use
retains documented natural engine failures and ACB asset-download controls, without
claiming that `offline` prevents every task-image network attempt. The final
package/CLI/evidence review passed. Claude amd64/QEMU startup failure is now an
accepted [known issue](harbor-operations.md#known-issue-claude-code-on-emulated-amd64-benchmarks),
with further investigation deferred by user decision. Phases 2/3 are complete
under this scope. Phase 1 and the UX/structure review are now complete;
Phase 4 is complete under the revised scope below. Historical
progress notes below do not create new gates.

Implementation order: **phases 3, 2, 1, and 4 complete**,
per the user's revised priority. H03-10/11 are phase 1 parent tasks, broken down
into H01-01 through H01-06 below. H03-13 is complete. See the
[historical status notes](harbor-migration-status.md) for earlier live evidence,
the first real Qwen pilot, known issues, and the next execution order. These are
repository tasks; they have not been added as native Codex tasks.
The [accounting/RH follow-up](harbor-accounting-rh-checkpoint.md) records H03-14/17
closure and historical RH failures. The
[UX and structure review](harbor-ux-structure-review.md) preserves the design
assessment requested before the next implementation phase.

Scope: the six-phase migration is authorized, with MCP deferred from this implementation
per the user's September 16 scope update. Skills remain in scope. Phases 2/3 are complete within the accepted scope, including
Docker/Podman interface compatibility (live Docker testing waived) and the known
Claude emulation limitation. Phase 1 is complete, including the resolver fixes and review below. Phase 4 functional acceptance and approved matched pilots are complete.
Phase 6 implementation is complete; effectiveness experiments remain user-owned.

Checked tasks have evidence. An implemented component does not close a live
acceptance gate. All tasks are owned by the migration implementation effort.

Additional scope decisions (September 16): full dependency locking is deferred;
fresh installations may resolve different dependency versions while runtime
inventory validation and offline checks remain. Exact transitive version and
artifact-hash locking is not a phase 2/3 exit gate. For rgctl, assume the binary
is usable on the target architecture; architecture investigation is outside its
acceptance check. Cached rgctl binary installation and execution now pass on all
four harnesses (evidence under H03-19).

## Phase 1: discrete implementation tasks

September 17 scope update: schema v1 support is removed. Schema v2 is the default
and only accepted version. Compatibility branches and v1 behavior tests are
removed; rejection tests cover retired versions. Earlier test counts below are
historical evidence, not a requirement to retain compatibility. Inline component
settings supported in v2 and the existing benchmark execution backend are separate
from configuration schema versions; phase 5 still owns backend retirement.
Verification after removal: 311 tests passed and 4 skipped in the sandbox; the
one sandbox-blocked process-cleanup test passed separately with permission to
run `ps` (312 passed total). Run examples explicitly declare schema 2 and their
registry paths are relative to the example file; output paths are relative to the invocation directory.


These tasks finish the existing phase 1 scope; H03-10/11 remain parent references,
not additional work. Execution order is H01-01 through H01-06. Existing resolver,
CLI, and runtime evidence is retained. These are repository tasks, not native
Codex tasks. No live model or container run is required for pure resolver changes.

- [x] **H01-01 — Preserve component asset path origins** (H03-11).
  Normalize local skill source paths and RTK binary paths from catalogs, harness
  registries, shared overrides, and per-harness overrides before merging them.
  Completion: tests from an unrelated working directory cover catalog defaults,
  selection options, inline legacy assets, replacement lists, immutable inputs,
  and the current schema. Container interpreter/workdir paths retain their
  container meaning.
  Evidence: 40 resolver tests pass, including 17 new path/replacement cases;
  67 related preparation/component/skill tests pass. No containers or model
  requests were used. Catalog option defaults now retain their catalog origin,
  while explicit selection options use the file containing the selection.
- [x] **H01-02 — Finish configuration discovery and remaining path rules** (H03-11).
  Audit run/config-dir, benchmark, machine cache, and proxy executable paths;
  distinguish file-declared paths from command-line paths and dataset-internal
  task roots. Completion: documented path table and outside-checkout CLI tests
  prove declaring-file behavior. Reuse existing
  benchmark/cache/interpreter-symlink coverage.
  Evidence: 57 resolver/CLI tests and 71 related preparation/component/skill/proxy
  tests pass. The fresh-process resolve test runs outside the checkout; CLI
  boundary tests cover resolve/prepare/run registry selection. See the
  [path table](configuration-ux-plan.md#implemented-path-rules). No live run or
  new installed-wheel check is claimed; legacy consumption remains H01-04.
- [x] **H01-03 — Close normalization and precedence gaps** (H03-10).
  Audit supported registry/run fields and machine defaults; reject ambiguous
  legacy/new declarations and unsupported options with field-specific errors.
  Completion: a documented precedence table and focused tests cover supported
  locations, explicit empty selections, model aliases, budgets, and backend
  selection without container startup or model requests. MCP remains deferred.
  Evidence: 82 resolver/configuration tests and 71 related preparation/component/
  skill/proxy tests pass (153 total). Precedence tests prohibit process/network
  startup. Supported SWE-bench/Lite/ScarfBench registry fields now resolve; malformed
  YAML, unknown machine/model fields, invalid budgets/booleans, inline skill errors,
  and hidden shared-selection conflicts fail early. See the
  [precedence table](configuration-ux-plan.md#implemented-precedence-and-validation).
- [x] **H01-04 — Make execution consume the normalized plan** (H03-10).
  Replace legacy runner settings merges with resolved benchmark, harness, model,
  proxy, cache, and concurrency values; use the same backend selection as resolve.
  Reuse existing asset preparation where needed and report unsupported combinations
  before side effects. Completion: boundary tests show resolve/prepare/run agree
  and per-instance execution does not re-read registries or re-merge overrides.
  Retain the legacy scheduler until phase 5 grading parity.
  Evidence: `tests/test_execution_plan.py` adds eight boundary cases; the focused
  execution/configuration/preparation/component/skill/proxy suite passes 161 tests.
  Runner dispatch resolves once; Harbor receives `ResolvedPlan` through `run_plan`.
  Legacy workers receive copied normalized settings without registry access;
  model aliases use the wire ID, and paths/concurrency use resolved values.
  CLI prepare and legacy run share preparation validation. Unsupported legacy
  Docker/offline/repeated-attempt/catalog-auto-preparation combinations fail before
  dataset/output side effects. Existing per-instance legacy asset setup remains;
  no new container run or benchmark grading-parity claim is made.
- [x] **H01-05 — Preserve requested/resolved configuration through execution** (H03-10).
  Audit both execution paths for saved provenance and immutable handoff; repair
  concrete omissions, including aliases and effective output/run identity.
  Completion: tests prove saved resolved settings match those consumed, retain
  credential variable references without values, and preserve baseline/treatment
  launch parity. No new general workflow framework.
  Evidence: both backends now write `requested.json` from the snapshot captured
  during resolution and `resolved.json` from the effective prepared plan before
  execution. Records retain model alias/wire ID, requested/effective run IDs,
  run directory, normalized settings, sources, and preparation evidence. Harbor
  passes that exact saved file to its worker. Legacy boundary tests compare
  submitted settings to the saved file; collision and post-resolution mutation
  tests cover both backends. Model credential values are not read into these
  records; `key_env` references remain. Requested metadata redacts inline
  credential keys and environment maps. Saved baseline/RTK treatment tests retain
  launch parity across all four harnesses. Focused suite: 182 passed.
- [x] **H01-06 — Review Phase 1 UX and code structure with the user.**
  Update the existing UX/structure review using completed evidence; walk through
  a minimal baseline and a skill/extension treatment from outside the checkout.
  Run the relevant regression suite and package/CLI check after implementation.
  Completion: summarize configuration layers, module responsibilities, remaining
  later-phase work, and limitations; close H03-10/11 only when their child tasks
  pass. Take the requested review break before starting phase 4.
  Evidence: full suite 354 passed, 4 skipped; offline sdist/wheel build and fresh
  installed-wheel CLI checks outside the checkout pass. Baseline and skill/RTK
  treatment resolve with unchanged launch settings across four harnesses. Init
  preserves files, schema 1 is rejected, list/help/resources and legacy prepare
  pass. Evidence: `runs/harbor-phase1-review`. The
  [current UX/structure assessment](harbor-ux-structure-review.md) recommends
  retaining the architecture and records later-phase limits. No new live run.
  **Paused for the requested user review before phase 4.**

## Phases 2/3: completed

- [x] **H03-01 — Isolate Harbor and define the worker protocol.** Pin Harbor
  0.23.0 in a Python 3.12 environment; reject incompatible worker protocols and
  package versions. Implement prepare, probe, run, and grading-control entry points.
- [x] **H03-02 — Bridge all four existing harness adapters.** Execute Goose,
  Pi, OpenCode, and Claude Code through Harbor's environment operations, using
  the task workdir and actual container architecture. Preserve their existing
  launch/configuration paths.
- [x] **H03-03 — Repair macOS Podman Compose compatibility.** Detect the actual
  Compose frontend, copy logs across the VM boundary, initialize log directories,
  and disable PTYs for command execution. Prefer the tested podman-compose
  frontend explicitly when installed so installing Docker Compose cannot silently
  change Podman behavior (verified again September 16).
- [x] **H03-04 — Wait for the actual agent process and capture output.** Use
  `setsid --wait`; retain stdout/stderr transcripts and the harness exit code.
  The four-harness fixture exposed and verified this fix.
- [x] **H03-05 — Run a dedicated Praxis service per trial.** Configure the
  sidecar, wait for readiness, stop it using SIGINT, and collect usage before
  Harbor teardown. All four fixture trials report complete measurement collection.
- [x] **H03-06 — Establish positive four-harness Podman evidence.** All four
  pinned harnesses earn reward 1 in `runs/harbor-bridge-baseline-3` using the local
  deterministic model server, including Claude's Anthropic-to-OpenAI route.
- [x] **H03-07 — Validate the first RH SWE-bench grading controls on Podman.**
  Pinned dataset revision `a31c36f6cb6d2c9f3caea36745564b3f4958239c`, task
  `task-0000`: no-op reward 0 in `runs/harbor-rh-nop`; oracle reward 1 in
  `runs/harbor-rh-oracle`.
- [x] **H03-08 — Import native rewards and failure evidence.** Preserve numeric
  reward dictionaries, exceptions, trial/task identity, and raw artifacts;
  distinguish malformed/missing rewards from a valid zero. Include failed trials
  with zero model traffic in reports.
- [x] **H03-09 — Preserve primary errors and return failing job status.** Cleanup
  errors have their own artifact and do not replace an agent error. Failed or
  incomplete measured jobs cause a nonzero worker exit.

## Acceptance checklist and phase 1 parent tasks

- [x] **H03-10 — Complete pure resolver normalization.** Use the same normalized
  selections for resolve, prepare, and execution; normalize selections supplied
  through every supported registry/override location. Reject ambiguous legacy
  settings and unknown fields. Add precedence tests, including machine defaults.
  Progress: registry/shared/per-harness extension selections now normalize;
  catalog defaults and reviewed versions are checked. Resolver tests: 23 passed.
  Completed through H01-03/04/05/06: both execution paths consume normalized
  settings and save configuration provenance; the final review and package checks pass.
- [x] **H03-11 — Finish declaring-file path semantics.** Resolve schema-v2 run,
  registry, machine, binary, task, and cache paths against the file that declares
  them. Schema v2 is now the only supported configuration format. Verify execution outside the
  repository checkout.
  Progress: benchmark and machine paths use their declaring files under v2;
  run overrides retain run-relative paths. H01-01 completes catalog and inline
  skill/RTK asset origins, preserving container interpreter paths. H01-02 completes
  discovery, remaining executable paths, and outside-checkout CLI acceptance.
  H01-04/06 now verify execution consumption and the final packaged UX checkpoint;
  this parent task is closed.
- [x] **H03-12 — Freeze task/provider/harness runtime plans on Podman.** Resolve and record
  image identity, actual architecture, workdir, user, resources, and prerequisites
  before measured work. In particular, do not select host-architecture RTK binaries
  for RH SWE-bench's amd64 images. Reject unsupported matrix combinations early.
  Progress: disposable inspection now freezes per-task architecture, workdir, user,
  and Python interpreter; per-task extension plans consume and recheck that contract.
  Installed binary startup/version checks now run before model work. Task-owned
  language profiles now verify conda activation and freeze interpreter/prefix
  evidence; the pinned RH revision declares testbed, and conflicting harness
  overrides fail preparation. Live RH inspection passes in
  `runs/harbor-rh-language-check-podman`, confirming the testbed interpreter.
  Main-container immutable image identity, CPU/memory enforcement, mount
  permissions, and network capability/policy are now frozen and rechecked.
  Preparation retains images through teardown; prepared runs select the inspected
  image with pulling/rebuilding disabled. `runs/harbor-retained-image-check` and
  `runs/harbor-pinned-contract-recheck` prove stable image reuse with 1 CPU and
  512 MiB. Mismatched image evidence is rejected in `runs/harbor-image-drift-check`.
  Task Compose service images (including the egress controller when present)
  are now inspected, pinned, and rechecked. Preparation resolves Praxis/Caveman
  references to immutable IDs; execution verifies provider container identities.
  `runs/harbor-service-image-check-2` verifies stable main/task/Praxis image IDs
  through all four harnesses, each reward 1. Separate-verifier preparation now
  inspects its images/resources/network baseline and freezes image selection;
  execution rejects unprepared verifiers and changed contracts before grading.
  `runs/harbor-verifier-contract-check` proves reuse of the inspected verifier
  contract, reward 1, preserved isolation and no remaining trial containers.
  `runs/harbor-caveman-image-check` verifies the pinned Caveman image in record
  mode across all four harnesses, each reward 1 with complete measurements.
  Live Docker repetition was waived September 17 (H03-20); compression effectiveness is later-phase work.
- [x] **H03-13 — Preparation/cache review complete within documented offline scope.**
  Verify pinned Praxis builds, artifact hashes, atomic cache publication, concurrent
  preparation, and cache invalidation. Ensure offline registry/dataset/harness
  preparation never silently downloads assets. Probe actual executable versions.
  Progress: local task snapshots use content-addressed atomic publication;
  registry offline resolution explicitly rejects network access. Download helpers
  add deadlines/cancellation. Provider image references now freeze to immutable
  IDs, with no missing-image pull in offline mode. Harbor task-image setup retains
  the documented engine-network limitation accepted for the current offline scope.
  Historical acceptance used managed worker runtime preparation that validated Python 3.12, Harbor and support
  imports, and the recorded package inventory under a process lock. Readiness is
  published atomically after validation; failed installation removes readiness.
  Offline use rejects legacy, malformed, or changed runtime records without
  installing. Focused preparation/config tests: 35 passed, including two-process
  preparation (one installation) and offline dependency drift. The existing worker
  interpreter passes the actual inventory probe (Harbor 0.23.0, 95 distributions).
  The post-integration cleanup later removed this separate managed environment;
  Harbor is now a pinned core dependency in ACB's environment. Dependency locking across fresh installations is deferred; broader asset
  concurrency remains open. The inventory records versions, not package-content hashes.
  RTK cache reuse now validates source revision, architecture, recipe, checksum
  and executable permission. Binary and manifest publish together from staging;
  failed copy/publication preserves an incomplete prior entry. Offline incomplete
  caches fail without invoking the engine. Focused preparation/download suite:
  28 passed, including two-process RTK preparation with one simulated build,
  provenance corruption and publication failure. Actual container building was
  mocked in these cache tests; later cache checks are recorded below.
  Managed provider builds now use a snapshot of their build context and recipe,
  record provenance labels plus a manifest with immutable image identity, and
  reject tag/manifest drift. Preparation returns the immutable ID directly.
  `runs/harbor-provider-cache-check` verifies concurrent preparation, offline
  reuse, and rejection of changed inputs on Podman using a small local fixture.
  Unit coverage adds altered tags/labels/manifests and failed-build cleanup.
  This does not lock base images or downloaded dependencies. Legacy automatically
  generated provider tags need one online preparation under the new cache format;
  explicitly configured image references retain their existing pinning path.
  Remote skill caches now key Git entries by repository/ref and release entries
  by resolved version/source/architecture. They validate file hashes and modes,
  require valid SKILL.md and the requested release executable, and publish the
  complete directory with its manifest atomically. Corruption fails explicitly;
  offline misses cannot clone/download. Six focused skill-cache tests include
  concurrent reuse of a real local Git clone. Release-transfer tests use mocked
  downloads. Legacy remote cache entries need online population of the verified
  format; explicit local skills (including the tested rgctl cache) are unchanged.
  Branches and `latest` retain existing resolution behavior; dependency locking
  remains deferred.
  All four harnesses now extract into staging and atomically publish a complete
  entry with file hashes and permission bits. Newly published entries detect
  corruption on reuse; extraction/publication failures cannot expose partial
  binaries. Thirteen archive/cache tests cover all four adapters, concurrent
  preparation, interrupted extraction, offline misses and restoration on failed
  publication. Downloads use fixture archives in these tests.
  `runs/harbor-harness-cache-check/cache-check.json` confirms actual existing
  caches are reusable offline. Legacy entries retain startup/version validation
  but do not gain retrospective file-integrity guarantees. Offline task-image
  behavior is documented in the operations guide; no strict network-isolation
  guarantee is claimed.
- [x] **H03-14 — Verify accounting boundaries.** Exclude preparation health
  requests, retain each agent request exactly once, and keep verifier traffic out
  of agent usage. Check token totals against the deterministic server and keep
  unknown costs unknown. Repeat after the latest health-reset change.
  Evidence: `runs/harbor-accounting-check` reconciles all four harnesses against
  the deterministic server request ledger: Goose/OpenCode 6 calls and 720 tokens;
  Pi/Claude 5 calls and 600 tokens. Every trial earns reward 1. Discovery is excluded,
  request IDs are unique, and verification confirms the proxy is stopped. No
  monetary costs are invented. Historical pilot turn counts remain uncorrected.
- [x] **H03-15 — Prove timeout and cancellation cleanup on Podman.** Interrupt a running
  agent and a streaming request; verify the actual process group and all
  trial-owned services stop, partial evidence survives, and no containers remain.
  Repeat for cancellation during setup and startup failures.
  Progress: live Goose long-running-tool cancellation and agent-timeout checks pass
  on Podman (`runs/harbor-cancellation-check`, `runs/harbor-timeout-check`). Setup
  healthcheck cancellation and partial model-stream cancellation now also pass:
  `runs/harbor-setup-cancellation-check`, `runs/harbor-stream-cancellation-check`.
  Reports retain failures and incomplete measurements; checks include stopped
  containers and find no remaining trial containers. Harness setup commands now
  run in tracked process groups; `runs/harbor-install-cancellation-check` cancels
  a stalled installation command, verifies completion within 45 seconds rather
  than the command timeout, preserves failure evidence, and finds no containers.
  Host asset download cancellation now passes against trickling and stalled
  loopback HTTP responses in `runs/harbor-download-cancellation-check`; the
  original cache entry survives and staging files are removed. Stalled reads
  remain bounded by the 30-second socket timeout. Truncated responses and
  cancellation at EOF cannot publish an asset. `runs/harbor-upload-cancellation-check`
  injects a stalled Compose copy client, cancels it, and verifies both client/
  child processes and trial containers are gone. This is copy-boundary fault
  injection, not a large-payload throughput test. Pending transport calls now
  cancel and finish cleanup before teardown. Live Docker repetition was waived September 17 (H03-20).
- [x] **H03-16 — Existing negative lifecycle evidence reconciled.** Existing fixtures cover
  agent crash/nonzero exit, missing/malformed rewards, verifier crash/timeout,
  Praxis startup failure, and incomplete/truncated usage. Preserve the original
  failure phase and avoid false zero rewards or false complete measurements.
  Progress: live missing/malformed reward and verifier crash/timeout cases behave
  as errors; the real pilot captured agent crash and timeout. Truncated metrics
  have unit coverage. Praxis startup now has a 60-second overall deadline,
  explicit startup evidence, and a distinct proxy_startup failure phase. Live
  unreachable-upstream test passes in `runs/harbor-praxis-startup-failure`: worker
  exit 1, no model usage, preserved collection evidence, incomplete measurement,
  and no trial-owned containers remain. The September 17 audit verified the saved
  missing/malformed reward and verifier crash/timeout results and their imported
  error evaluations. Truncated usage has collection-boundary tests and partial
  streams have live cancellation evidence. No unspecified new live-failure gate
  remains; consolidate this evidence with H03-21/22 and repeat only for a concrete
  regression or the required Docker acceptance.
- [x] **H03-17 — Account for every scheduled trial.** Record expected versus
  emitted results on cancellation and partial job failure. Report missing trials
  explicitly; keep unknown binary resolution distinct from continuous scores.
  Evidence: `runs/harbor-partial-job-check` cancels a two-attempt serial run:
  one emitted failure and one missing slot are both reported as incomplete.
  No trial containers remain. Regression cases keep failed steps as errors even
  when an aggregate reward exists, and preserve unknown continuous resolution.
- [x] **H03-18 — Preserve tested environment policy and mounts on Podman.** Audit task-defined
  volumes, resource requirements, network policies, and service-name conflicts.
  Replace only Harbor's convention log mounts on macOS; do not silently drop
  unrelated task mounts. Validate credentials remain confined to the proxy.
  Progress: the Podman adapter now replaces only convention log mounts. The
  separate-verifier fixture passes limited isolation checks. The live
  `runs/harbor-isolation-cancellation-check` proves a synthetic credential is in
  the proxy environment only and a task volume survives as read-only (writes
  fail). Reserved ACB service-name collisions now fail before Compose startup.
  Public/no-network reachability controls pass against a healthy local service in
  `runs/harbor-network-public-check` and `runs/harbor-network-denied-check`, with
  complete cancellation cleanup. `runs/harbor-network-phase-check-4` verifies
  public setup access to two healthy local services, agent allowlist access to
  only one, and verifier denial of both. Fixes include translating Harbor's
  --no-TTY policy command for podman-compose and forwarding the upstream Host
  authority through Praxis. The post-fix four-harness accounting check passes in
  `runs/harbor-host-header-accounting-check`. Separate-verifier checks now pass
  in `runs/harbor-verifier-isolation-check`: verifier-only variables are absent
  from the agent; agent/provider variables and agent files are absent from the
  fresh verifier; the agent proxy is unreachable there. The verifier earns
  reward 1, accounting is complete, and all trial containers are removed.
  September 17 audit: this satisfies the tested Podman profile. Broader
  wildcard/CIDR/TLS capability coverage belongs to phase 6; live Docker repetition
  was waived under H03-20. Do not reopen this item for unspecified additional coverage.
- [x] **H03-19 — Validate skills through the transport on Podman.** Bridge native
  task-provided skills through the adapter installer and deliver explicit skill
  paths to every harness. Snapshot and validate task skill files, reject collisions
  with configured names, and freeze file hashes in the runtime contract.
  Evidence: `runs/harbor-skill-delivery-check-2` runs all four harnesses with both
  a task-provided and configured local skill. Supporting-file contents reach the
  model fixture and every trial earns reward 1 with complete measurement collection.
  This verifies explicit-path delivery/use; automatic native discovery in each
  isolated launch profile is not claimed. Live Docker repetition was waived September 17 (H03-20);
  configured remote asset reproducibility remains H03-13.
  Binary-backed skill evidence: `runs/harbor-rgctl-cache-check` uses cached
  rgctl v0.4.12 without downloading a release. All four harnesses read the skill,
  install `/usr/local/bin/rgctl` with the same SHA-256 as the cached binary,
  execute `discover`, and query a known function successfully. Every trial earns
  reward 1 with complete measurement collection. The deterministic model fixture
  drives these operations; this does not establish real-model skill compliance.
  **MCP is deferred from this implementation** and is not a phase 2/3 exit gate.
  Existing Claude/Goose delivery fixes, task stdio composition, validation, and
  Pi's explicit unsupported-capability error remain. Pi's bridge, remote
  transports, and live MCP acceptance are follow-on work.
- [x] **H03-20 — Docker/Podman interface review accepted; live Docker testing waived.**
  User decision, September 17: do not install Docker for this migration. Harbor
  dispatch selects ACBDockerEnvironment or ACBPodmanEnvironment; shared harness
  operations use the environment transport. Explicit Podman commands remain in
  its provider and the later-retired legacy path. Installed-worker configuration
  checks pass for both providers. Docker live compatibility is unverified; handle
  differences as bugs rather than keeping a phase gate open.
- [x] **H03-21 — Current package, CLI and regression review.** Source distribution
  and wheel built offline; wheel installed into a clean environment outside the
  checkout. Packaged resources and CLI init/resolve/list/help passed. Installed
  Harbor worker check accepted plans for both providers. Final suite: 282 passed,
  4 skipped. Evidence: `runs/harbor-final-package-review`. Live installed-wheel
  prepare/run was not repeated; existing live runtime evidence is retained.
- [x] **H03-22 — Operational notes and acceptance review published.** See
  `docs/harbor-operations.md`, the phase audit and final package-review evidence.
  The supported profile and offline/Docker limitations are explicit. Claude
  emulation is accepted as a known issue; later-phase migration/parity work remains.

### Current execution order

H01-01 through H01-06 and their H03-10/11 parent tasks are complete.
The user has resumed Phase 4 under the revised scope below. H04-05 is complete;
H04-01 through H04-07 are complete, including the approved native Terminal matrix;
do not reopen completed phase 1/2/3 work
without a concrete regression or new scope decision.

Phases 2/3 are complete under the September 17 scope decisions. The Claude RH
emulation investigation is closed for this migration as an accepted limitation,
not as a fixed bug. See the [known issue and upstream links](harbor-operations.md#known-issue-claude-code-on-emulated-amd64-benchmarks).
Do not reopen Claude emulation, Docker testing, general cache hardening or broader
network coverage as phase gates without a new scope decision.

The real-model RH treatment pilot remains phase 4; its model timeout does not
replace the controlled runtime acceptance criteria for phases 2/3.

## Phase 4: shared extensions on Harbor and existing runners

September 17 revised scope: benchmark conversion is a follow-on issue. Phase 4
must make configuration and extensions usable on the retained SWE-bench/ScarfBench
runners as well as Harbor. Existing configuration commands, aliases, paths,
provenance and package checks are complete; do not repeat them as implementation.
Live Docker testing remains waived. MCP, full dependency locking, and the affected
Claude amd64-emulation combination retain their accepted deferrals.

Execution order: H04-05, H04-01/02, H04-06/07, then H04-03/04.

- [x] **H04-05 — Share treatment asset preparation across execution backends.**
  - [x] Use one pinned Caveman skill materialization/instruction path for Harbor
    and existing runners; accept `skills: [caveman]` without a local-path workaround.
    Implemented through shared `prepare_response_skills`; all three intensities
    produce identical prepared settings on both backends across four harnesses.
    Checks cover hashes, additive prompts, launch parity, immutable inputs,
    idempotence, invalid intensity and legacy run handoff. Focused suite: 166
    passed at that checkpoint. Live delivery remains H04-02.
  - [x] Prepare RTK automatically for the existing runner's selected architecture,
    reusing the verified cache/build path and preserving explicit artifact overrides.
  - [x] Verify the existing task's interpreter requirement before Claude hooks;
    preserve benchmark workdir/conda behavior and record prepared asset identities.
  Preparation evidence: shared `prepare_rtk` uses the same verified cache/build
  and checksum path on both backends. Legacy preparation freezes selected
  `image_arch`; per-instance setup checks actual architecture and discovers or
  verifies Claude's Python >=3.8 before hooks are installed or model requests start.
  `rtk-runtime.json` records success/failure and effective interpreter/settings.
  Explicit interpreter failures do not silently fall back; workdir/conda remain
  unchanged. Legacy prepare still reports `probed: false` and pending runtime
  checks. Focused suite: 173 passed; asset builds use test fixtures, interpreter
  probe commands execute locally in tests. No live container/model run this turn.
  Live activation evidence remains H04-01/02; this closes preparation implementation
  only and does not close those live acceptance tasks.
- [x] **H04-01 — RTK activation across all four harnesses and both execution paths.**
  Verify native hooks/wrapper activation, loading/interception evidence, and
  baseline/treatment parity using the shared prepared assets. Docker provider
  selection remains supported without a new live-Docker gate.
  Evidence: four current pinned harnesses pass paired native-container adapter
  checks (4 tests, 141.55 seconds), including rewritten/passthrough commands,
  compressed results reaching the API, and injected RTK failure handling.
  `runs/phase4-native-treatments/check.json` indexes original artifacts. The
  Harbor treatment fixture also passes all four with reward 1 and complete
  accounting in `runs/harbor-phase4-treatments`. Goose/OpenCode: 6 requests,
  720 fixture tokens; Pi/Claude: 5 requests, 600 tokens. Request IDs are unique
  and ledger counts agree. Proxies stop before grading; no test-owned containers
  remain. Native tests exercise shared prepare and adapters, not full benchmark
  runner/grading. Actual benchmark pilots remain H04-03/04.
- [x] **H04-02 — Pinned Caveman response skill on both execution paths.** Verify
  instruction content/hash, priority, actual delivery, and all four harness
  adaptations independently of the context runtime. Include retained benchmark
  execution, not only Harbor fixtures.
  Evidence: both arms of the native-container checks install the pinned skill
  with its expected hash and deliver additive instructions to the API across all
  four harnesses. Harbor records matching instruction hashes/priority and the
  actual fixture ledger contains those instructions. Skill-on parity is held
  across the paired RTK arms. This proves delivery, not autonomous model compliance
  or quality improvements; model behavior belongs to the measured pilots.
  No Caveman context compression claim is made by these response-skill checks.
- [x] **H04-06 — Caveman context lifecycle for existing runners.** Add an isolated
  service/recovery store and transport operations to the existing pod lifecycle;
  share the adapter and gateway with Harbor. Verify interpreter, startup, endpoint
  routing, evidence collection, accounting, and cleanup without benchmark export.
  Implemented the per-task sidecar, recovery interpreter probe, shared recovery
  guidance and adapter transport. Partial startup cleanup is covered by unit tests.
  Live evidence: `runs/legacy-caveman-6775a675f1/check.json`, generated with
  the now-retired `check_legacy_caveman.py` migration helper exercised the actual retained runner
  pipeline with Pi and local Qwen on a tiny native task. Two model requests,
  2,410 provider tokens including cache reads, with every token bucket matching
  Pi's transcript; exact-byte preflight recovery; both services stopped before
  prediction collection and the pod removed before grading. Fixed legacy Praxis
  readiness traffic leaking into measured usage. This is record-mode lifecycle
  acceptance, not benchmark parity or actual compression/retrieval acceptance.
  Regression: 371 passed, four opt-in live tests skipped; the one sandbox-denied
  process-inspection test passed separately outside the sandbox. The subsequent
  readiness regression test and focused suite passed (28 tests).
- [x] **H04-07 — Functional Caveman compression acceptance.** On both paths,
  demonstrate an eligible real harness payload being compressed, agent-driven
  exact-byte retrieval, failed/critical output preservation, tool-ID/cache metadata
  preservation, streaming/cancellation, and provider usage accounting. Distinguish
  installed/loaded/record-only from compressed/retrieved activity. Required service
  failures must fail the run. These basic functionality gates move out of Phase 6.
  - [x] Pi success/error status survives OpenAI tool serialization through a
    native tool-result adapter; failed, critical, nontext and retrieval results
    retain their original representation. Shared gateway also handles Anthropic
    text-block arrays without changing tool IDs or cache metadata.
  - [x] Retained runner + local Qwen: actual compression and agent-driven exact
    recovery, five requests with all token buckets matching Pi's transcript.
    Evidence: `runs/legacy-caveman-34b0c32b31/check.json`.
  - [x] Harbor + Pi deterministic fixture: compression, exact agent recovery,
    failure/critical-output preservation, reward 1, six requests/720 fixture
    tokens and services stopped before verification.
    Evidence: `runs/harbor-caveman-compression-pi-2/check.json`.
  - [x] Shared gateway socket tests prove opaque incremental SSE forwarding and
    cancellation while the upstream is silent. Fixed cancellation waiting for
    a subsequent event before closing upstream. Failed retrieval attempts no
    longer count as successful agent recovery in manifests.
  - [x] Validate actual compression/recovery for Goose, OpenCode and Claude
    Code, preserving each harness's explicit success/error semantics.
  - [x] Finish required-service failure checks and remaining per-runner
    cancellation/isolation acceptance for context compression.
  Completion evidence: all four current pinned harnesses pass actual compression,
  exact recovery, failed/critical output preservation and matching fixture usage
  on Harbor (runs/harbor-caveman-compression-all-3/check.json) and the full retained
  runner pipeline (runs/legacy-context-all-4/check.json). Goose/OpenCode make seven
  requests (840 fixture tokens); Pi/Claude make six (720). Both paths reject loss
  of the required context service and clean up after cancellation:
  runs/harbor-context-service-failure, runs/harbor-context-cancellation,
  runs/legacy-context-service-failure and runs/legacy-context-cancellation.
  Harbor verifies proxy-only credentials and unchanged read-only task mounts;
  runs/caveman-store-isolation rejects retrieval of another pod's unique record.
  Shared model-request filtering now excludes Goose discovery traffic on the
  retained path too. Conservative critical-output matching also handles adjacent
  log fragments without a separating newline.
  Earlier checkpoint: 378 passed, four opt-in live tests skipped. These are functional
  checks, not a paired token-savings claim. The first Harbor fixture attempt
  selected the literal HANDLE from system instructions; corrected handle
  selection and moved recovery assertions before writing a passing reward.
- [x] **H04-03 — RH SWE-bench measured pilot.** Completed the frozen matched
  baseline/response-skill/RTK/context matrix with Goose/Pi/OpenCode and local
  Qwen; Claude excluded under the previously accepted emulation limitation.
  `runs/phase4-rh-pilot-matched/pilot.json`: eleven 300-second timeouts and one
  completed Pi response-skill trial, all reward 0. Partial usage is marked
  incomplete; this is execution evidence, not a quality or savings claim.
  The pilot-only 20 GiB quota exception was explicitly approved. Original
  source identity and the testbed conda runtime profile were preserved.
- [x] **H04-04 — Registry pilots and portable configuration.** All four arms
  executed for Terminal (three harnesses) and Aider (all four). Aider controls
  passed 0/1; its sixteen model trials completed with reward 0. Terminal's
  verifier `uvx` segfaulted under QEMU, including two oracle attempts: quality
  results are invalid. Three Terminal RTK trials failed before inference due
  to an unnecessary Git dependency in our preflight. The Git-free fix passed
  the separate three-harness retry in `runs/phase4-terminal-rtk-recheck-2/check.json`
  with complete measurements and no adapter errors.
  Portable examples in `config.example/phase4/` all resolve (eight files).
  Both 10 GiB quota exceptions were approved for pilot copies only.
  Completion: user approved `runs/phase4-terminal-native-proposal`. The native
  ARM64 build preserves the upstream Dockerfile/tests and CPU/memory limits.
  Controls passed 0/1; all sixteen trials completed with complete measurements,
  no execution exceptions, and one solved task (Pi/RTK). Other rewards were 0.
  Final results: `runs/phase4-summary-final/pilots.md` (44 selected trials).
  Native image matching and cleanup: `runs/phase4-terminal-native-pilot/check.json`.
  No Phase 4 work remains; quality/effectiveness claims remain outside this gate.
  Original results: `runs/phase4-summary/pilots.md` (40 trials).
  Latest regression: 388 passed, four skipped, after the preflight fix. The rebuilt
  wheel matches the current integration code/assets.

## Phase 5: benchmark conversion and remaining retirement

The benchmark migration is now implemented under cleanup C14. Both benchmark
families use Harbor by default and retain native grading. Fixed-fixture tests
and live Podman controls are recorded in the
[migration checkpoint](harbor-benchmark-migration.md). Optional pricing remains
follow-on work and is not a prerequisite for runner retirement.

- [x] **H05-01 — ScarfBench task export and verifier.** Preserve the complete
  generated project; keep grading files hidden during agent work; run native
  `scarf validate`/`make test` in an isolated verifier with an explicit container
  engine arrangement. Retain compile/deploy/smoke-test evidence.
- [x] **H05-02 — ScarfBench grading parity.** Compare known passing and failing
  migrations through the old and Harbor paths before switching execution.
- [x] **H05-03 — SWE-bench task export and grading parity.** Preserve official
  test scripts, grading semantics, patch isolation, and per-test evidence.
- [x] **H05-04 — Configuration completion and old runner retirement.** Completed
  September 21 under cleanup C09/C14d after both native grading parity gates.
  Shared helpers, CLI routing, replacement coverage, installed resources and
  examples are updated. See [current evidence and limitations](harbor-cleanup.md).

## Phase 6: concrete advanced capabilities and experiments

**Closed by user decision on September 18, 2026.** The selected implementation
scope is complete. Additional report charts remain a follow-up; H06-05 remains
user-owned. The real-Qwen RH timeouts are retained as limitations of those runs
and do not reopen this phase.

User scope update: implement dataset-defined metrics, combined RTK/context and
expanded multi-step support. Effectiveness experiments are user-owned and are not
an implementation acceptance gate. The agreed comparison model and HTML report
refactor below are part of the metrics work, not an optional presentation follow-up.
In this discussion, a suite "task" means an individual benchmark in that suite;
keep underlying dataset instances/trials separately identifiable.

This replaces the former open-ended broader-coverage gate. Research basis:
[Harbor metrics](https://docs.harborframework.com/core-concepts/datasets/metrics),
[multi-step semantics](https://docs.harborframework.com/core-concepts/tasks/multi-step),
and [Caveman recovery/estimate contract](https://github.com/JuliusBrussee/caveman/blob/main/engine/README.md).
Current upstream docs explain concepts; implementation targets our existing pins,
not an implicit upgrade. Local code and saved evidence determine what is complete.

- [x] **H06-01 — Preserve dataset-defined metrics.** Carry metric definitions
  through dataset preparation/job configuration and preserve native aggregate
  results. ACB completed-grade means are retained and labeled separately. Completion:
  a deliberately non-average metric matches Harbor output; missing/error trials
  retain explicit semantics and ACB summaries are labeled separately.
  Preserve individual benchmark grades, metric definitions/direction and grading
  provenance. Dataset-native aggregates remain available but are not substituted
  for per-benchmark comparisons or used as an automatic universal suite score.
  Feed the same comparison model to JSON, CLI and HTML (H06-06).
- [x] **H06-02 — Caveman runtime prerequisites and recovery probes on Podman.**
  Existing evidence remains accepted: `harbor-caveman-probe-3` exact-byte probes
  and `harbor-caveman-record` four-harness reward-1 runs. Saved manifests report
  zero compression and no agent-driven retrieval; those gaps now belong to H04-07.
- [x] **H06-03 — RTK plus Caveman context composition.** Verify transformation
  order, eligibility after RTK, recovery meaning, diagnostic preservation, and
  configuration conflicts. Completion: controlled combined-treatment runs show
  both components' actual activity and usable recovery. Not enabled by default.
- [x] **H06-04 — Multi-step lifecycle and accounting.** Extend the successful
  two-step Goose baseline to step transitions, early stopping, later-step errors,
  per-step budgets, and extension/recovery service lifetime. Completion: controlled
  fixtures preserve per-step artifacts and usage without duplication across the
  supported harnesses. Session resumption is not implicitly promised.
- **H06-05 — User-owned effectiveness experiments; excluded from implementation completion.** Compare matched baseline,
  record-mode, compression and combined treatments with paired repeats. Include
  task outcomes, retrieval overhead, extra requests and provider usage. Local
  Qwen and Haiku results are independent. Completion is reproducible reporting
  of benefit or regression, not a required token-savings threshold.


- [x] **H06-06 — Refactor comparison data and HTML reports.** Implement the
  agreed per-benchmark comparison model alongside H06-01. Update
  `acb/report.py`, `acb/harbor/results.py`, `acb/html_report.py` and CLI consumers
  as needed; comparison rules must be shared rather than reimplemented in HTML.
  - [x] Align the same benchmark across baseline/candidate suites using stable
    identity, revision, inputs/instance selection and grading definitions.
    Display unmatched benchmarks and provenance differences explicitly; do not
    silently align by display name or fail the entire page on different sets.
  - [x] Show an individual grade for each benchmark on single-run and comparison
    pages, with baseline/candidate values, metric direction, quality change and
    drill-down to instances/trials. Unknown grades and broken verifiers remain
    distinct from valid failures. Missing comparison metadata is not proof of
    compatibility; show an unverified/not-comparable explanation.
  - [x] Summarize candidate quality relative to baseline: same if every matched
    grade is unchanged; better if at least one improves and none worsen; worse
    if at least one worsens and none improve; mixed if changes go both ways.
    Respect the metric's declared direction/equality rules. Multiple metrics
    need an explicit primary grade or per-metric presentation, not invented
    weights. Unknown/error grades cannot silently become same or worse.
  - [x] Report comparison coverage separately from quality direction. Partial
    coverage must qualify the summary (for example, better on 8/10 benchmarks;
    comparison incomplete); zero comparable grades has no quality verdict.
    Mixed results visibly identify each benchmark's increase/decrease.
  - [x] Show baseline/candidate tokens, absolute and percentage changes for each
    matched benchmark, retaining fresh/cache buckets and measurement status.
    Flag differences in task sets, budgets, attempts, grading and resource
    conditions. Aggregate tokens only over an explicitly identified comparable
    set with complete measurements; never present unmatched full-suite totals
    as equivalent workloads. Handle zero baseline values without invented
    percentages and distinguish unavailable measurements from zero usage.
  - [x] Replace misleading suite score/resolve-rate headline comparisons with
    the directional quality summary, coverage and comparability notices. Keep
    native dataset aggregates labeled as secondary detail. Preserve useful
    token breakdowns and drill-down navigation. Apply the same semantics to
    text/JSON reports and multi-run entry points.
  - [x] Generate standalone, individually addressable HTML reports for each
    benchmark, linked from suite and comparison pages. Preserve detailed
    investigation views: grade/verifier results, configuration/provenance,
    instance/trial breakdowns, turns, model requests, tool calls and results,
    tool names/arguments/status/errors, available timing, per-turn tokens and
    cache usage, context growth, content/tool/shell-command breakdowns, patches
    and links to recorded artifacts. Show recorded detail where available;
    missing telemetry must be explicit rather than inferred or counted as zero.
    The suite refactor must not discard existing detailed report capabilities.
  - [x] Expose turns and tool-call counts per matched benchmark in comparison
    views alongside grades and token changes, with baseline/candidate values
    and deltas. Provide expandable or linked side-by-side detail for tool usage,
    per-turn timelines and individual calls, and direct links to each run's
    standalone benchmark HTML. Define turns, model requests and tool calls
    separately so parallel calls are not conflated with turns. Use consistent
    counting and coverage rules across both runs; flag unavailable/incompatible
    telemetry. These diagnostic measures do not alter the quality verdict or
    imply one-to-one correspondence between calls from different trajectories.
  - [x] Verify same/better/worse/mixed, partial/unmatched sets, revision/budget
    mismatches, missing/error grades, incomplete usage and zero baselines with
    controlled fixtures. Render representative single-run, matched and partial
    HTML reports and review readability, escaping and drill-down behavior.
    Verify standalone benchmark pages retain tool/turn detail and comparison
    links reach the correct baseline/candidate pages and artifacts.

Execution order: H06-01 and H06-06 together, then H06-03 and H06-04. H06-05
remains user-owned; controlled functional checks for the implementation still
belong to the implementation tasks.

Already covered: Compose/service image/resource checks, separate-verifier isolation,
numeric/dictionary rewards, and basic artifact transfer. Do not reopen these as
unspecified broader-ecosystem gates. Add capability work only for a concrete selected
benchmark requirement or regression. No new Docker/MCP/locking/emulation gate.

Implementation evidence and supported limits: [Phase 6 checkpoint](harbor-phase6-checkpoint.md).

## Completion rule

Phases 1/2/3/4 and the selected Phase 6 implementation are complete. Phase 5 remains deferred, so the full migration and old-runner retirement are unfinished. Keep the current SWE-bench and ScarfBench
execution paths until independent grading-parity checks pass. Do not describe
experimental code, a successful build, or a preparation probe as a completed
end-to-end capability.
