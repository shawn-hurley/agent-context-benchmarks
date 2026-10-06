# Quick Start validation — October 6, 2026

## Environment and method

Validation used fresh local Git clones, new virtual environments, the locked
Python dependencies, and separate ACB configuration/output/cache directories.
The first install selected Python 3.13.15; package and regression checks used
Python 3.12.14. No SWE-bench initialization or existing harness assets were needed.
One model configuration entry was supplied explicitly; credentials stayed in its
environment variable.

A separate Colima VM provided Docker 29.5.2 on macOS ARM64, with Compose 5.6.0,
Buildx 0.37.2, BuildKit enabled, four CPUs, 12 GiB RAM, and 60 GiB disk.
Its image store was initially empty and its filesystem was not shared with the
controller. The baseline Podman VM, images, caches, and configuration were retained.
Retries reused images after the initial cold downloads/source build.

Fixes were applied to the local validation checkout and recorded in temporary
commits as problems were found. The final Cart confirmation used snapshot
`ad9573f`; the checked-in acceptance script records its revision in `check.json`.
This validates local changes, not publication to the public ACB repository.

## Results

| Check | Outcome |
| --- | --- |
| Locked dependency installation in fresh clones | Passed |
| Setup oracle / nop | Grades 1 / 0, completed |
| GPT-5.6-Luna with Goose 1.50.1 | Grade 1, four transcript tool calls; proxy accounting failed before its fix |
| Responses HTTP replay after fix | One metric row, 120 input tokens, 6 output tokens, one shell tool call before transport end |
| Native Cart oracle / nop | Grades 1 / 0 |
| Standalone Cart confirmation after wrapper fix | Grade 1, 81,680-byte source diff; about 22 seconds with warmed build cache |
| Control report ZIP navigation | Two HTML pages per bundle; all local links and anchors resolve |
| Python regression suite | 635 passed, four opt-in skips; two existing fork deprecation warnings |
| Rust filter tests | 16 passed |
| Source distribution / wheel / installed-package check | Passed |
| Public corrected ScarfBench checkout | Pending maintainer push |

The first native Cart oracle took about 460 seconds, including time stalled in
the Docker wrapper before repair. Nop took about 117 seconds. The cold proxy
source build took about seven minutes; this is not a general timing guarantee.

## Bugs found and fixed

1. Maven cache options were missing from benchmark schema validation.
2. Homebrew Docker needed Compose and Buildx available under its CLI plugin
   directory. The guide documents both; the acceptance script checks them early.
3. Docker log bind mounts referenced the daemon filesystem. ACB now transfers
   convention logs/rewards using Harbor's copy lifecycle.
4. Docker rejected a proxy host mapping on a service sharing another service's
   network namespace. The mapping now belongs to the main service.
5. The native Docker Maven wrapper invoked itself through PATH. It now resolves
   the engine's absolute executable before installing the wrapper.
6. Goose closed Responses streams at `response.completed`, before transport
   end. Metrics now finalize at that terminal event, with request-ID deduplication.
   Non-streaming responses keep the existing final-chunk JSON path.
7. An agent with no measured model requests was marked complete. It now retains
   an explicit incomplete-accounting error while preserving collection status.

The real-model trial exposed the accounting bug rather than passing the full
measurement gate. Its original evidence is retained. The corrected proxy passed
a fragmented HTTP replay with an SDK-style early close and no model calls.
A second paid trial has not been run; real-provider accounting after this fix
remains unverified. The replay does not establish cache pricing, every provider
format, or the full Docker harness/SWE-bench/isolation matrix.

## Retained evidence and publication

Local evidence is under the ignored directory
`runs/quickstart-validation-20261006/`: control runs and ZIPs, the original model
trial, failed setup attempts, source-build/package logs, the replay script and
metric, and `controls/confirmation.json` for the standalone Cart check.

Cart used the local corrected benchmark at
`d0a6a3d3bd7c0e8a80bc6474737475a593ba4418` and native Scarf CLI 0.1.2.
At the last check, the public fork branch still pointed to
`765436eda20130f71ff48845537223e0186606cd`, 48 commits behind that local revision.
The guide intentionally fails a pinned checkout rather than substituting the
older tests. Both ACB changes and the corrected fork revision must be published
before the public clone instructions can be considered validated.

See [Quick Start](quick-start.md) for repeatable checks and maintainer push steps.
