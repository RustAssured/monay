"""CLI.  python -m spendaudit {audit,demo,evaluate,economics} ..."""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile

from .audit import run_audit
from .normalize import merchant_family
from .parsers import load_file, merge_statements, parse_date
from .report import to_json, to_markdown


def load_usage(path):
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out[merchant_family(row["merchant"])] = parse_date(row["last_used"])
    return out


def cmd_audit(args):
    stmts = [load_file(p) for p in args.files]
    txs = merge_statements(stmts)
    if not txs:
        print("no transactions parsed", file=sys.stderr)
        return 2
    balances = {}
    for s in stmts:
        balances.update(s.balances)
    res = run_audit(txs, usage=load_usage(args.usage) if args.usage else None, balances=balances,
                    target_apy=args.target_apy, current_apy=args.current_apy, buffer_months=args.buffer_months)
    text = to_json(res) if args.json else to_markdown(res)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote {args.out}: identified ${res.identified_annual:,.2f}/yr, expected ${res.expected_annual:,.2f}")
    else:
        print(text)
    return 0


def cmd_demo(args):
    """End-to-end on a synthetic household: export to real bank formats, re-parse from disk, audit."""
    from .synth import generate_household, to_chase_checking_csv, to_chase_credit_csv
    hh = generate_household(args.seed)
    d = args.dir or tempfile.mkdtemp(prefix="spendaudit_demo_")
    os.makedirs(d, exist_ok=True)
    chk = [t for t in hh.transactions if t.account == "checking"]
    card = [t for t in hh.transactions if t.account == "card"]
    paths = [os.path.join(d, "checking.csv"), os.path.join(d, "card.csv")]
    with open(paths[0], "w") as f:
        f.write(to_chase_checking_csv(chk))
    with open(paths[1], "w") as f:
        f.write(to_chase_credit_csv(card))
    print(f"synthetic household {args.seed} ({hh.truth['profile']}) exported to {d}", file=sys.stderr)
    return cmd_audit(argparse.Namespace(files=paths, usage=None, json=args.json, out=args.out, target_apy=0.035,
                                        current_apy=0.0001, buffer_months=1.5))


def cmd_evaluate(args):
    from .evaluate import evaluate, format_table
    from .evaluate import forecast_backtest
    m, t = evaluate(args.n, args.seed)
    print(format_table(m, t))
    bt = forecast_backtest(min(args.n, 100), args.seed)
    print(f"\nForecast backtest (fit < 2026-01, predict Jan-Jun 2026, {bt['months_compared']} household-months): "
          f"outflow WAPE {bt['outflow_wape']:.1%}, mean |net error| ${bt['mean_abs_net_error']:,.0f} "
          f"vs mean monthly outflow ${bt['mean_monthly_outflow']:,.0f}.")
    return 0


def cmd_economics(args):
    from .economics import format_economics
    print(format_economics(args.household_savings))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="spendaudit", description="Local-first spending auditor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="audit bank/card exports (CSV, OFX, QFX)")
    a.add_argument("files", nargs="+")
    a.add_argument("--usage", help="CSV merchant,last_used to flag unused services")
    a.add_argument("--json", action="store_true")
    a.add_argument("--out")
    a.add_argument("--target-apy", type=float, default=0.035, help="APY you could get on savings (check current)")
    a.add_argument("--current-apy", type=float, default=0.0001)
    a.add_argument("--buffer-months", type=float, default=1.5)
    a.set_defaults(fn=cmd_audit)
    d = sub.add_parser("demo", help="run on a synthetic household")
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--dir")
    d.add_argument("--json", action="store_true")
    d.add_argument("--out")
    d.set_defaults(fn=cmd_demo)
    e = sub.add_parser("evaluate", help="precision/recall on labelled synthetic data")
    e.add_argument("--n", type=int, default=200)
    e.add_argument("--seed", type=int, default=1000)
    e.set_defaults(fn=cmd_evaluate)
    c = sub.add_parser("economics", help="household and product economics with sensitivity")
    c.add_argument("--household-savings", type=float)
    c.set_defaults(fn=cmd_economics)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
