# Interactive run setup

Start the full-screen editor in a terminal:

```sh
uv run acb run
```

For an installed package, use `acb run`. Both stdin and stdout must be attached
to a terminal. You can select a registry directory explicitly:

```sh
uv run acb run --config-dir config/quickstart
```

Supply only `--config-dir` and optionally `--verbose` when opening the editor.
`--config run.yaml` executes that file directly; to edit it interactively, open
the editor and choose **Load YAML file**. Benchmark/model selection flags and
`--control` select direct execution instead of the editor.

## Create or edit a run

1. Choose **Create new run** or **Load YAML file**.
2. Select a field and press Enter to edit it.
3. Choose **Review** to validate the configuration without downloading assets,
   starting containers or making model requests.
4. Correct any validation errors, then choose **Save configuration**, **Save and
   run**, or **Run without saving**. Saving configuration does not prepare or run
   anything. Run actions start preparation and model execution; model calls may
   incur charges. **Back** returns to editing; **Cancel** exits.

**Save configuration** and **Save and run** ask for a `.yaml` or `.yml` destination. When editing an
existing file, the destination defaults to that source file. A new run defaults
to `run.<run-id>.yaml` in the current directory. The editor preserves unrelated
fields and comments when patching a loaded file, checks for concurrent changes,
and refuses to overwrite a different existing file. Saving into another
directory evaluates relative input paths against that destination. The UI then
shows the destination's effective settings again; choose the save action to
commit the file. Save failures remain visible so you can return to editing.

**Run without saving** executes the current draft without changing the source
YAML. The run still retains requested/resolved configuration as evidence.
**Cancel** saves nothing and starts no run. After **Save configuration**, the
editor exits and prints commands for inspecting and executing the saved file.

Review shows the effective environment, task source, model alias and provider
ID/endpoint, credential environment reference, workflow stages, selections,
limits, output/cache directories, harness versions, timeouts, treatments and
configuration sources. Credential values are not read for the review. Use
PageUp/PageDown to scroll through details while the save/run actions remain
selectable. Task count remains pending until discovery or preparation.
In very short terminals, Review keeps the actions visible and asks you to
enlarge the terminal to read the effective settings.

## Keyboard controls

| Control | Action |
| --- | --- |
| Up/Down or `k`/`j` | Move through menu choices; the menu scrolls to keep the selected choice visible |
| Enter | Open a field, choose an action, or accept a text value |
| Space | Toggle a choice in the Harnesses, Skills or Extensions menus |
| Enter in a selection menu | Toggle the highlighted choice; select **Done** to apply the selection |
| Escape | Leave a field/menu without applying its pending edits, or go back/cancel as shown on screen |
| Backspace | Delete the last character in a text prompt |
| PageUp/PageDown in Review | Scroll through effective settings, including long paths and inherited options |

Text prompts edit at the end of the value. Clear the existing text with
Backspace before replacing it. Empty input clears optional task IDs, task limit
or timeout; the run ID and concurrent trial count require values.

## Editable fields

| Field | Meaning |
| --- | --- |
| Run ID | Result directory name; collisions receive a suffix |
| Benchmark | Built-in or configured benchmark identity |
| Harnesses | One or more of Goose, Pi, OpenCode and Claude Code |
| Model | Alias from the selected model registry |
| Workflow | No workflow, a bundled workflow or a custom directory |
| Task IDs | Enter IDs manually or browse/download a listing and select tasks; no checked IDs selects all eligible tasks |
| Task limit | Positive maximum number of selected tasks per harness; empty removes the limit |
| Skills / Extensions | Shared selections for the selected harnesses; selecting none supplies an empty list |
| Timeout (seconds) | Positive timeout applied across harnesses; empty uses configured defaults |
| Concurrent trials (global) | Positive maximum number of concurrent trials across the entire run |

Models must already be defined in `models.yaml` or `proxy.yaml`; the editor does
not create provider definitions. The [Quick Start](quick-start.md) explains that
setup. For a new run, registries are discovered from the current directory and
its ancestors. A loaded run uses its YAML `config_dir` or discovery beside that
file. Explicit CLI `--config-dir` overrides those choices.

Other settings, including environment, output directory, component options,
per-harness overrides and grading controls, are configured through YAML or the
direct CLI. Loaded advanced settings remain part of the draft. Review summarizes
the resolved settings; `acb resolve --config your-run.yaml --json` exposes the
complete plan. Use `prepare` to inspect the selected task manifest and runtime
requirements.

## Discover and select tasks

Open **Task IDs** and choose:

- **Enter task IDs…** for comma- or whitespace-separated IDs.
- **Browse local/cached tasks** to list IDs without network requests.
- **Download task listing** to allow remote metadata/data downloads and cache
  the IDs. This action does not start containers or make model calls.

Toggle IDs with Space or Enter, then choose **Done**. No checked IDs means all
eligible tasks. The task limit still applies. Cached listings can be partial;
their coverage is stated rather than presented as a complete dataset. A model
definition and valid experiment configuration are needed to resolve the draft
before discovery. See [task discovery](task-discovery.md) for data-source behavior
and the equivalent `acb tasks` command.

## Select your own workflow

The **Workflow** menu automatically lists the workflows bundled with ACB:
`kantra-controller`, `kantra-rgctl` and `migiq`. It also offers **No workflow**,
the current custom selection if one is loaded, and **Enter workflow directory…**.
The UI does not automatically scan project directories for custom workflows.

For a workflow you created following the [workflow authoring guide](workflows.md):

1. Select **Workflow → Enter workflow directory…**.
2. Enter the directory containing `workflow.yaml`, such as
   `./workflows/my-migration`. Enter the directory, not the YAML filename.
3. Choose a supported benchmark and harness, then **Review**.

An absolute directory path removes ambiguity about where the workflow lives.
Relative paths use the loaded run YAML's directory. For a new run executed
without saving, they use the current directory; for a saved run, they use the
destination YAML's directory. If a new draft's workflow exists only relative to
the future save destination, Review offers the save actions so that the path
can be validated there before saving or execution.

You can also add `workflow: ./workflows/my-migration` to an existing run YAML
and choose **Load YAML file**. Its workflow appears as the current selection.
For example, this layout permits that exact relative path:

```text
experiment/
  run.yaml
  workflows/
    my-migration/
      workflow.yaml
      plan.md
      implement.md
```

Workflow validation checks the definition, instruction/assets files and declared
benchmark/harness compatibility. Run-level workflows currently require
ACB-exported ScarfBench, SWE-bench or SWE-bench Lite tasks. Invalid selections
show an error at Review and require correction before the run can start.
