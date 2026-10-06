# Harbor task runtime contract

Preparation inspects each selected task inside its actual container. It records
architecture, working directory, user, the helper Python interpreter, and the
language environment. Asset preparation uses that architecture; execution
rechecks the contract before installing or running the harness.

Current task status and scope: [Harbor cleanup](harbor-cleanup.md).
Dated evidence sections below record the checks performed at those checkpoints.

## Language environment

Generic tasks retain their image's PATH. A task requiring the supported conda
activation can declare it in `task.toml`:

```toml
[metadata.acb_runtime]
conda_env = "testbed"
```

This currently uses `/opt/miniconda3/etc/profile.d/conda.sh`, matching the four
existing adapters. Preparation must successfully activate the environment and
records its Python executable and prefix. All four harness configurations receive
the same environment. Conflicting harness overrides fail preparation.

The first RH SWE-bench dataset revision,
`a31c36f6cb6d2c9f3caea36745564b3f4958239c`, has a revision-scoped `testbed` profile.
An explicit task metadata profile takes precedence. An empty `acb_runtime` table
selects the image PATH. Other dataset revisions are not assumed to use this
profile.

Re-prepare older saved plans to include the language-environment contract.
The main-container contract now includes the immutable image ID, architecture,
engine-enforced CPU/memory limits, mount destinations/permissions, privilege flag,
and setup network policy/capabilities. Explicit CPU/memory requirements must match
engine inspection. Storage/GPU/TPU limits are unsupported by the current providers.
Container environment values are never written into the contract; provider
credential variable names are checked for absence in the agent container.

Prepared runs use the inspected main image with pulling and rebuilding disabled.
Images remain cached after teardown; containers and trial volumes are still
removed. If the image cache is pruned, prepare again. Docker/Podman inspection
uses a small version-specific adapter for the pinned Harbor release. Separate
verifier images are not replaced with the agent image.

Task Compose services and the egress controller now have their image IDs frozen
alongside the main image. The final Compose overlay pins these IDs after Harbor's
own policy overlays. Praxis/Caveman references are resolved during preparation;
measured execution verifies the running provider image IDs. Offline preparation
fails if an image is missing. Separate-verifier contracts and package verification
are implemented (see below). Docker Quick Start evidence is recorded in
[Quick Start validation](quick-start-validation.md); the broader contract matrix
has only the Podman evidence below.

## Podman Compose

When available, ACB invokes `podman-compose --in-pod=false` directly. This keeps
installing Docker Compose from changing the frontend used for Podman trials.
Otherwise, ACB requires a Compose V2 compatible frontend through `podman compose`.

## Verification

- `runs/harbor-rh-language-check-podman`: live RH inspection confirms
  `/opt/miniconda3/envs/testbed/bin/python` in the amd64 task image.
- `runs/harbor-runtime-accounting-check`: all four native ARM fixture runs earn
  reward 1 and reconcile model requests and tokens exactly.

These checks do not resolve Claude's amd64 startup failure; repair is deferred
under the accepted scope. MCP is deferred from this implementation;
skills remain in scope.

## Additional Podman evidence

- `runs/harbor-retained-image-check` and `runs/harbor-pinned-contract-recheck`:
  stable prepared image reuse, 1 CPU / 512 MiB, and successful harness startup.
- `runs/harbor-image-drift-check`: altered expected image identity rejected.
- `runs/harbor-setup-cancellation-check`: cancel during environment healthcheck.
- `runs/harbor-stream-cancellation-check`: cancel during a partial model stream.
- `runs/harbor-isolation-cancellation-check`: proxy-only synthetic credential,
  retained read-only task volume, write rejection, and cancellation cleanup.
- `runs/harbor-network-public-check` / `runs/harbor-network-denied-check`: a healthy
  local service is reachable under public policy and blocked under no-network.

The cancellation driver supports `--phase tool|setup|stream|install|upload`, plus
`--check-isolation` with stream or `--check-network public|no-network` with setup.
These are bounded fixture checks, not a general hostile-agent isolation audit.

## Service images and policy transitions

- `runs/harbor-service-image-check-2`: all four harnesses reuse the same prepared
  main, task model-service, and Praxis images and earn reward 1.
- `runs/harbor-install-cancellation-check`: a stalled installation command is
  terminated promptly, with incomplete measurement status and no trial containers.
- `runs/harbor-network-phase-check-4`: setup reaches both local canaries, the agent
  allowlist permits only the selected hostname, and the verifier reaches neither.
- `runs/harbor-host-header-accounting-check`: exact accounting reconciliation
  across all four harnesses after the proxy Host-header correction.

Praxis forwards the upstream authority instead of the agent's loopback Host.
This matters for virtual hosting and hostname-based plain-HTTP policy checks.
Podman policy commands translate Docker Compose's `--no-TTY` flag to `-T`.

