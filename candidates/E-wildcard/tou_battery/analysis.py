"""End-to-end analysis: python3 -m tou_battery.analysis [--seeds N] [--out results]

Produces results/results.json and results/summary.md (all numbers quoted in REPORT.md).
"""
import argparse, json, os, time
import numpy as np
from .battery import Battery
from .tariff import TARIFFS, CA_TOU_ILLUSTRATIVE, CHEAP_NIGHTS_ILLUSTRATIVE
from .profiles import synthetic_load, synthetic_pv
from . import dispatch as D
from .mpc import run_mpc
from . import economics as E
from .mv import verify, recommend

# ---- labeled economic ASSUMPTIONS ----
ASSUMPTIONS = {
    "discount_rate": 0.05,
    "battery_life_years": 12,
    "capacity_fade_per_year": 0.02,
    "installed_battery_cost_usd": 12000.0,   # ~13.5 kWh installed, order of magnitude of 2024-25 US quotes; verify locally
    "federal_tax_credit": 0.0,               # residential 25D credit understood to end for 2026 expenditures; verify
    "controller_hardware_usd": 80.0,         # single-board computer + SD card + case
    "controller_running_cost_usd_per_year": 5.0,   # ~5 W * 8760 h * $0.12-0.40/kWh (rounded)
    "operator_setup_hours": 6.0,
}


def run_case(load, pv, imp, exp, b, mpc=True):
    out = {
        "no_battery": D.no_battery(load, pv, imp, exp, b),
        "default_self_consumption": D.simulate(load, pv, imp, exp, b, D.self_consumption_controller(load, pv, b)),
        "vendor_tou_rule": D.simulate(load, pv, imp, exp, b, D.tou_rule_controller(load, pv, imp, b)),
        "vendor_tou_grid_charge": D.simulate(load, pv, imp, exp, b, D.tou_rule_controller(load, pv, imp, b, grid_charge=True)),
        "perfect_foresight": D.perfect_foresight(load, pv, imp, exp, b),
    }
    if mpc:
        out["optimizer_mpc"] = run_mpc(load, pv, imp, exp, b)
    return out


