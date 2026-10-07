"""Measurement & verification (M&V): turn METERED data into a verifiable savings statement.

Inputs are hourly measurements a battery gateway already records (home load, PV output,
battery AC in/out, grid import/export). Counterfactuals (no battery; factory-default
self-consumption) are recomputed on the SAME measured load/PV, so the savings figure
depends only on the tariff and measured data - not on any forecast or model.
"""
import numpy as np
from .battery import Battery
from . import dispatch as D


def verify(load, pv, grid_import, grid_export, ac_in, ac_out, imp, exp, b: Battery, tol=1e-6):
    load, pv, gi, ge, ai, ao = (np.asarray(a, float) for a in (load, pv, grid_import, grid_export, ac_in, ac_out))
    residual = (load - pv + ai - ao) - (gi - ge)
    consistent = bool(np.max(np.abs(residual)) <= tol * max(1.0, float(np.max(load))))
    actual_bill = float(np.dot(imp, gi) - np.dot(exp, ge))
    actual_deg = float(b.deg_cost_per_kwh * (ao / b.eta_discharge).sum())
    nb = D.no_battery(load, pv, imp, exp, b)
    sc = D.simulate(load, pv, imp, exp, b, D.self_consumption_controller(load, pv, b))
    return {
        "meter_data_consistent": consistent,
        "max_balance_residual_kwh": float(np.max(np.abs(residual))),
        "actual_bill": actual_bill,
        "actual_bill_plus_degradation": actual_bill + actual_deg,
        "counterfactual_no_battery_bill": nb["bill"],
        "counterfactual_self_consumption_total": sc["total_cost"],
        "savings_vs_no_battery_net_of_degradation": nb["bill"] - (actual_bill + actual_deg),
        "savings_vs_default_mode_net_of_degradation": sc["total_cost"] - (actual_bill + actual_deg),
    }


def recommend(savings_vs_default: float, fee: float = 0.0) -> str:
    """Anti-dark-pattern rule: tell the user to stop/revert when the tool is not paying for itself."""
    if savings_vs_default - fee > 0:
        return "KEEP: optimizer beats factory default net of degradation and fees"
    return "REVERT: optimizer is not beating the factory default; switch back (or cancel)"


def best_plan(load, pv, b: Battery, tariffs, controllers=("no_battery", "self_consumption", "optimizer")):
    """Exact bill for each (tariff, controller) on historical data; returns rows sorted by total cost.
    'optimizer' here = perfect-foresight upper bound; use mpc for realistic numbers."""
    rows = []
    for t in tariffs:
        imp, exp = t.prices(len(load))
        for c in controllers:
            if c == "no_battery":
                r = D.no_battery(load, pv, imp, exp, b)
            elif c == "self_consumption":
                r = D.simulate(load, pv, imp, exp, b, D.self_consumption_controller(load, pv, b))
            else:
                r = D.perfect_foresight(load, pv, imp, exp, b)
            rows.append({"tariff": t.name, "controller": c, "total_cost": r["total_cost"], "bill": r["bill"]})
    return sorted(rows, key=lambda r: r["total_cost"])