The phase fixture verifies HTTP access to local Compose services. It does not
establish coverage for every wildcard/CIDR/TLS allowlist combination. The host download and copy-boundary cancellation checks and separate-verifier
credential checks are recorded below. Caveman pinning is implemented but not exercised by this
baseline fixture.

## Transfers and separate verifier isolation

Pending synchronous-adapter operations now register their async transport calls.
Cancellation closes the transport, stops agent/setup process groups, cancels
pending operations, and waits for their cleanup before environment teardown.
Compose clients run in their own local process groups; cancellation terminates
both the client and engine subprocesses. Cleanup failures are attached to the
original error instead of replacing it.

Asset downloads check cancellation between individual HTTP reads and before
publication. They reject truncated Content-Length responses. Existing cache
entries survive unsuccessful transfers, and staging files are removed. A fully
stalled read can take up to the configured 30-second socket timeout to stop.

Evidence:

- `runs/harbor-download-cancellation-check`: real loopback HTTP trickle/stall
  cancellation and truncated-response rejection.
- `runs/harbor-upload-cancellation-check`: an injected stalled Compose copy
  client and child are terminated; no trial containers remain. This checks the
  upload boundary rather than large-payload transfer performance.
- `runs/harbor-verifier-isolation-check`: synthetic agent/provider/verifier
  variables stay in their intended contexts; agent files and the agent loopback
  proxy are unavailable in the separate verifier. Reward 1, complete accounting,
  and no remaining trial containers.
- `runs/harbor-transfer-accounting-check`: all four harnesses retain reward 1
  and exact request/token reconciliation through the new Compose process layer.

These dated checks apply to Podman. Later Docker Quick Start evidence covers
the repair task and native Cart controls, not this full isolation/transfer matrix.

## Managed worker cache validation

Harbor is a pinned core ACB dependency and requires Python 3.12 or newer. The
worker remains isolated as a subprocess but uses `sys.executable`, so preparation
does not create or mutate a second virtual environment. `benchmark.python`, the
managed-runtime readiness marker and installed-package inventory were removed.
The worker checks its pinned Harbor version at startup.

Full transitive dependency locking remains deferred by user decision.

## Task and configured skill delivery

The bridge downloads Harbor's task `skills_dir` through the environment API,
validates each skill's frontmatter and directory name, and records SHA-256 hashes
for SKILL.md and supporting files in the prepared runtime contract. Symlinks and
unsupported file types are rejected. Changed task skill contents fail the existing
runtime comparison before harness installation or model traffic. The downloaded
snapshot is retained in the trial's `agent/acb/task-skills` directory.

Task skills and configured skills use the same adapter installer. Duplicate names
fail setup. Additive instructions list the installed SKILL.md paths, preserving
the benchmark instruction and existing additive prompt. This supports isolated
launch profiles without depending on automatic native discovery.

`skill-delivery.json` records the task inventory, configured selections and delivery
mechanism. Installation evidence alone does not prove model reading.
`runs/harbor-skill-delivery-check-2/skill-check.json` adds live evidence: all four
harnesses read both skills and their supporting files, the returned file contents
appear in the deterministic model's request ledger, and each trial earns reward 1
with complete measurement collection. This is Podman evidence using explicit
paths; it does not establish automatic discovery, real-model compliance, Docker
support, or remote configured-skill cache reproducibility.

Binary-backed skill acceptance also passes in `runs/harbor-rgctl-cache-check`.
The test snapshots `runs/.cache/skills/rgctl-v0.4.12-aarch64`, configures the
existing installer with `binary_name: rgctl` and
`binary_install_path: /usr/local/bin/rgctl`, and exercises all four harnesses.
Each installed binary reports v0.4.12 and matches cached SHA-256
`daf4968b20cbafd8ffc85a66ee6dd6be7f61da22d6b707d5e6e85334d12d5ddc`.
The model fixture requests skill reading, repository discovery and a JSON query
for a known function. All four return the expected function and earn reward 1
with complete measurement collection. The check uses cached bytes and does not
test GitHub release downloading or real-model compliance. Reproduce it with
`python -m scripts.check_harbor_rgctl PLAN SKILL_CACHE NEW_OUTPUT_DIR` using the
ACB environment and the deterministic bridge fixture plan.

## Separate-verifier image contracts

Preparation starts each separate verifier environment using Harbor 0.23's own
configuration, build-context and network-policy resolution, including step-level
verifier definitions. It records main/service image IDs, enforced resources,
mount permissions and the network baseline in `verifier-contracts.json`. Contract
keys include the build context and effective environment configuration.

Prepared runs select these immutable images with pulling/rebuilding disabled.
Before grading, the provider records `agent/acb/verifier-runtime-*.json` and
rejects a missing or changed contract. The inspector uses private Harbor APIs;
this compatibility boundary must be reviewed when upgrading Harbor.

