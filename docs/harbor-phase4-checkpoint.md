# Phase 4 validation checkpoint

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

**Phase 4 complete — September 18, 2026, within the agreed scope and documented pilot deviations.**

## Completed implementation

Shared preparation, RTK activation, Caveman response-skill delivery and context
compression work with Harbor and the retained runners. All four pinned harnesses
have functional compression, exact agent-driven recovery, failed/critical-output
preservation, accounting and cleanup evidence. Required-service loss and
cancellation are covered; recovery stores are isolated between trials.

| Acceptance | Evidence under `runs/` |
| --- | --- |
| Harbor, all four harnesses | `harbor-caveman-compression-all-3/check.json` |
| Retained runner, all four harnesses | `legacy-context-all-4/check.json` |
| Local Qwen compression/recovery | `legacy-caveman-34b0c32b31/check.json` |
| Service failure | `harbor-context-service-failure`, `legacy-context-service-failure` |
| Cancellation and isolation | `harbor-context-cancellation`, `legacy-context-cancellation`, `caveman-store-isolation` |
| Portable configuration | Eight resolved files in `config.example/phase4/` |

## Measured pilots

The model is `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2`, served
locally. Each matrix uses one frozen task, one attempt per arm, one worker and a
300-second agent limit. Arms are baseline, response skill, RTK and context
compression. Provider cache is not reset; fresh/cache token buckets are retained.

- **RH SWE-bench:** twelve trials across Goose/Pi/OpenCode. Eleven timed out;
  Pi's response-skill trial completed with reward 0. Claude was excluded under
  the accepted amd64 emulation limitation. Source identity preserves RH's
  `testbed` conda profile even when loading the approved local task copy.
- **Aider Polyglot:** sixteen trials across all four harnesses completed their
  measurements with reward 0. No-op/oracle controls passed with rewards 0/1.
- **Terminal-Bench, published amd64 image:** nine trials completed measurement;
  three RTK trials failed before inference because preflight assumed Git.
  Its `uvx` verifier segfaulted under QEMU, including two oracle attempts.
  These rewards cannot establish model quality. The Git-free preflight retry
  completed all three harnesses with complete measurements and no adapter errors.
  Its rewards remain invalid because the published-image verifier still crashes.
- **Terminal-Bench, approved native ARM64 image:** no-op/oracle controls passed
  with rewards 0/1. All sixteen trials completed with complete measurements and
  no execution exceptions. Pi with RTK solved the task (reward 1); the other
  fifteen trials scored 0. All arms used the same inspected native image.
  Evidence: `runs/phase4-terminal-native-pilot/check.json`.

Final RH/Aider/native-Terminal results: `runs/phase4-summary-final/pilots.md`
and `pilots.json` (44 trials). Validation qualifications and image provenance
are in `runs/phase4-summary-final/validation.json`. The original 40-trial summary
and separate three-trial Terminal RTK retry remain under `runs/phase4-summary`;
the native experiment does not overwrite those results.
Partial usage on timed-out trials is not a complete cost. These small pilots do
not establish token savings, statistical quality differences or benchmark parity.

## Experiment deviations

Only separately approved pilot copies omit the unenforceable disk quotas:
20 GiB for RH and 10 GiB each for Terminal/Aider. Task code, tests, CPU and memory
requirements remain unchanged. Production resource checks still reject unsupported
quotas. Source pins and approvals are in the `*-storage-proposal` run folders.
For RH task-0000, the quota is an environment budget; its instruction and grading
concern YAML parsing, not disk exhaustion. This conclusion is task-specific.

The user also approved building Terminal's unchanged Dockerfile natively instead
of using the published amd64 image. Only the image-selection field differs from
the quota-adjusted copy. `runs/phase4-terminal-native-proposal/deviation-check.json`
records the byte-level comparison; CPU/memory, code, tests and solution are
unchanged. The frozen ARM64 image is
`sha256:416d947bbff3dda7fc252b0f2e9e31981e7f7cb2857c425994abfdcb1e1c1d94`.

## Final checks and design

The resumed regression suite passed **388 tests, four skipped** after the
Git-free RTK preflight change. The wheel contains the current preflight and all
six Caveman asset files byte-for-byte; evidence is
`runs/phase4-package-check-final.json`.

The [UX/structure review](harbor-ux-structure-review.md#phase-4-structure-checkpoint)
still supports the design: normal configurations select named treatments,
shared preparation resolves assets, harness adapters preserve tool semantics,
and each backend owns its execution lifecycle. Existing SWE-bench/ScarfBench
runners retain extension support without requiring Harbor-format conversion.

MCP, full dependency locking and live Docker repetition retain their agreed
deferrals. Phase 5 conversion/retirement remains follow-on work. Phase 6 advanced
composition and effectiveness are separate from this phase's functional checks.

## Closure

H04-01 through H04-07 are complete. The Git-free RTK fix was validated in the
published-image retry and the native matrix. All owned pilot containers were
removed; seven unrelated ScarfBench containers were preserved. Cleanup and
matched image verification are recorded in the native pilot's `check.json`.
No additional Phase 4 task remains. Phase 6 is the next implementation phase;
Phase 5 remains deferred as agreed.
