"""acb command-line entrypoint.

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
from pathlib import Path

from acb.config import RunConfig, Registries


def _cmd_run(args):
    if args.config:
        cfg = RunConfig.from_file(args.config)
    else:
        if not (args.benchmark and args.harness and args.model and args.run_id):
            raise SystemExit("need --config OR (--benchmark --harness --model --run-id)")
        cfg = RunConfig(
            run_id=args.run_id, benchmark=args.benchmark, harness=args.harness,
            model=args.model, proxy=args.proxy, limit=args.limit,
            max_workers=args.max_workers,
        )
    from acb.runner import run
    run(cfg, verbose=args.verbose)


def _cmd_report(args):
    run_dirs = [Path(d) for d in args.run_dirs]

    reports = []
    for run_dir in run_dirs:
        p = run_dir / "report.json"
        if p.exists():
            reports.append(json.loads(p.read_text()))

    if len(reports) == 1:
        print(json.dumps(reports[0], indent=2))
    else:
        print(json.dumps(reports, indent=2))

    if args.html is not None:
        from acb.html_report import build_html_report
        if args.html:
            out_path = Path(args.html)
        else:
            out_path = run_dirs[0] / "report.html"
        out_path.write_text(build_html_report(run_dirs))
        print(f"html report: {out_path}")


def _cmd_compare(args):
    rows = []
    for d in args.run_dirs:
        p = Path(d) / "report.json"
        if p.exists():
            rows.append(json.loads(p.read_text()))
    cols = ["harness", "model", "benchmark", "resolve_rate",
            "avg_total_tokens", "avg_peak_context", "avg_cache_efficiency",
            "tokens_per_resolved"]
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(r.get(c)) for c in cols))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="acb")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a benchmark x harness x model")
    r.add_argument("--config")
    r.add_argument("--benchmark")
    r.add_argument("--harness")
    r.add_argument("--model")
    r.add_argument("--run-id")
    r.add_argument("--proxy", default="praxis")
    r.add_argument("--limit", type=int)
    r.add_argument("--max-workers", type=int, default=4)
    r.add_argument("--verbose", "-v", action="store_true",
                   help="show stderr output live during run (disables stderr redirection)")
    r.set_defaults(func=_cmd_run)

    rp = sub.add_parser("report", help="show a run's report (one or more runs)")
    rp.add_argument("run_dirs", nargs="+",
                    help="one or more run directories; multiple dirs produce a combined report")
    rp.add_argument("--html", nargs="?", const="", default=None,
                     help="also write an HTML visualization (default: <first_run_dir>/report.html)")
    rp.set_defaults(func=_cmd_report)

    c = sub.add_parser("compare", help="compare multiple runs")
    c.add_argument("run_dirs", nargs="+")
    c.set_defaults(func=_cmd_compare)

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
