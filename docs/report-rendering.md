# Report rendering

ACB reports compare grades per benchmark and display captured usage and trajectory
evidence. Incomplete measurements stay labeled and excluded from comparable token
totals. The current cleanup status lives in [the cleanup record](harbor-cleanup.md).

## Generate reports

`acb report ACTUAL_RUN` and `acb compare BASELINE_RUN CANDIDATE_RUN` show text
summaries in a terminal and JSON when piped. Use `--text` or `--json` explicitly
for scripts or saved output. Text reports include execution failures, their phase
and message, and available job-log/run-directory evidence. Incomplete usage is
unavailable rather than zero. Export notices go to stderr so JSON stays parseable.

```sh
acb report runs/example --html
acb compare runs/baseline runs/candidate runs/another-candidate --html runs/comparison.html
acb report runs/example --bundle example-report.zip
acb compare runs/baseline runs/candidate --bundle comparison-report.zip
```

HTML output consists of an overview and `<report-name>-benchmarks/`, which contains
detail pages, local scripts, and selected trial evidence. Comparisons also write
`<report-name>.comparison.json`. Keep the HTML, its directory, and comparison JSON together.

Use `--bundle FILE.zip` to share a report. Extract the archive and open `index.html`;
charts, filters, sorting, and navigation work offline without a web server. The ZIP
includes its comparison JSON when applicable. `--bundle` works by itself or alongside
`--html`. Source diffs and agent patches are exported in full; log previews retain
their existing size limits. Startup configuration is excluded and structured credential fields are redacted.
Saved logs and source changes retain their application content.

Overview tables support search, result/harness filters, incomplete-measurement filters,
and sorting. Comparisons also filter grade changes and initially list regressions first.
Each benchmark and its diagnostics move together during sorting and filtering.
Detail pages link back to the overview, to adjacent benchmarks, and to their sections.

Comparison summaries show grade-outcome counts, settings that differ between runs,
and paired token bars over comparable measurements only. Grades keep their original
scale and benchmark-specific direction; no normalized suite score is introduced.

For an in-memory document, use `acb.comparison_html.build_report(run_dirs)`; it embeds
local scripts inline. `write_reports(run_dirs, destination)` writes portable files;
`acb.report_bundle.write_bundle(run_dirs, destination)` writes a ZIP.

## Module boundaries

| Module | Responsibility |
| --- | --- |
| `acb/report_data.py` | Discover current harness reports and cache/index each input by trial. |
| `acb/comparison.py` | Derive grades, coverage and comparability; compare loaded records without file reads. |
| `acb/report_metrics.py` | Transform captured requests into context, duration, token and tool chart data. |
| `acb/html_components.py` | Compose documents/sections, allocate chart IDs, escape text/JSON and bound evidence previews. |
| `acb/html_report.py` | Render benchmark evidence, requests, tool statistics, charts and patch previews. |
| `acb/comparison_html.py` | Compose comparison overviews and coordinate in-memory or file output. |
| `acb/report_ui.py` | Result table controls, navigation, settings differences, and comparison bars. |
| `acb/report_bundle.py` | Export selected evidence and package portable ZIPs. |
| `acb/report_assets/` | Pinned Chart.js distribution/license and local table interaction script. |

A `ReportSource` owns one run/suite. `HarnessReport` reads its report and indexes
metrics, usage and classification records once. Detail pages select the benchmark's
trial IDs from those indexes. Multiple candidates reuse the loaded baseline.
Comparison semantics are shared with JSON/CLI reporting.

A `Section` contains trusted HTML and structured chart configurations.
`RenderContext` assigns unique IDs throughout a document; `render_page` assembles
sections and includes chart assets once. A duplicate chart ID fails composition.
No renderer splits complete HTML documents, rewrites headings in generated HTML,
or imports another renderer's private functions. Benchmark details use the same
components inline and as standalone pages.

## Evidence and limits

Current inputs are `report.json` with `evaluations`, usage/metrics JSONL at the
harness root, and visible `<harness>/<trial-id>/` artifacts. The renderer does not
use the old `instances/` aliases or infer evaluations from old metric formats.
Comparisons use saved benchmark contracts, revisions and task checksums. Missing
saved identity produces unavailable comparisons; reports never recover identity
from mutable external task files. Partial native submission/diff recovery remains
available for current trials. Backward compatibility with old reports and
rendering APIs is out of scope.

HTML text is escaped; embedded chart JSON escapes HTML delimiters and Unicode
line separators. Tool commands, model names and patch text are data, not markup.
Patch views are escaped text, removing the separate diff2html script dependency.

Each artifact preview reads at most 32 KiB, with at most 40 artifacts and 128 KiB
of artifact content per trial. Large structured evidence and patches also have
bounded previews. File reports export selected evidence with relative links, so copying or unzipping
the generated report does not require the original run directory. Exported structured
evidence redacts credential fields. Log previews select trial/transcript/grader/validation
logs; startup configuration, Praxis configuration, and full source trees are excluded.
Evidence text is stored with a `.txt` suffix and embedded previews stay escaped.
Evidence symlinks outside the trial are not followed. In-memory reports retain original
artifact links because they do not create an evidence directory.

Chart.js 4.5.1 is packaged locally with its MIT license; report rendering does not
fetch scripts from a CDN. Request charts show captured observations; they do not
turn incomplete measurements into comparable totals. Sparse request indices use
missing values rather than fabricated zeros.

## Verification

`tests/test_html_rendering.py` covers composition, unique IDs, escaping, bounded
artifacts, one-time input loading, task selection, multiple candidates/harnesses,
partial measurements and multi-step evidence. Comparison semantics remain covered
by `tests/test_comparison.py`; chart transformations by usage/tool tests.
`tests/test_report_bundle.py` covers relocated archives, local links/assets, exported
patches, bounded evidence, credential-field redaction, and CLI bundle generation.
Run the full gate described in [maintenance checks](../scripts/README.md).