`runs/harbor-verifier-contract-check` verifies the prepared and observed contracts
match, the existing isolation checks pass, reward is 1, and no trial containers
remain. Unit checks cover unprepared verifiers, runtime drift, resource-key
differences, truncated session names and a verifier step named `env`. Step-level
resolution is implemented but the live fixture uses a single separate verifier.

`runs/harbor-caveman-image-check` additionally verifies prepared Praxis/Caveman
provider identities across all four harnesses in Caveman record mode. Each trial
earns reward 1 with complete measurement collection. These are Podman image-contract
checks; Docker coverage and compression effectiveness remain separate gates.

## RTK artifact cache validation

RTK reuse checks the recorded source commit, requested architecture and build
recipe as well as the binary checksum and executable permission. Invalid complete
entries fail explicitly. Missing or incomplete entries fail offline without engine
calls; online preparation may build their replacement.

The binary and manifest are staged in one directory and published together under
the existing process lock. Incomplete prior entries remain until the replacement
is ready and are restored if publication raises an error. Temporary build files
are removed on normal completion or handled failure.

The focused preparation/download suite passes 28 tests, covering corruption,
offline misses, copy/publication failures and two concurrent processes sharing
one build. Engine commands are mocked for these cache tests. This validates cache
lifecycle behavior, not a fresh RTK build or its later-phase harness integration.

## Managed provider build provenance

Managed Praxis/Caveman builds snapshot their build context before calling the
engine. The cache key includes the recipe name, file paths, content hashes and
file permissions. Symlinks and special files are rejected. The built image carries
context/recipe labels, and an atomically published host manifest records these
inputs together with the image ID, architecture and OS. Preparation returns the
immutable ID to avoid later tag changes affecting provider selection.

Reuse runs under a process lock and checks image labels and any existing manifest.
Unexpected tag or manifest changes fail explicitly. Missing images fail offline;
online preparation can rebuild them. Failed builds cannot publish a readiness
manifest. Build inspection has a 30-second timeout and builds have a 30-minute
timeout. Base-image tags and downloaded dependencies retain current resolution
behavior because full dependency locking is deferred.

`runs/harbor-provider-cache-check/cache-check.json` records a live Podman check:
two concurrent preparers return the same image ID, offline reuse succeeds,
changed inputs fail offline, and no staging directories remain. It uses a small
image based on an existing local fixture; it does not rebuild Praxis or Caveman.
Unit tests cover source changes after snapshotting, tag/label/manifest drift,
build failure and unsupported context entries. The full suite passes 263 tests
with 4 skips.

The cache format changes automatically generated provider tags, so older managed
images need one online preparation. Explicitly configured provider references
continue through the existing image-ID pinning path.

## Remote skill cache validation

Git skills use repository/ref-specific cache entries. Release skills include the
resolved version, repository, archive pattern, binary selection, instruction URL
and requested architecture in their cache identity. `latest` resolution and cached
branch behavior remain as before; full dependency locking is deferred.

Downloads/clones happen in staging. Publication requires a valid SKILL.md and,
for release configurations requesting a binary, an executable payload. A complete
directory and its manifest publish together under a process lock. Reuse verifies
every recorded file's checksum and permission bits, so deleted, added or altered
files fail explicitly. Offline misses fail before network access.

Six focused cache tests pass, covering release transfer failure, missing binaries,
changed sources/refs and corruption. A real local Git repository verifies that two
concurrent callers share one clone and that offline reuse does not clone again.
Release downloads are mocked in these tests. The broad regression suite passes
269 tests with 4 skips.

Older remote cache entries require online population of the verified cache format.
Explicit local skill paths, including the previously tested rgctl cache, retain
their existing behavior.

## Harness archive publication

Goose, Pi, OpenCode and Claude Code download and extract archives into staging
under their existing cache lock. The expected executable must be present,
nonempty and executable before the whole directory is published. The entry
includes a manifest of file checksums and permissions; subsequent reuse detects
changed or missing support files as well as changed binaries. Pi's inventory
covers its full distribution. Internal symlinks are recorded; escaping links and
reserved cache metadata are rejected.

Failed extraction leaves no ready cache entry. Failed publication restores an
incomplete prior entry. Existing caches without a manifest remain compatible and
continue through Harbor's installed startup/version checks; they do not have
retrospective file-integrity guarantees.

Thirteen tests exercise all four adapters using generated archives, including
concurrency, interrupted extraction, corruption, offline misses and publication
failure. `runs/harbor-harness-cache-check/cache-check.json` records offline reuse
of the actual existing caches. The regression suite passes 282 tests with 4 skips.
These checks do not establish that the complete offline preparation path prevents
Harbor task-image setup from pulling or building missing dependencies; that remains
the next preparation acceptance check.
