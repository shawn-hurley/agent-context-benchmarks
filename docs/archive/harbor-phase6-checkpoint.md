# Phase 6 implementation checkpoint

> Historical design/checkpoint record. For current task status and accepted scope,
> use [Harbor cleanup](../harbor-cleanup.md). Later decisions there supersede
> open-task lists and compatibility requirements below.

September 18, 2026. H06-01, H06-03, H06-04 and H06-06 are implemented.
Effectiveness experiments (H06-05) remain user-owned. Phase 5 conversion and
retirement, MCP, full dependency locking and amd64 emulation repair remain deferred.

## Closure decision

On September 18, the user accepted Phase 6 as complete within this scope.
Additional report charts remain a follow-up. The two real-Qwen RH runs still
have incomplete measurements after their 600-second agent timeouts; closure
does not establish a quality or token-savings verdict for those runs.

Reproduction configs are in `config/phase6/harbor.baseline.yaml` and
`config/phase6/harbor.combined.yaml`, with outputs at `config/phase6/runs/`.
`acb clean --config <run.yaml> --dry-run` now previews cleanup using the same
config-relative output path as execution. Targeted cleanup, execution-plan and
Harbor configuration checks passed (103 tests); no existing results were deleted.

## Delivered behavior

- Dataset metric definitions survive preparation and reach Harbor job configuration.
  Native results are retained per harness, separately from ACB completed-grade means.
  Custom Python metrics run in disposable containers with a frozen image and script
  hash, 1 CPU, 1 GiB memory and a 300-second execution limit. No host script execution.
- Shared comparison logic powers CLI JSON, comparison HTML and public HTML APIs.
  Each dataset task is a benchmark. Multiple attempts retain individual grades and
  use their mean only when every scheduled attempt has a valid grade.
- Matching checks task identity/content, grade definition, budgets, attempts,
  runtime/resources and cache policy. Missing provenance and unmatched tasks are
  explicit. Single-harness inputs can compare different harnesses; multi-harness
  suites align by harness. Select one configuration per side if identities repeat.
- Quality is same, better, worse or mixed over comparable grades, with separate
  coverage. Unknown/error grades never silently become a quality result.
  There is no universal suite quality score.
- Token changes include fresh input, output, cache reads and cache creation, with
  buckets retained. Totals cover only named comparable benchmarks with complete
  measurements. No matched measurements means unavailable, not zero.
- Standalone benchmark HTML preserves charts, tool/shell breakdowns, patches,
  trial grades, raw trajectories, arguments/results/errors, request tables,
  timing, token buckets, context and captured artifacts. Comparison pages link
  directly to both sides. Missing telemetry stays unavailable. Agent turns,
  model requests and tool calls are separate; cross-harness turn definitions
  differ, so their counts have no comparison delta.
- RTK runs before Caveman context. Recovery returns exact post-RTK bytes.
  Goose chains the two shell wrappers explicitly. Other configuration conflicts
  still fail. Both extensions must be selected; composition is not a default.
- Each multi-step invocation gets a fresh harness session. Task workspace and
  recovery store persist within the trial. Extension services restart between
  steps and stop for grading. Usage retains step identity and local request
  index plus a continuous benchmark request index. Task timeouts are honored
  subject to the configured upper limit. Missing visited-step evidence or an
  aborted step makes measurement completeness false.

## Functional evidence

The lifecycle checks use controlled model responses through actual harnesses and containers. A separate RH comparison uses the local Qwen model; these bounded checks do not establish effectiveness. The user cleaned the earlier run outputs; the checks below were rerun to restore live evidence.