def scale_spread(imp, k):
    T = len(imp)
    dmin = np.repeat(imp.reshape(-1, 24).min(axis=1), 24)[:T]
    return dmin + k * (imp - dmin)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    b = Battery()
    res = {"assumptions": ASSUMPTIONS, "battery": b.__dict__, "cases": {}, "sensitivity": {}, "economics": {}}

    # ---------- main grid: households x solar config x tariff ----------
    for pv_kw in (0.0, 6.0):
        for tname, tf in TARIFFS.items():
            imp, exp = tf.prices()
            rows = []
            for seed in range(a.seeds):
                L, V = synthetic_load(seed), synthetic_pv(seed, kw_dc=pv_kw)
                r = run_case(L, V, imp, exp, b)
                tc = {k: v["total_cost"] for k, v in r.items()}
                rows.append({
                    "seed": seed,
                    "total_cost": tc,
                    "cycles_mpc": r["optimizer_mpc"]["cycles"],
                    "mpc_vs_default": tc["default_self_consumption"] - tc["optimizer_mpc"],
                    "mpc_vs_vendor_tou": tc["vendor_tou_rule"] - tc["optimizer_mpc"],
                    "mpc_vs_vendor_tou_grid": tc["vendor_tou_grid_charge"] - tc["optimizer_mpc"],
                    "pf_vs_vendor_tou_grid": tc["vendor_tou_grid_charge"] - tc["perfect_foresight"],
                    "pf_vs_default": tc["default_self_consumption"] - tc["perfect_foresight"],
                    "mpc_vs_no_battery": tc["no_battery"] - tc["optimizer_mpc"],
                    "pf_vs_no_battery": tc["no_battery"] - tc["perfect_foresight"],
                })
            key = f"pv{int(pv_kw)}kw|{tname}"
            agg = {m: {"mean": float(np.mean([r[m] for r in rows])), "min": float(np.min([r[m] for r in rows])),
                       "max": float(np.max([r[m] for r in rows]))}
                   for m in ("mpc_vs_default", "mpc_vs_vendor_tou", "mpc_vs_vendor_tou_grid", "pf_vs_vendor_tou_grid", "pf_vs_default", "mpc_vs_no_battery", "pf_vs_no_battery", "cycles_mpc")}
            pfv = agg["pf_vs_no_battery"]["mean"]
            agg["mpc_capture_of_perfect_foresight_vs_no_battery"] = (agg["mpc_vs_no_battery"]["mean"] / pfv) if pfv > 1e-6 else None
            res["cases"][key] = {"per_household": rows, "summary": agg}
            print(f"[{time.time()-t0:5.0f}s] {key}: mpc_vs_default={agg['mpc_vs_default']['mean']:.0f} "
                  f"mpc_vs_no_batt={agg['mpc_vs_no_battery']['mean']:.0f} pf_vs_no_batt={pfv:.0f}", flush=True)

    # ---------- M&V demo on one realised MPC run ----------
    L, V = synthetic_load(0), synthetic_pv(0, kw_dc=0.0)
    imp, exp = CHEAP_NIGHTS_ILLUSTRATIVE.prices()
    m = run_mpc(L, V, imp, exp, b)
    mv = verify(L, V, m["grid_import"], m["grid_export"], m["ac_in"], m["ac_out"], imp, exp, b)
    mv["recommendation"] = recommend(mv["savings_vs_default_mode_net_of_degradation"])
    res["mv_demo_pv0_cheap_nights_seed0"] = mv

    # ---------- sensitivity (perfect-foresight value vs no battery, seed 0; x measured MPC capture) ----------
    sens_cases = [("pv0|CA_TOU", 0.0, CA_TOU_ILLUSTRATIVE), ("pv0|cheap_nights", 0.0, CHEAP_NIGHTS_ILLUSTRATIVE),
                  ("pv6|CA_TOU", 6.0, CA_TOU_ILLUSTRATIVE)]
    for label, pv_kw, tf in sens_cases:
        L, V = synthetic_load(0), synthetic_pv(0, kw_dc=pv_kw)
        imp0, exp0 = tf.prices()
        def val(bb=b, imp=imp0, exp=exp0):
            exp = np.minimum(exp, imp)
            return D.no_battery(L, V, imp, exp, bb)["total_cost"] - D.perfect_foresight(L, V, imp, exp, bb)["total_cost"]
        s = {}
        s["spread_scale"] = {k: val(imp=scale_spread(imp0, k)) for k in (0.0, 0.5, 1.0, 1.5, 2.0)}
        s["round_trip_eff"] = {rt: val(bb=b.replace(eta_charge=rt ** 0.5, eta_discharge=rt ** 0.5)) for rt in (0.80, 0.85, 0.90, 0.95)}
        s["deg_cost_per_kwh"] = {d: val(bb=b.replace(deg_cost_per_kwh=d)) for d in (0.0, 0.03, 0.06, 0.10)}
        s["capacity_kwh"] = {c: val(bb=b.replace(capacity_kwh=c, power_kw=min(5.0, c / 2))) for c in (5.0, 10.0, 13.5, 20.0)}
        s["export_price_x"] = {k: val(exp=exp0 * k) for k in (0.0, 1.0, 3.0)}
        res["sensitivity"][label] = {kk: {str(k): float(v) for k, v in vv.items()} for kk, vv in s.items()}
        print(f"[{time.time()-t0:5.0f}s] sensitivity {label} done", flush=True)

    # forecast-quality sensitivity (MPC) on the case where dispatch matters most
    L, V = synthetic_load(0), synthetic_pv(0, kw_dc=0.0)
    imp, exp = CHEAP_NIGHTS_ILLUSTRATIVE.prices()
    nb = D.no_battery(L, V, imp, exp, b)["total_cost"]
    res["sensitivity"]["forecast_pv0|cheap_nights"] = {
        "lookback_1d": nb - run_mpc(L, V, imp, exp, b, lookback=1)["total_cost"],
        "lookback_7d": nb - run_mpc(L, V, imp, exp, b, lookback=7)["total_cost"],
        "oracle_forecast": nb - run_mpc(L, V, imp, exp, b, oracle=True)["total_cost"],
        "perfect_foresight_full_year": nb - D.perfect_foresight(L, V, imp, exp, b)["total_cost"],
    }

    # ---------- economics ----------
    A = ASSUMPTIONS
    ec = {}
    for key, c in res["cases"].items():
        sm = c["summary"]
        inc = sm["mpc_vs_default"]["mean"]
        full = sm["mpc_vs_no_battery"]["mean"]
        cap = A["installed_battery_cost_usd"] * (1 - A["federal_tax_credit"])
        ec[key] = {
            "existing_owner_incremental_usd_per_year": inc,
            "existing_owner_net_npv": E.npv(inc, 10, A["discount_rate"], A["capacity_fade_per_year"],
                                            capex=A["controller_hardware_usd"], annual_cost=A["controller_running_cost_usd_per_year"]),
            "existing_owner_payback_years": E.simple_payback_years(A["controller_hardware_usd"], inc, A["controller_running_cost_usd_per_year"]),
            "new_battery_value_usd_per_year": full,
            "new_battery_break_even_capex": E.break_even_capex(full, A["battery_life_years"], A["discount_rate"], A["capacity_fade_per_year"]),
            "new_battery_npv_at_assumed_cost": E.npv(full, A["battery_life_years"], A["discount_rate"], A["capacity_fade_per_year"], capex=cap),
        }
    res["economics"] = ec
    res["analytic_thresholds"] = {
        "CA_summer_offpeak_0.38_min_peak_for_grid_arbitrage": E.arbitrage_threshold_price(0.38, b),
        "cheap_nights_0.08_min_later_price": E.arbitrage_threshold_price(0.08, b),
        "solar_export_0.05_min_later_price": E.solar_shift_threshold_price(0.05, b),
        "daily_upper_bound_cheap_nights_0.08_to_0.24": E.daily_arbitrage_upper_bound(0.08, 0.24, b),
    }
    res["runtime_seconds"] = time.time() - t0

    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, float)):
            return None if not np.isfinite(o) else round(float(o), 4)
        return o
    with open(os.path.join(a.out, "results.json"), "w") as f:
        json.dump(clean(res), f, indent=1)
    write_summary(res, os.path.join(a.out, "summary.md"))
    print(f"done in {time.time()-t0:.0f}s -> {a.out}/results.json, {a.out}/summary.md")


