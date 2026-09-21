# Report rendering

ACB reports compare grades per benchmark and display captured usage and trajectory
evidence. Incomplete measurements stay labeled and excluded from comparable token
totals. The current cleanup status lives in [the cleanup record](harbor-cleanup.md).

## Generate reports

```sh
acb report runs/example --html
acb compare runs/baseline runs/candidate runs/another-candidate --html runs/comparison.html
```

The output consists of an overview and `<report-name>-benchmarks/` detail pages.
Comparisons also write `<report-name>.comparison.json`. Keep these files together.
For an in-memory document, use `acb.comparison_html.build_report(run_dirs)`;
`write_reports(run_dirs, destination)` writes the bundle.

## Module boundaries

| Module | Responsibility |
| --- | --- |
| `acb/report_data.py` | Discover current harness reports and cache/index each input by trial. |
| `acb/comparison.py` | Derive grades, coverage and comparability; compare loaded records without file reads. |
| `acb/report_metrics.py` | Transform captured requests into context, duration, token and tool chart data. |
| `acb/html_components.py` | Compose documents/sections, allocate chart IDs, escape text/JSON and bound evidence previews. |
| `acb/html_report.py` | Render benchmark evidence, requests, tool statistics, charts and patch previews. |
| `acb/comparison_html.py` | Compose comparison overviews and coordinate in-memory or file output. |

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
Backward compatibility with old reports and rendering APIs is out of scope.

HTML text is escaped; embedded chart JSON escapes HTML delimiters and Unicode
line separators. Tool commands, model names and patch text are data, not markup.
Patch views are escaped text, removing the separate diff2html script dependency.

Each artifact preview reads at most 32 KiB, with at most 40 artifacts and 128 KiB
of artifact content per trial. Large structured evidence and patches also have
bounded previews. Local file links expose full original evidence without embedding
it all. Links need the original run directory, so copied bundles retain previews
but may lose access to full originals. Evidence symlinks outside the trial are not
followed for embedded previews.

Chart.js loads from a CDN. When unavailable, evidence tables, statuses and patch
previews remain readable. Request charts show captured observations; they do not
turn incomplete measurements into comparable totals. Sparse request indices use
missing values rather than fabricated zeros.

## Verification

`tests/test_html_rendering.py` covers composition, unique IDs, escaping, bounded
artifacts, one-time input loading, task selection, multiple candidates/harnesses,
partial measurements and multi-step evidence. Comparison semantics remain covered
by `tests/test_comparison.py`; chart transformations by usage/tool tests.
Run the full gate described in [maintenance checks](../scripts/README.md).
