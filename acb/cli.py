"""acb command-line entrypoint.

    acb run   --config config/run.requests-1142.yaml
    acb run   --benchmark swebench --harness goose --model mlx-community/Qwen3.8-27B-4bit \
              --run-id demo --limit 1 --proxy praxis
    acb report runs/<run_id>            # re-aggregate metrics from usage.jsonl
    acb compare runs/<a> runs/<b> ...   # side-by-side rollups
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
    run(cfg)


def _cmd_report(args):
    from acb.report import build_report
    run_dir = Path(args.run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    print(json.dumps(report, indent=2))


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
    r.set_defaults(func=_cmd_run)

    rp = sub.add_parser("report", help="show a run's report")
    rp.add_argument("run_dir")
    rp.set_defaults(func=_cmd_report)

    c = sub.add_parser("compare", help="compare multiple runs")
    c.add_argument("run_dirs", nargs="+")
    c.set_defaults(func=_cmd_compare)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
