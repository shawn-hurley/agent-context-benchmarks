"""acb command-line entrypoint.

    acb run   # interactive run configuration
    acb run   --config config/run.requests-1142.yaml
    acb run   --benchmark swebench --harness goose --model mlx-community/Qwen3.8-27B-4bit \
              --run-id demo --limit 1 --proxy praxis
    acb report runs/<run_id>                     # re-aggregate metrics from usage.jsonl
    acb report runs/<run_id> --html              # also write runs/<run_id>/report.html
    acb report runs/<a> runs/<b> --html          # combined multi-run HTML comparison
    acb compare runs/<a> runs/<b> ...            # side-by-side rollups (text table)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from acb.config import RunConfig, Registries


def _cmd_run(args):
    if args.config:
        cfg = RunConfig.from_file(args.config)
    else:
        direct = any(getattr(args, key) is not None for key in (
            "benchmark", "harness", "model", "run_id", "limit", "max_workers", "proxy", "control"))
        if not direct:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                raise SystemExit("bare acb run requires an interactive terminal; use --config or explicit run flags")
            from acb.interactive_run import configure
            selected = configure(args.config_dir)
            if selected is None:
                return
            from acb.harbor.backend import run_plan
            run_plan(selected, verbose=args.verbose)
            return
        if not (args.benchmark and args.harness and args.model and args.run_id):
            raise SystemExit("need --config OR (--benchmark --harness --model --run-id)")
        cfg = RunConfig(
            run_id=args.run_id, benchmark=args.benchmark, harness=args.harness,
            model=args.model, proxy=args.proxy or "praxis", limit=args.limit,
            max_workers=args.max_workers if args.max_workers is not None else 4,
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
        from acb.preparation import prepare
        document = prepare(plan)
    else:
        document = plan.to_dict()
    print(json.dumps(document, indent=2))


def _cmd_list(args):
    from acb.resolver import defaults
    registries = Registries.load(Path(args.config_dir) if args.config_dir else None)
    category = "mcp_servers" if args.category == "mcp" else args.category
    entries = {**defaults().get(category, {}), **getattr(registries, category)}
    if category == "models":
        entries = {**registries.proxy.get("models", {}), **entries}
    print(json.dumps(entries, indent=2))


def _cmd_init(args):
    import yaml
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    templates = {
        "run.yaml": {"schema_version": 2, "run_id": "rh-swe-bench-smoke", "benchmark": "rh-swe-bench",
                     "harness": ["goose", "pi", "opencode", "claude-code"], "model": "local-model",
                     "skills": [], "extensions": [], "subset": ["task-0000"], "max_workers": 1},
        "config/models.yaml": {"local-model": {"model": "YOUR_MODEL_ID", "api": "openai", "endpoint": "host.containers.internal:8000", "tls": False}},
        "config/machine.yaml": {"environment": "podman"},
    }
    for relative, data in templates.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x") as file:
                yaml.safe_dump(data, file, sort_keys=False)
            print(f"created {path}")
        except FileExistsError:
            print(f"kept existing {path}")


def _cmd_report(args):
    run_dirs = [Path(d) for d in args.run_dirs]

    reports = []
    for run_dir in run_dirs:
        p = run_dir / "report.json"
        if p.exists():
            reports.append(json.loads(p.read_text()))

    if len(run_dirs) > 1:
        from acb.comparison import compare
        print(json.dumps([compare(run_dirs[0], root) for root in run_dirs[1:]], indent=2))
    elif len(reports) == 1:
        print(json.dumps(reports[0], indent=2))
    else:
        print(json.dumps(reports, indent=2))

    if args.html is not None:
        if args.html:
            out_path = Path(args.html)
        else:
            out_path = run_dirs[0] / "report.html"
        from acb.comparison_html import write_reports
        write_reports(run_dirs, out_path)
        print(f"html report: {out_path}")


def _cmd_clean(args):
    import shutil
    output_dir = (RunConfig.from_file(args.config).output_path() if args.config
                  else Path(args.output_dir or "runs").expanduser().resolve())
    if not output_dir.exists():
        raise SystemExit(f"output dir does not exist: {output_dir}")

    to_delete = [
        p for p in output_dir.iterdir()
        if p.name not in {".cache", ".gitkeep"}
    ]

    if not to_delete:
        print("No runs to delete.")
        return

    print(f"Will delete {len(to_delete)} run(s) in {output_dir}:")
    for p in sorted(to_delete):
        print(f"  {p.name}")

    if args.dry_run:
        return

    if not args.yes:
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    for p in to_delete:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()

    print(f"Deleted {len(to_delete)} run(s).")


def _cmd_compare(args):
    from acb.comparison import compare
    roots = [Path(d) for d in args.run_dirs]
    if len(roots) < 2:
        raise SystemExit("compare requires a baseline and at least one candidate")
    payloads = [compare(roots[0], candidate) for candidate in roots[1:]]
    print(json.dumps(payloads, indent=2))
    if args.html is not None:
        from acb.comparison_html import write_reports
        out_path = Path(args.html) if args.html else roots[0] / "comparison.html"
        write_reports(roots, out_path)
        print(f"html comparison: {out_path}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="acb")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a benchmark x harness x model")
    r.add_argument("--config")
    r.add_argument("--config-dir")
    r.add_argument("--control", choices=("nop", "oracle"), help="run a Harbor grading control without model calls")
    r.add_argument("--benchmark")
    r.add_argument("--harness")
    r.add_argument("--model")
    r.add_argument("--run-id")
    r.add_argument("--proxy")
    r.add_argument("--limit", type=int)
    r.add_argument("--max-workers", type=int)
    r.add_argument("--verbose", "-v", action="store_true",
                   help="show stderr output live during run (disables stderr redirection)")
    r.set_defaults(func=_cmd_run)

    for name in ("resolve", "prepare"):
        command = sub.add_parser(name, help=f"{name} the configured experiment")
        command.add_argument("--config", required=True)
        command.add_argument("--config-dir")
        command.set_defaults(func=_cmd_resolve)
    listing = sub.add_parser("list", help="show configured component identities")
    listing.add_argument("category", choices=("harnesses", "models", "skills", "mcp", "extensions", "benchmarks"))
    listing.add_argument("--config-dir")
    listing.set_defaults(func=_cmd_list)
    init = sub.add_parser("init", help="create starter configuration without overwriting files")
    init.add_argument("directory", nargs="?", default=".")
    init.set_defaults(func=_cmd_init)

    rp = sub.add_parser("report", help="show a run's report (one or more runs)")
    rp.add_argument("run_dirs", nargs="+",
                    help="one or more run directories; multiple dirs produce a combined report")
    rp.add_argument("--html", nargs="?", const="", default=None,
                     help="also write an HTML visualization (default: <first_run_dir>/report.html)")
    rp.set_defaults(func=_cmd_report)

    c = sub.add_parser("compare", help="compare multiple runs")
    c.add_argument("run_dirs", nargs="+")
    c.add_argument("--html", nargs="?", const="", default=None,
                   help="write an interactive HTML comparison (default: <baseline>/comparison.html)")
    c.set_defaults(func=_cmd_compare)

    cl = sub.add_parser("clean", help="delete all run directories, keeping the cache")
    clean_target = cl.add_mutually_exclusive_group()
    clean_target.add_argument("--config", help="use the run YAML's output_dir, relative to the current directory")
    clean_target.add_argument("--output-dir", help="runs directory relative to the current directory (default: runs)")
    cl.add_argument("--dry-run", action="store_true", help="list what would be deleted without deleting anything")
    cl.add_argument("--yes", "-y", action="store_true", help="skip confirmation prompt")
    cl.set_defaults(func=_cmd_clean)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Try to show log file location even if run() failed
        import traceback
        import sys
        # If we've created a run directory, log file should be in runs/{run_id}/acb.log
        # For now just show the error - the user should check runs/ for the log
        traceback.print_exc(file=sys.stderr)
        print(f"\n[acb] Error occurred. Check runs/ directory for detailed logs in acb.log files.", 
              file=sys.stderr)
        sys.exit(1)
