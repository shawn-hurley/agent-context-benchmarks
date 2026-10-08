# Accounting and RH baseline follow-up

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](../harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

> Historical checkpoint. Use the [September 17 phase audit](harbor-phase-2-3-audit.md)
> for current scope and remaining work. Subsequent checks closed setup/stream
> cancellation and other items described as outstanding below. Claude amd64/QEMU
> failure is now an accepted [known issue](../harbor-operations.md#known-issue-claude-code-on-emulated-amd64-benchmarks),
> with investigation deferred; phases 2/3 are complete under the revised scope.

September 16, 2026. Scope: finish accounting/failure-reporting checks, investigate
the RH baseline blockers, then pause for a configuration UX and structure review.
Status: this focused pass is finished and implementation is paused. Step 1's
Podman checks pass; step 2's full four-harness acceptance remains blocked.

## 1. Accounting and failure reporting: verified scope

The four-harness deterministic live check passes in
`runs/harbor-accounting-check/accounting-check.json`:

| Harness | Model requests | Tokens | Reward |
| --- | ---: | ---: | ---: |
| Goose | 6 | 720 | 1 |
| Pi | 5 | 600 | 1 |
| OpenCode | 6 | 720 | 1 |
| Claude Code | 5 | 600 | 1 |

The check reconciles proxy records against the independent fixture-server POST
ledger, verifies unique request IDs, excludes discovery from model usage, and
confirms the proxy is stopped before verification. The server reports 100 input
and 20 output tokens per inference. Monetary costs remain unspecified.

`runs/harbor-partial-job-check/cancellation-check.json` verifies a serial
two-attempt cancellation: one failed emitted trial, one explicitly missing trial,
both measurement-incomplete, nonzero worker exit, and no matching live containers.

Reporting fixes:

- A failed step makes the trial an error even if aggregate rewards exist.
- Agent timeouts stay attributed to agent execution when verification runs later.
- Exception timestamps help identify the phase where other failures occurred.
- Multi-step collection status is retained separately from accounting completeness.
- A truncated metrics stream preserves valid usage and reports incomplete
  measurement; covered at the proxy collection boundary by a regression test.

Reproduction from the repository root, using the isolated Harbor Python:

```sh
python -m scripts.check_harbor_accounting /private/tmp/acb-bridge-plan.json runs/accounting-repeat
python -m scripts.check_harbor_cancellation /private/tmp/acb-bridge-plan.json runs/cancellation-repeat --attempts 2
```

Use unused output directories. The plan and runtime under `/private/tmp` are local
and ephemeral; the successful accounting run saves its own `fixture-plan.json`.
The fixture still requires its existing local base image. These checks close
H03-14 and H03-17 within the current Podman scope. Docker, streaming/setup
cancellation, and remaining negative lifecycle cases retain their separate gates.

## 2. RH baseline: runtime blocker and diagnostic

**The four-harness RH acceptance gate remains open.** Only the ARM64 Podman engine
is configured here; no native amd64 connection has been supplied. The selected RH
task pins an amd64 image. No task image, grading script, or dependency was changed.

Claude 2.1.241 crashes even for `--version` in that image under QEMU. Preparation
now executes the installed binary's version command in the actual task environment,
records `harness-startup.json`, checks the pinned version, and rejects failure
before model requests. This is a startup check, not a model-protocol guarantee.

- `runs/harbor-rh-startup-check`: Claude rejected with binary exit 139 and failed
  preparation. Runtime diagnostics and attempted-workaround logs are retained.
- `runs/harbor-native-startup-check`: all four pinned binaries pass on native ARM64.
- Experimental JIT-disable and explicit-loader startup attempts produced no usable
  binary. The diagnostic container was explicitly stopped; neither workaround
  was adopted.

Pi's original 600-second pilot was actively issuing tools, including attempted
edits. It repeatedly failed to import `yaml` because the agent had not activated
the image's `testbed` conda environment; the task verifier activates that environment.
The follow-up is a **single-harness diagnostic**, retaining the same task revision,
model, and 600-second limit, with explicit `conda_env: testbed`.

Diagnostic artifacts: `runs/harbor-rh-qwen-pi-conda`. Its `diagnostic.json` identifies
the changed setting and notes that preparation metadata inherited from the source
plan is not evidence validating the override.

Final outcome: **agent timeout at 600 seconds; native verifier reward 0**. The
worker exits nonzero. The report attributes the failure to `agent_execution`,
keeps binary resolution unknown, and records collection-complete but
measurement-incomplete. Recorded usage: **20 model requests, 38,907 tokens**;
in-flight usage may be absent. The transcript has 20 tool calls (7 bash, 8 read,
5 write), zero tool errors, and zero missing-`yaml` errors. Fixing activation
removed the observed dependency failures but did not produce a completed solution
within this budget. No passing RH baseline is claimed.
After worker exit, the provider showed no remaining containers from these Harbor
checks or diagnostics. Unrelated ScarfBench containers were left untouched.

The supported next route for the full RH baseline is native amd64 execution with
a reachable model endpoint, or a separately validated ARM task variant. A larger
agent budget is a distinct experiment change; the upstream task declares 1800
seconds while both local diagnostics used 600.

## Verification and design review

Current regression suite: **156 passed, 4 skipped**. The four-harness native
startup probe and live accounting/cancellation checks supplement the unit suite.
Existing RH pilot reports have not been rewritten to retrofit the new accounting
filter; their historical turn counts still include discovery requests.

The [UX and structure review](harbor-ux-structure-review.md) recommends retaining
the current design and finishing its boundaries. Its small example resolves to
all four harnesses with the shared conda setting and budget, without containers
or model calls. Remaining design work is explicit task language environments,
consistent normalized-plan consumption, internal plan validation/provenance,
and clearer preparation error presentation.

ScarfBench/SWE-bench parity, extension treatments, and the rest of the six-phase
migration remain pending. They were not expanded during this focused follow-up.