def write_summary(res, path):
    L = ["# Results summary (auto-generated by `python3 -m tou_battery.analysis`)", "",
         "All $ are per household per year, net of battery degradation cost; mean [min..max] over synthetic households.", "",
         "| case | optimizer vs factory default | optimizer vs vendor TOU (solar only) | optimizer vs vendor TOU + grid charge | perfect-foresight vs default | optimizer vs no battery | MPC capture | cycles/yr |",
         "|---|---|---|---|---|---|---|---|"]
    f = lambda d: f"{d['mean']:.0f} [{d['min']:.0f}..{d['max']:.0f}]"
    for k, c in res["cases"].items():
        s = c["summary"]
        cap = s["mpc_capture_of_perfect_foresight_vs_no_battery"]
        L.append(f"| {k} | {f(s['mpc_vs_default'])} | {f(s['mpc_vs_vendor_tou'])} | {f(s['mpc_vs_vendor_tou_grid'])} | {f(s['pf_vs_default'])} | "
                 f"{f(s['mpc_vs_no_battery'])} | {'n/a' if cap is None else f'{cap:.0%}'} | {s['cycles_mpc']['mean']:.0f} |")
    L += ["", "## Economics", "", "| case | existing owner: +$/yr | 10-yr NPV of $80 controller | new battery: $/yr | break-even installed cost | NPV at assumed $12k |", "|---|---|---|---|---|---|"]
    for k, e in res["economics"].items():
        L.append(f"| {k} | {e['existing_owner_incremental_usd_per_year']:.0f} | {e['existing_owner_net_npv']:.0f} | "
                 f"{e['new_battery_value_usd_per_year']:.0f} | {e['new_battery_break_even_capex']:.0f} | {e['new_battery_npv_at_assumed_cost']:.0f} |")
    L += ["", "## Sensitivity (perfect-foresight value vs no battery, $/yr, household seed 0)", ""]
    for k, s in res["sensitivity"].items():
        L.append(f"**{k}**")
        L.append("")
        for kk, vv in s.items():
            if isinstance(vv, dict):
                L.append(f"- {kk}: " + ", ".join(f"{a}->{b:.0f}" for a, b in vv.items()))
            else:
                L.append(f"- {kk}: {vv:.0f}")
        L.append("")
    L += ["## M&V demo (no solar, cheap-nights tariff, household 0)", ""]
    L += [f"- {k}: {v if not isinstance(v, float) else round(v, 2)}" for k, v in res["mv_demo_pv0_cheap_nights_seed0"].items()]
    L += ["", "## Analytic thresholds ($/kWh)", ""] + [f"- {k}: {v:.3f}" for k, v in res["analytic_thresholds"].items()]
    open(path, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