| Check | Saved evidence | Result |
| --- | --- | --- |
| Non-average native metric | `runs/phase6-metrics/check.json` | Two successful trials: Harbor sum 2, ACB completed mean 1 |
| Custom metric through Harbor job | `runs/phase6-metrics/check.json` | Native script result preserved independently of primary task reward |
| Combined extensions, Harbor | `runs/phase6-combined-harbor/check.json` | All four harnesses; both components active; exact recovery and diagnostic preservation |
| Combined extensions, retained runners | `runs/phase6-combined-legacy/check.json` | All four harnesses; both components active; recovery and cleanup pass |
| Two steps with both extensions | `runs/phase6-multistep-success/check.json` | All four harnesses; complete accounting, distinct requests, per-step artifacts |
| Early stop | `runs/phase6-multistep-early-stop/check.json` | Pi: first grade 0 stops before second step; valid complete measurement |
| Later-step timeout | `runs/phase6-multistep-later-timeout/check.json` | Pi: second-step timeout retained as error; first-step usage preserved; incomplete measurement |
| Local Qwen RH comparison | `runs/phase6-rh-real/pilot.json` | Pi baseline and combined extensions; 600-second budgets; approved quota exception recorded |
| Report review | `runs/phase6-report-review/` | Individual, matched and partial bundles; Chrome reviewed comparison and detail charts; link and escaping tests |

Regression: **413 passed, four existing skips**, with the comparison tests rerun after the real-data presentation fixes. The final wheel includes the new modules; `git diff --check` passes. Fresh logs and aggregate check results are in `runs/phase6-verification/`. All rerun-owned containers were removed; the seven preexisting ScarfBench validation containers were left untouched. Tests cover
all four quality directions, lower-is-better, tolerances, unmatched/revised/budget
mismatches, missing grades/provenance/measurements, zero baselines, script tampering,
owned metric-container cleanup on timeout/cancellation, repeated stream events,
parallel calls, non-lexical step order and missing visited-step artifacts.

## Fresh real-model RH results

The requested rerun used Pi and
`mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2` at the local server.
Both arms used task-0000 at the pinned RH revision, identical 600-second agent
budgets, and the previously approved quota-only exception documented in
`runs/phase6-rh-storage-copy/deviation.json`.

| Observation | Baseline | RTK + Caveman |
| --- | ---: | ---: |
| Trial outcome | AgentTimeoutError | AgentTimeoutError |
| Native verifier reward | 0 | 0 |
| Captured agent turns | 22 | 29 |
| Captured model requests | 21 | 28 |
| Captured tool calls | 21 | 28 |
| Captured tokens, including cache reads | 463,243 | 902,475 |
| Measurement complete | No | No |

These are partial observations, so the comparison correctly provides no quality
verdict, token delta or comparable suite token total. A timeout remains distinct
from a completed grade of zero. RTK recorded four rewrites with no integration
errors. Caveman loaded and passed its recovery probe but recorded no compression
in this RH trial; its actual compression/retrieval behavior is demonstrated by
the separately labeled controlled checks.

Open `runs/phase6-report-review/index.html` for the report index or
`runs/phase6-report-review/matched.html` for the real Qwen comparison. Both sides
link to standalone benchmark pages containing the actual tool trajectories,
request timing, token buckets, errors, native results and captured artifacts.
Machine-readable observations are in `runs/phase6-verification/real-model-summary.json`. Chrome verified both detail destinations and expanded the real trial evidence without JavaScript errors; `runs/phase6-report-review/browser-check.json` and the neighboring screenshots retain that review.

## Practical limits

Historical reports lacking provenance/collection evidence are unverified; generating
HTML does not retroactively prove comparability. Dataset-native aggregates may use
Harbor's missing-reward semantics; read their labels separately from benchmark grades.
Custom metrics are not executed again during report import, so cancelled jobs can
lack their final native aggregate. Script dependencies are not fully locked; offline
script execution disables networking and dependencies must already be available.

Charts and optional patch rendering use CDN assets. Tables and embedded raw evidence
remain available offline. Cross-model token counts may use different tokenizers and
are not automatically comparable prices. Fixture traffic proves functionality, not
savings, quality gains, or statistical significance. Session resumption is not supported.

## Design review

Configuration still resolves once into a shared plan. Shared preparation and harness
adapters serve both backends; Harbor owns its lifecycle. Comparison policy lives in
`acb/comparison.py`, telemetry interpretation in `acb/telemetry.py`, and HTML rendering
in `acb/comparison_html.py` plus the retained individual-chart helpers. Obsolete HTML
suite-average comparison code was removed. Custom metric execution and the pinned
Harbor compatibility hook are isolated in `acb/harbor/metrics.py`.

No benchmark conversion or old-runner retirement is implied by this checkpoint.
