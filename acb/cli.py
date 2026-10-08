"""acb command-line entrypoint.

    acb run   # interactive run configuration
    acb run   --config config/run.requests-1142.yaml
    acb run   --benchmark swebench --harness goose --model mlx-community/Qwen3.8-27B-4bit \
              --run-id demo --limit 1
    acb report runs/<run_id>                     # show saved results
    acb report runs/<run_id> --html              # also write runs/<run_id>/report.html
    acb report runs/<a> runs/<b> --html          # combined multi-run HTML comparison
    acb compare runs/<a> runs/<b> ...            # comparison JSON
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from acb.config import RunConfig, Registries

RUN_FLAGS = ("benchmark", "harness", "model", "run_id", "limit", "max_workers")
CONFIG_DIR_HELP = ("registry directory, relative to the current directory; overrides YAML/discovery "
                   "(default: nearest .acb/ or config/ beside the run YAML, or current directory)")


def _cmd_run(args):
    if args.config:
        cfg = RunConfig.from_file(args.config)
    else:
        direct = any(getattr(args, key) is not None for key in (*RUN_FLAGS, "control"))
        if not direct:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                raise SystemExit("bare acb run requires an interactive terminal; use --config or explicit run flags")
            from acb.interactive_run import configure
            selected = configure(args.config_dir)
            if selected is None:
                return
            from acb.interactive_run import SavedConfiguration
            if isinstance(selected, SavedConfiguration):
                import shlex
                path = shlex.quote(str(selected.path))
                print(f"Saved configuration: {selected.path}\nValidate: acb resolve --config {path}\nRun: acb run --config {path}")
                return
            from acb.harbor.backend import run_plan
            run_plan(selected, verbose=args.verbose)
            return
        cfg = RunConfig(
            run_id=args.run_id, benchmark=args.benchmark, harness=args.harness,
            model=args.model, limit=args.limit,
            execution={"max_workers": args.max_workers if args.max_workers is not None else 4},
        )
    if args.config_dir:
        cfg.config_dir = str(Path(args.config_dir).expanduser().absolute())
    from acb.harbor.backend import run
    run(cfg, verbose=args.verbose, control=args.control)


def _cmd_resolve(args):
    from acb.resolver import resolve
    cfg = RunConfig.from_file(args.config)
    if args.config_dir:
        cfg.config_dir = str(Path(args.config_dir).expanduser().absolute())
    plan = resolve(cfg)
    if args.cmd == "prepare":
        from contextlib import redirect_stdout
        from acb.preparation import prepare
        with redirect_stdout(sys.stderr):
            document = prepare(plan)
    else:
        document = plan.to_dict()
    from acb.cli_output import emit, plan_lines
    emit(args, document, lambda: plan_lines(document))


def _cmd_tasks(args):
    from acb.resolver import resolve
    from acb.task_discovery import discover_tasks
    from acb.cli_output import emit, task_lines
    cfg = RunConfig.from_file(args.config)
    if args.config_dir:
        cfg.config_dir = str(Path(args.config_dir).expanduser().absolute())
    if args.download:
        print("Fetching task listings may download dataset metadata/data; no containers or model calls.", file=sys.stderr)
    document = discover_tasks(resolve(cfg).to_dict(), allow_download=args.download)
    emit(args, document, lambda: task_lines(document))


def _cmd_list(args):
    from acb.resolver import defaults
    registries = Registries.load(Path(args.config_dir).expanduser() if args.config_dir else None)
    category = "mcp_servers" if args.category == "mcp" else args.category
    entries = {**defaults().get(category, {}), **getattr(registries, category)}
    print(json.dumps(entries, indent=2))
    if not entries:
        hint = ("Add a model alias to models.yaml with its model ID, API and endpoint."
                if category == "models" else "No entries are configured in this category.")
        print(f"acb list {args.category}: {hint} Use --config-dir to select your registries.", file=sys.stderr)


def _cmd_init(args):
    from acb.starters import starter_files
    directory = Path(args.directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    for relative, content in starter_files(args.template, args.environment).items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as file:
                file.write(content)
            if path.suffix == ".sh":
                path.chmod(path.stat().st_mode | 0o111)
            print(f"created {path}")
        except FileExistsError:
            print(f"kept existing {path}")
    import shlex
    command = f"acb resolve --config {shlex.quote(str(directory / 'run.yaml'))}"
    print(f"\nStarter: {args.template}. Edit {directory / 'config/models.yaml'} with your model ID and endpoint,")
    print(f"then set Docker/Podman in {directory / 'config/machine.yaml'} and check the plan:\n  {command}")
    if args.template == "quickstart":
        config = shlex.quote(str(directory / "run.yaml"))
        print(f"Check setup without model calls:\n  acb run --config {config} --control oracle\n  acb run --config {config} --control nop")
        print("Expected grades: oracle 1, nop 0. These controls do not verify model access.")
    else:
        print("For the included setup task instead, use acb init DIRECTORY --template quickstart.")


def _report_inputs(paths, *, require_harnesses=False):
    """Validate all inputs before printing results or writing report artifacts."""
    from acb.report_data import ReportSource
    roots, reports = [], []
    for value in paths:
        root = Path(value).expanduser()
        source = ReportSource(root)
        report_path = root / "report.json"
        loaded = {}
        for path in [report_path, *sorted(root.glob("*/report.json"))]:
            if not path.is_file():
                continue
            try:
                report = json.loads(path.read_text())
            except ValueError as error:
                raise ValueError(f"invalid report JSON: {path}: {error}") from error
            if not isinstance(report, dict):
                raise ValueError(f"report must contain a JSON object: {path}")
            loaded[path] = report
        if not loaded:
            raise ValueError(f"no saved reports in {root}; use a run or harness directory containing report.json")
        if require_harnesses and not source.harnesses:
            raise ValueError(f"no harness reports in {root}; rendering and comparison require saved harness results")
        if report_path in loaded:
            reports.append(loaded[report_path])
        else:
            reports.extend(item.report for item in source.harnesses.values())
            if not source.harnesses:
                raise ValueError(f"no harness reports in {root}; use a run or harness directory containing report.json")
        roots.append(root)
    return roots, reports


def _cmd_report(args):
    run_dirs, reports = _report_inputs(args.run_dirs, require_harnesses=(
        len(args.run_dirs) > 1 or args.html is not None or bool(args.bundle)))

    from acb.cli_output import emit, report_lines, comparison_lines
    if len(run_dirs) > 1:
        from acb.comparison import compare
        payloads = [compare(run_dirs[0], root) for root in run_dirs[1:]]
        emit(args, payloads, lambda: comparison_lines(payloads))
    else:
        emit(args, reports[0] if len(reports) == 1 else reports, lambda: report_lines(run_dirs))

    if args.html is not None:
        if args.html:
            out_path = Path(args.html).expanduser()
        else:
            out_path = run_dirs[0] / "report.html"
        from acb.comparison_html import write_reports
        write_reports(run_dirs, out_path)
        print(f"html report: {out_path}", file=sys.stderr)
    if args.bundle:
        from acb.report_bundle import write_bundle
        print(f"report bundle: {write_bundle(run_dirs, Path(args.bundle).expanduser())}", file=sys.stderr)


def _cmd_clean(args):
    import shutil
    output_dir = (RunConfig.from_file(args.config).output_path() if args.config
                  else Path(args.output_dir or "runs").expanduser().resolve())
    if not output_dir.is_dir():
        raise ValueError(f"output directory does not exist or is not a directory: {output_dir}")

    to_delete = [
        p for p in output_dir.iterdir()
        if p.name not in {".cache", ".gitkeep"}
    ]

    if not to_delete:
        print("No output items to delete.")
        return

    print(f"Will delete {len(to_delete)} item(s) in {output_dir} (all except .cache and .gitkeep):")
    for p in sorted(to_delete):
        print(f"  {p.name}")

    if args.dry_run:
        return

    if not args.yes:
        if not sys.stdin.isatty():
            raise ValueError("clean requires an interactive terminal for confirmation; inspect --dry-run, then use --yes")
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    for p in to_delete:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()

    print(f"Deleted {len(to_delete)} item(s).")


def _cmd_compare(args):
    from acb.comparison import compare
    roots, _ = _report_inputs([args.baseline, *args.candidates], require_harnesses=True)
    payloads = [compare(roots[0], candidate) for candidate in roots[1:]]
    from acb.cli_output import emit, comparison_lines
    emit(args, payloads, lambda: comparison_lines(payloads))
    if args.html is not None:
        from acb.comparison_html import write_reports
        out_path = Path(args.html).expanduser() if args.html else roots[0] / "comparison.html"
        write_reports(roots, out_path)
        print(f"html comparison: {out_path}", file=sys.stderr)
    if args.bundle:
        from acb.report_bundle import write_bundle
        print(f"comparison bundle: {write_bundle(roots, Path(args.bundle).expanduser())}", file=sys.stderr)


def main(argv=None):
    def output_flags(parser):
        output = parser.add_mutually_exclusive_group()
        output.add_argument("--json", dest="output_format", action="store_const", const="json",
                            help="print JSON to stdout (default when output is piped)")
        output.add_argument("--text", dest="output_format", action="store_const", const="text",
                            help="print a readable summary (default in a terminal)")
    ap = argparse.ArgumentParser(
        prog="acb", description="Run coding benchmarks and compare grades, tokens and tool use.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Typical workflow:
  acb init experiment --template quickstart   Create an included setup task
  acb list models --config-dir experiment/config
  acb resolve --config experiment/run.yaml     Validate; no containers or model calls
  acb tasks --config experiment/run.yaml       List local/cached task IDs
  acb prepare --config experiment/run.yaml     Download/build and inspect prerequisites
  acb run --config experiment/run.yaml         Execute; model calls may incur charges
  acb report ACTUAL_RUN --html                 Open ACTUAL_RUN/report.html
  acb compare BASELINE_RUN CANDIDATE_RUN --html

Use 'acb run' in a terminal for interactive setup. Use YAML for multiple
harnesses, task IDs, workflows, skills, extensions and execution settings.
Run 'acb COMMAND --help' for details. See README.md and docs/quick-start.md
for the self-contained setup task and container requirements.""")
    sub = ap.add_subparsers(dest="cmd", required=True, title="commands")

    r = sub.add_parser("run", help="configure interactively or execute a benchmark",
                       description="Run from YAML, explicit flags, or interactive setup (no selection flags). "
                       "A normal run contacts the configured model provider and may incur charges. "
                       "The editor also offers Save configuration without execution.",
                       epilog="Use --config for multiple harnesses, workflows, task IDs, skills and extensions. "
                       "Selection flags cannot be combined with --config; edit the YAML instead. "
                       "Exit 0 means execution completed; inspect reports for grades and measurement coverage.")
    r.add_argument("--config", metavar="YAML", help="run YAML, relative to the current directory")
    r.add_argument("--config-dir", metavar="DIR", help=CONFIG_DIR_HELP)
    r.add_argument("--control", choices=("nop", "oracle"), help="run a Harbor grading control without model calls")
    r.add_argument("--benchmark", help="benchmark identity (see acb list benchmarks)")
    r.add_argument("--harness", help="one harness identity (see acb list harnesses)")
    r.add_argument("--model", help="configured model alias (see acb list models); provider ID belongs in models.yaml")
    r.add_argument("--run-id", help="run directory name under output_dir (default parent: runs/); collisions get a suffix")
    r.add_argument("--limit", type=int, help="positive task limit per harness; omitted means all selected tasks")
    r.add_argument("--max-workers", type=int, help="positive global limit on concurrent trials across harnesses (default: 4)")
    r.add_argument("--verbose", "-v", action="store_true",
                   help="show ACB debug logging and error tracebacks; worker output is saved in .harbor/worker.log")
    r.set_defaults(func=_cmd_run)

    for name in ("resolve", "prepare"):
        description = ("Validate configuration and show the effective plan. "
                       "Starts no containers, makes no model calls, and does not enumerate the task manifest."
                       if name == "resolve" else
                       "Download/build prerequisites, inspect disposable task containers and print the prepared plan. "
                       "Makes no model requests; measured protocol and grading checks remain pending until run.")
        command = sub.add_parser(name, help=("validate and print a plan without execution" if name == "resolve"
                                            else "download/build and inspect prerequisites; no model calls"),
                                 description=description)
        command.add_argument("--config", required=True, metavar="YAML", help="run YAML, relative to the current directory")
        command.add_argument("--config-dir", metavar="DIR", help=CONFIG_DIR_HELP)
        output_flags(command)
        command.set_defaults(func=_cmd_resolve)
    tasks = sub.add_parser("tasks", help="discover task IDs without containers or model calls",
                           description="List eligible tasks and mark the current subset/limit selection. "
                           "Uses local/cached data by default; --download permits fetching metadata/data. "
                           "No images, task exports, containers or model requests are needed.")
    tasks.add_argument("--config", required=True, metavar="YAML", help="run YAML with benchmark and registry settings")
    tasks.add_argument("--config-dir", metavar="DIR", help=CONFIG_DIR_HELP)
    tasks.add_argument("--download", action="store_true", help="allow fetching remote task listings/data and cache the IDs")
    output_flags(tasks)
    tasks.set_defaults(func=_cmd_tasks)
    listing = sub.add_parser("list", help="show built-in and configured components as JSON",
                             description="List component definitions from built-in catalogs and selected registries. "
                             "This is configuration discovery; availability and compatibility are checked by resolve/prepare.")
    listing.add_argument("category", choices=("harnesses", "models", "skills", "mcp", "extensions", "benchmarks"),
                         help="component category; models require a project registry")
    listing.add_argument("--config-dir", metavar="DIR", help=CONFIG_DIR_HELP)
    listing.set_defaults(func=_cmd_list)
    init = sub.add_parser("init", help="create a named starter; keep existing files",
                          description="Create editable run and registry files. quickstart includes a one-task setup check; "
                          "rh-swe-bench selects a remote benchmark and local model server. Existing files are kept.")
    init.add_argument("directory", nargs="?", default=".", help="destination directory (default: current directory)")
    init.add_argument("--template", choices=("quickstart", "rh-swe-bench"), default="rh-swe-bench",
                      help="starter template (default: rh-swe-bench for existing scripts; new users can choose quickstart)")
    init.add_argument("--environment", choices=("podman", "docker"), default="podman",
                      help="container engine in new machine.yaml (default: podman)")
    init.set_defaults(func=_cmd_init)

    rp = sub.add_parser("report", help="show saved results or export HTML/ZIP",
                        description="Read saved reports without rerunning agents or grading. "
                        "Multiple inputs compare each subsequent run against the first.")
    rp.add_argument("run_dirs", nargs="+", metavar="RUN_DIR",
                    help="actual run or harness directories printed by run; use a harness directory for cross-harness comparisons")
    rp.add_argument("--html", nargs="?", const="", default=None,
                     help="also write an HTML visualization (default: <first_run_dir>/report.html)")
    rp.add_argument("--bundle", metavar="ZIP", help="write a portable ZIP with offline charts and saved evidence")
    output_flags(rp)
    rp.set_defaults(func=_cmd_report)

    c = sub.add_parser("compare", help="compare a baseline with one or more candidates",
                       description="Show grades, measurement coverage and comparable token changes. "
                       "Use individual harness directories to compare different harnesses.")
    c.add_argument("baseline", metavar="BASELINE_RUN", help="baseline run or harness directory")
    c.add_argument("candidates", metavar="CANDIDATE_RUN", nargs="+", help="one or more candidate run or harness directories")
    c.add_argument("--html", nargs="?", const="", default=None,
                   help="write an interactive HTML comparison (default: <baseline>/comparison.html)")
    c.add_argument("--bundle", metavar="ZIP", help="write a portable comparison ZIP with offline charts and saved evidence")
    output_flags(c)
    c.set_defaults(func=_cmd_compare)

    cl = sub.add_parser("clean", help="delete output directory contents, keeping .cache and .gitkeep",
                        description="Delete every file and directory in the chosen output directory except .cache and .gitkeep. "
                        "Inspect --dry-run first. Interactive confirmation is required unless --yes is supplied.")
    clean_target = cl.add_mutually_exclusive_group()
    clean_target.add_argument("--config", help="use the run YAML's output_dir, relative to the current directory")
    clean_target.add_argument("--output-dir", help="runs directory relative to the current directory (default: runs)")
    cl.add_argument("--dry-run", action="store_true", help="list what would be deleted without deleting anything")
    cl.add_argument("--yes", "-y", action="store_true", help="skip confirmation prompt")
    cl.set_defaults(func=_cmd_clean)

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        ap.print_help()
        return
    args = ap.parse_args(argv)
    if args.cmd == "run":
        selections = ["--" + key.replace("_", "-") for key in RUN_FLAGS if getattr(args, key) is not None]
        if args.config and selections:
            r.error(f"--config cannot be combined with {' '.join(selections)}; edit those settings in the YAML")
        if not args.config and (selections or args.control):
            missing = ["--" + key.replace("_", "-") for key in ("benchmark", "harness", "model", "run_id")
                       if not getattr(args, key)]
            if missing:
                r.error(f"explicit runs require {' '.join(missing)}; use --config YAML or bare 'acb run' for interactive setup")
        for key in ("limit", "max_workers"):
            value = getattr(args, key)
            if value is not None and value < 1:
                r.error(f"--{key.replace('_', '-')} must be a positive integer")
    try:
        args.func(args)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError, subprocess.SubprocessError) as error:
        if getattr(args, "verbose", False):
            raise
        ap.exit(1, f"acb {args.cmd}: error: {error}\n")
    except KeyboardInterrupt:
        ap.exit(130, f"acb {args.cmd}: interrupted\n")


if __name__ == "__main__":
    main()
