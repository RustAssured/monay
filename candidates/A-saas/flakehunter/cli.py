"""Command line: flakehunter analyze reports/*.xml [--json] [--fail-on-new-flaky QUARANTINE]."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from .analyze import CONFIRMED, SUSPECTED, analyze, summarize, to_markdown
from .junit import parse_junit


def _expand(patterns):
    files = []
    for p in patterns:
        matches = sorted(glob.glob(p)) or ([p] if Path(p).exists() else [])
        files.extend(matches)
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flakehunter", description="Find flaky tests in JUnit XML history.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="analyse JUnit XML files (one file per CI run, oldest first)")
    a.add_argument("files", nargs="+")
    a.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    a.add_argument("--min-flip-rate", type=float, default=0.2)
    a.add_argument("--broken-streak", type=int, default=3)
    a.add_argument("--quarantine-out", help="write newline-separated flaky test ids here")
    a.add_argument("--fail-on-new-flaky", metavar="KNOWN",
                   help="exit 1 if a flaky test is found that is not listed in KNOWN file")
    args = ap.parse_args(argv)

    files = _expand(args.files)
    if not files:
        print("no input files found", file=sys.stderr)
        return 2
    results = []
    for f in files:
        results.extend(parse_junit(Path(f)))
    stats = analyze(results, args.min_flip_rate, args.broken_streak)
    summ = summarize(stats)
    if args.json:
        print(json.dumps({"summary": summ, "tests": [s.to_dict() for s in stats.values()]}, indent=2))
    else:
        print(to_markdown(stats))
    if args.quarantine_out:
        Path(args.quarantine_out).write_text("\n".join(summ["quarantine"]) + ("\n" if summ["quarantine"] else ""))
    if args.fail_on_new_flaky:
        kp = Path(args.fail_on_new_flaky)
        known = set(kp.read_text().split()) if kp.exists() else set()
        new = [t for t in summ["quarantine"] if t not in known]
        if new:
            print("NEW flaky tests: " + ", ".join(new), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
