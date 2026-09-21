# Harbor migration checkpoint

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

> Historical September 16 snapshot. Its open-issue and resume lists are superseded
> by the [September 17 phase audit](harbor-phase-2-3-audit.md) and
> [current checklist](harbor-migration-tasks.md). In particular, its accounting,
> skills/MCP, image-contract and test-count statements are not current status.

Latest follow-up: [accounting and RH baseline checkpoint](harbor-accounting-rh-checkpoint.md)
and [configuration UX / structure review](harbor-ux-structure-review.md).
The text below preserves the earlier pause checkpoint; subsequent fixes and
verification results are recorded in the follow-up.

Updated: September 16, 2026. Implementation paused at the user's request to
consolidate status. Scope remains the entire six-phase migration.

Use the [task checklist](harbor-migration-tasks.md) for acceptance gates and the
[architecture plan](../HARBOR_PLAN.md) for intended behavior. This checkpoint
records implementation and evidence; it does not change the acceptance criteria.
Tasks are repository checkboxes, not native Codex tasks. Changes are uncommitted.

## Where we are

The active phase is **phase 3, runtime contract and four-harness bridge**, with
unfinished phase 1/2 requirements. Some phase 4/6 features have been implemented
and exercised. The six-phase migration is not complete.

| Phase | Current evidence | Remaining gate |
| --- | --- | --- |
| 1: configuration | Pure resolver, schema-v2 selections, catalog, CLI resolve/prepare, declaring-file paths | Complete path/validation cases and use the same normalized plan in legacy execution |
| 2: Harbor foundation | Harbor 0.23.0 isolated in Python 3.12; Podman adapter; pinned RH task; no-op/oracle controls | Docker acceptance, image identity, resource/network policy, reproducible offline preparation |
| 3: harness bridge | All four harnesses pass deterministic native ARM64 fixture; transcripts, proxy collection, cancellation evidence | Accounting rerun, remaining failure cases, skills/MCP validation, cross-provider checks |
| 4: extensions and real pilots | RTK preparation code; pinned Caveman skill; first real Qwen baseline attempted | Four-harness RH gate, live RTK/skill treatments, registry pilots |
| 5: benchmark migration | Existing SWE-bench and ScarfBench execution retained | Harbor exporters, independent grading parity, configuration completion, old runner retirement |
| 6: broader capabilities | Numeric rewards, separate verifier, two-step Goose baseline, Caveman recovery and record-mode probes | Dataset metrics, broader harness/provider coverage, extension restart lifecycle, measured compression/composition |

ScarfBench still uses its existing path. A Harbor task exporter must preserve the
complete generated project, hide grading material during agent execution, run
native validation in an isolated verifier, and demonstrate passing/failing parity
before switching execution. Current Harbor successes do not establish that parity.

## First real RH SWE-bench pilot

Local evidence: `runs/harbor-rh-qwen-baseline/report.json`, per-harness reports,
and native trial artifacts under that directory.

- Dataset: `rounakbende/rh-swe-bench`, revision
  `a31c36f6cb6d2c9f3caea36745564b3f4958239c`, task `task-0000`.
- Model: `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2` served at
  `http://localhost:8000`; containers use `http://host.containers.internal:8000`.
- One attempt per harness, serial execution, 600-second agent limit, no extensions.
- Harness pins: Goose 1.50.1, Pi 0.84.3, OpenCode 1.18.22,
  Claude Code 2.1.241 with the isolated-hooks profile.
- This pilot used the existing `acb-praxis-ai:latest` override, so it does not
  validate the new automatic pinned-source Praxis image build.

| Harness | Outcome | Native reward | Reported total tokens |
| --- | --- | --- | ---: |
| Goose | Completed | 0 | 6,191 |
| OpenCode | Completed | 0 | 24,213 |
| Pi | Agent timeout at 600 seconds | 0; trial remains an error | 41,538 |
| Claude Code | Exit 139; Bun segmentation fault under amd64 QEMU | Unavailable | 0 |

These are smoke-test outcomes, not a benchmark comparison. Pi and Claude are
marked measurement-incomplete even though collection finished. In-flight usage
can be missing for aborted trials. Historical turn counts include discovery
requests; the latest filtering fix has unit coverage but has not been live-rerun
or applied to historical pilot usage. Error-phase labeling also needs review:
the Pi report labels the verifier phase despite an agent-timeout exception.

The short direct Qwen tool-call probe succeeded. That verifies basic server tool
support, not successful end-to-end behavior for every harness.

### Architecture finding

The tested RH task explicitly selects `linux/amd64`. Its upstream image manifest
exposed an amd64 image and an attestation, without an ARM64 task image. The local
host runs that image through emulation. Harbor itself is not restricted to amd64:
all four harnesses passed the native ARM64 fixture.

The RH four-harness gate needs a native amd64 environment or an explicitly
validated ARM task variant. Do not silently change upstream task dependencies
or count Claude's emulation crash as a model failure.

## Live evidence ledger

Paths below are local run artifacts and may be ignored by Git.

