"""Operator CLI: which battery configuration (and tariff) is cheapest for THIS household?

  python3 -m tou_battery.advise --csv my_hourly.csv --tariff CA_TOU_illustrative [--current self_consumption]
  python3 -m tou_battery.advise --demo            # synthetic household, no data needed

CSV: one row per hour, header with columns `load_kwh` and `pv_kwh` (pv optional), 24*k rows.
Replace the illustrative tariff with your utility's real tariff in tariff.py before trusting $ figures.
"""
import argparse, csv, sys
import numpy as np
from .battery import Battery
from .tariff import TARIFFS
from .profiles import synthetic_load, synthetic_pv
from .mv import best_plan, CONTROLLERS


def read_csv(path):
    L, V = [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            L.append(float(row["load_kwh"]))
            V.append(float(row.get("pv_kwh") or 0.0))
    L, V = np.array(L), np.array(V)
    if len(L) % 24 or len(L) == 0 or len(L) > 8760:
        raise ValueError("need 24*k hourly rows (k>=1, <=365 days), starting Jan 1 00:00")
    if np.any(L < 0) or np.any(V < 0):
        raise ValueError("negative energy values")
    return L, V


def advise(L, V, b, tariff_names, current="self_consumption"):
    rows = best_plan(L, V, b, [TARIFFS[t] for t in tariff_names],
                     controllers=[c for c in CONTROLLERS if c != "optimizer_upper_bound"])
    cur = [r for r in rows if r["controller"] == current and r["tariff"] == tariff_names[0]][0]
    best = rows[0]
    return {"rows": rows, "current": cur, "best": best,
            "annualised_saving_vs_current": (cur["total_cost"] - best["total_cost"]) * 8760 / len(L)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv"); ap.add_argument("--demo", action="store_true")
    ap.add_argument("--tariff", action="append", help="first one = your current tariff", default=None)
    ap.add_argument("--current", default="self_consumption", choices=[c for c in CONTROLLERS if c != "optimizer_upper_bound"])
    ap.add_argument("--capacity", type=float, default=13.5); ap.add_argument("--power", type=float, default=5.0)
    ap.add_argument("--reserve", type=float, default=0.2)
    ap.add_argument("--no-grid-charge", action="store_true")
    a = ap.parse_args(argv)
    if a.csv:
        L, V = read_csv(a.csv)
    elif a.demo:
        L, V = synthetic_load(0), synthetic_pv(0, kw_dc=0.0)
    else:
        ap.error("--csv or --demo required")
    b = Battery(capacity_kwh=a.capacity, power_kw=a.power, reserve_frac=a.reserve, allow_grid_charge=not a.no_grid_charge)
    tariffs = a.tariff or ["CA_TOU_illustrative"]
    out = advise(L, V, b, tariffs, a.current)
    print(f"{'tariff':28s} {'configuration':24s} {'cost $ (bill+wear)':>18s}")
    for r in out["rows"]:
        print(f"{r['tariff']:28s} {r['controller']:24s} {r['total_cost']:18.2f}")
    print(f"\nCurrent: {out['current']['controller']} on {out['current']['tariff']}")
    print(f"Best   : {out['best']['controller']} on {out['best']['tariff']}")
    print(f"Estimated saving vs current: ${out['annualised_saving_vs_current']:.0f}/yr "
          "(historical replay; future depends on your usage, tariff changes and forecast error)")
    return out


if __name__ == "__main__":
    main()
