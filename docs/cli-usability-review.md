# CLI usability review

Reviewed October 7, 2026 against the source CLI, interactive editor, configuration
resolver, reporting commands and current Quick Start/operations documentation.

ACB can support the core benchmark workflow: configure an experiment, discover
components, validate settings, prepare prerequisites, execute measured runs or
grading controls, inspect results, compare treatments, export evidence and clean
output. The main problems were discovering how to do those things and knowing
when a command had failed or ignored an instruction. The fixes below improve
those paths without adding new execution modes.

## Walkthrough findings and fixes

| Priority | Finding and reproduction before the changes | Result after the changes |
| --- | --- | --- |
| High | `acb run --config run.yaml --limit 1` silently ignored the limit; a changed model or benchmark was similarly ignored | Mixed configuration/selection flags fail with exit 2 and instructions to edit the YAML, before loading configuration or starting execution |
| High | `acb report missing-run` returned `[]` and exit 0 | Missing directories, missing reports and malformed JSON fail with exit 1 and the input path; all inputs are validated before exports are written |
| High | Interactive menus clipped choices below the terminal height, including selected choices, errors and navigation hints | Menus scroll to the selected choice and recalculate on resize; error and navigation lines remain at the bottom |
| Medium | `acb resolve --config missing.yaml` printed a traceback and suggested looking for `acb.log` despite no run having started; installed and module entry points handled errors differently | Expected configuration, file and worker errors use the same concise stderr message and nonzero exit through either entry point; `run --verbose` retains tracebacks |
| Medium | Most `run` flags had no help; top-level help omitted the workflow, interactive behavior and advanced configuration route | Top-level help includes a workflow; subcommand help explains aliases, paths, concurrency, defaults, controls, model calls and YAML selections; bare `acb` shows help |
| Medium | Partial explicit flags produced a generic missing-identities message; `compare` accepted one input before failing in its handler | Explicit runs name missing flags and alternative invocation modes; `compare` requires a baseline and candidate in argument parsing |
| Medium | `acb init` created files without explaining the next action or which benchmark it selected | Output names the RH SWE-bench starter, files to edit, a usable `resolve` command and the separate self-contained Quick Start; the generated run explicitly selects its own registry directory |
| Medium | `acb list models` returned `{}` without explaining how to make models available | JSON remains on stdout; an empty registry gets a stderr hint naming `models.yaml` and `--config-dir` |
| Medium | `clean` described every output item as a run, and piped input could end in an EOF error | Help and preview explain that reports/bundles are included; noninteractive deletion requires `--yes` and points to `--dry-run` first |
| Low | Literal `~/...` paths worked for some flags but failed for config files, catalog lookup and report/export paths | These CLI paths expand the home directory consistently |

The [operations guide](harbor-operations.md#choosing-a-command) maps user goals
to commands and explains how to run multiple harnesses, select treatments,
choose task limits, compare harnesses and interpret process success.

## Remaining friction

The follow-up discussion approved save-only setup, effective-settings review,
terminal summaries, task discovery and named starter templates. These are now
implemented:

- **Save configuration** exits the editor without preparation/model execution.
- Review uses the resolved environment, provider endpoint, output/cache paths,
  harness settings, treatments and source files, with PageUp/PageDown scrolling.
  Choosing a save destination resolves and reviews paths there before committing.
- `resolve`, `prepare`, `tasks`, `report` and `compare` show summaries in terminals
  and retain JSON when piped. Explicit `--text`/`--json` flags control the format;
  progress and export locations use stderr.
- `acb tasks` and the interactive task picker discover local/cached IDs, with an
  explicit download option and visible partial coverage.
- `acb init --template quickstart|rh-swe-bench` names the starting point. The
  quickstart task is packaged; plain `init` retains the RH default for scripts.

Custom workflows remain a directory entry by user preference. Maintaining
compatibility labels is deferred; ordinary resolution/preparation validation
continues to apply. Remaining opportunities are recorded below.

| Priority | Gap | Available workflow / proposed improvement |
| --- | --- | --- |
| Low | Custom workflows require a directory entry | Kept as requested; bundled workflows and a loaded custom selection remain visible |
| Low | Discovering component definitions is easier than discovering their supported combinations | Deferred for a future pass; `resolve` validates configurations and `prepare` checks runtime requirements |
| Low | Experiment arms need separate run YAML files; there is no matrix command | Keep baseline/treatment files explicit and compare saved directories. A matrix feature needs its own design for provenance and result naming |
| Low | There are no dedicated status/resume commands | Use live progress, `job.log` and the saved trial evidence; reruns reserve a new directory. Resume would require execution semantics, not just a CLI alias |

Use `--json` when a script needs an explicit format; export-location messages
and preparation progress are sent to stderr. Component `list` retains its JSON
format. Task discovery is covered in the [task guide](task-discovery.md), and
starting points in the [template guide](starter-templates.md).

## Verification and limits

The walkthrough exercised top-level/subcommand help, missing and malformed
configuration, empty registries, starter initialization and resolution, report
validation, comparison argument order, cleanup previews and noninteractive
errors. Regression tests exercise silent-flag conflicts before execution,
entry-point parity, preservation of existing exports on invalid inputs,
recovery of harness reports without a suite overview, registry selection from
another directory, home path expansion and menu navigation/resizing at 10 and
24 terminal rows.

Verification uses temporary files, saved report fixtures and simulated terminal
input. After the approved improvements, the full regression suite passed:
**680 passed, 4 skipped**. A walkthrough
through the installed console entry point also passed configuration resolution,
report/compare JSON, HTML and ZIP exports, invalid-input exits and cleanup
preview. Interactive setup, task selection, PageDown review scrolling, save-only
behavior and cancellation were checked in a real 24-row terminal; resizing
regression checks use simulated terminal input. Save-only produced no run output
or preparation cache. The source-distribution/wheel build and installed-package
gate passed, including quickstart creation, task discovery and offline export
outside the checkout.
`git diff --check` passed. No container acceptance or paid model run was
performed for this review.
Existing [Quick Start acceptance evidence](quick-start-validation.md) describes
engine/harness coverage; this review does not expand it. Process success remains
separate from valid grades and complete measurements.