| Evidence directory under `runs/` | Verified scope |
| --- | --- |
| `harbor-nop-smoke-3`, `harbor-oracle-smoke` | Local binary control rewards 0 and 1 |
| `harbor-rh-nop`, `harbor-rh-oracle` | Pinned RH task controls return 0 and 1 on Podman |
| `harbor-bridge-probe` | All four harness installation probes |
| `harbor-bridge-baseline-3` | All four deterministic native ARM64 trials reward 1; transcripts and Praxis collection, including Claude translation |
| `harbor-runtime-inspection` | Actual ARM64 architecture, `/work`, root user, task Python at `/opt/miniconda3/bin/python` |
| `harbor-cancellation-check`, `harbor-timeout-check` | Goose long-running tool cancellation/timeout, preserved failure evidence, no matching running containers |
| `harbor-lifecycle-fixtures` | Missing/malformed rewards and verifier crash/timeout become errors; numeric dictionary rewards preserved |
| `harbor-separate-fixture` | Corrected separate-verifier fixture passes; main answer file and `ANTHROPIC_API_KEY` absent in verifier |
| `harbor-multistep-fixture` | Two-step Goose baseline reward 1 with archived step transcripts and aggregated usage |
| `harbor-caveman-probe-3` | All four environments pass exact-byte recovery preparation probes |
| `harbor-caveman-record` | All four reward 1 through the no-transform Caveman gateway |

The first combined lifecycle script failed because its separate-verifier fixture
did not copy `/tests/test.sh`; the corrected fixture passed separately. Separate
verification does not yet prove artifact transfer or complete credential isolation.
Caveman preparation/record-mode success does not prove measured compression or
agent-initiated recovery. Multi-step RTK/Caveman lifecycle remains unvalidated.
The bridge fixture currently depends on the pre-existing local image
`localhost/acb-rtk-smoke:0.48.0`; fresh-checkout portability remains open.

## Code and verification checkpoint

- `acb/resolver.py`, `acb/config.py`, `acb/catalog/`, and `acb/cli.py` implement
  configuration resolution and preparation entry points. Python executable paths
  preserve virtual-environment symlinks.
- `acb/harbor/` implements dataset snapshots, worker protocol, Podman environment,
  transport, harness bridge, Praxis lifecycle, and native result import.
- `acb/preparation.py` freezes per-task runtime prerequisites and extension
  assets. `acb/downloads.py` adds bounded, cancellable downloads and atomic writes.
- Result import preserves missing scheduled trials, numeric rewards, primary
  errors, aborted measurement state, and archived multi-step evidence.
- Latest measurement parsing retains raw and sanitized records, rejects malformed
  token fields, and filters discovery requests from model-call accounting.
  **This latest change still needs a live accounting check.**
- Last fully recorded broad regression result: **147 passed, 4 skipped** before
  subsequent changes. Latest recorded targeted runtime/results run: **16 passed**.
  These are historical checkpoints, not final current-tree certification.
- `uv.lock` was updated. A wheel built and imported outside the checkout with
  packaged catalog, Praxis, and Caveman resources. Later source changes mean a
  final release build/install check is still required.

## Open implementation issues to retain

1. Legacy execution does not yet consume every normalized shared selection;
   some declaring-file path cases remain incomplete.
2. Complete image digests, artifact verification, cache extraction atomicity,
   resource/network checks, and offline enforcement for provider/MCP startup.
   Registry git sources and dataset metrics also need completion.
3. Validate every native skills/MCP route. Claude's isolated-hooks launch currently
   supplies an empty MCP configuration; Pi's configured MCP support is unverified.
   Task-provided Harbor skills/MCP are not fully bridged.
4. Review multi-step evaluation status when a step fails but aggregate rewards
   exist. Ensure step exceptions cannot become a completed trial.
5. Make Caveman services restartable across steps; validate actual compression,
   recovery through agents, streaming/cancellation, and RTK composition.
6. Finish Docker coverage. The Docker host-gateway overlay has no live evidence.
   Preserve unrelated mounts and verify credential/resource policies on providers.
7. Promote temporary negative/separate-verifier drivers into reproducible checked-in
   checks, and remove reliance on locally prebuilt fixture images.

## Resume order and local context

1. Run the current regression suite and repeat a deterministic live accounting
   fixture after the latest metrics filtering changes.
2. Resolve RH architecture/runtime preflight and run the four-harness baseline
   on a supported environment. Investigate Pi's timeout from its transcript.
3. Finish H03 configuration, preparation, lifecycle, MCP, and Docker gates;
   verify the packaged CLI outside the checkout.
4. Run RTK and response-skill treatments, then broader registry pilots.
5. Implement ScarfBench/SWE-bench exporters and grading parity before retiring
   their current execution paths. Finish broader Harbor/Caveman gates.

Current development uses ACB's own Python 3.12+ environment with pinned Harbor
0.23.0. From the repository root, run:

```sh
python -m pytest tests -q
```

Use `tests` explicitly; collecting the repository root also picks up vendored and
run-directory tests. Temporary files may disappear across restarts:

- `/private/tmp/acb-qwen-harbor.yaml`: pilot configuration; key choices recorded above.
- `/private/tmp/acb-qwen-pilot.log`: pilot controller log.
- `/private/tmp/acb-check-negative-fixtures.py` and
  `/private/tmp/acb-check-separate-fixture.py`: additional fixture drivers.
- `/private/tmp/acb-multistep-plan.json`: two-step probe plan.

The reusable cancellation driver is checked in at
`scripts/check_harbor_cancellation.py` (cancel and timeout modes). Preserve
concurrent ScarfBench edits when resuming. `HARBOR_PLAN.md` matches the current
`*_PLAN.md` ignore rule; include it deliberately if later committing the plan.
