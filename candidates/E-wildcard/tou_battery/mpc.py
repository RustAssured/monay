"""Causal rolling-horizon controller: uses ONLY past metered data + the (known) tariff.

Forecast: load/pv for hour h = mean of the same hour over the previous `lookback` days.
Every `replan_every` hours an LP is solved over a `horizon`-hour window. The plan is NOT
executed blindly (forecasts are wrong); it is translated into a per-hour MODE that is
robust to forecast error:
  * discharge-mode  : plan discharges -> follow the ACTUAL deficit (load-following)
  * grid-charge-mode: plan charges beyond forecast solar surplus -> charge up to planned SoC
  * solar-store-mode: plan stores solar -> store the ACTUAL surplus
  * hold-mode       : otherwise do not discharge; store unforecast surplus only if worth it
"""
import numpy as np
from .battery import Battery
from .dispatch import optimize, simulate


def _forecast(series, t, H, lookback):
    out = np.empty(H)
    for k in range(H):
        h = t + k
        past = [series[h - 24 * j] for j in range(1, lookback + 1) if 0 <= h - 24 * j < t]
        if not past:  # cold start
            past = [series[i] for i in range(t) if i % 24 == h % 24] or ([float(np.mean(series[:t]))] if t > 0 else [0.0])
        out[k] = np.mean(past)
    return out


def mpc_controller(load, pv, imp, exp, b: Battery, horizon=48, replan_every=6, lookback=7, oracle=False):
    """oracle=True uses true future load/pv instead of forecasts (diagnostics/tests only)."""
    T = len(load)
    st = {"t0": None}

    def ctl(t, soc):
        if st["t0"] is None or t - st["t0"] >= replan_every:
            H = min(horizon, T - t)
            if oracle:
                lf, pf = np.asarray(load[t:t + H]), np.asarray(pv[t:t + H])
            else:
                lf, pf = _forecast(load, t, H, lookback), _forecast(pv, t, H, lookback)
            path, _, fl = optimize(lf, pf, imp[t:t + H], exp[t:t + H], b, soc0=soc, return_flows=True)
            st.update(t0=t, path=path, fl=fl, surplus_fc=np.maximum(pf - lf, 0.0))
        k = t - st["t0"]
        surplus = pv[t] - load[t]
        solar_charge = surplus * b.eta_charge if surplus > 0 else 0.0
        if st["fl"]["x"][k] > 1e-4:                                  # discharge-mode
            return solar_charge if surplus > 0 else surplus / b.eta_discharge
        if st["fl"]["c"][k] > st["surplus_fc"][k] + 1e-4:            # grid-charge-mode
            return max(st["path"][k + 1] - soc, solar_charge)
        if st["fl"]["c"][k] > 1e-4:                                  # solar-store-mode
            return solar_charge
        # hold-mode: plan says idle; still store an UNFORECAST solar surplus if that is worth
        # more than exporting it (myopic check against the next 24h of import prices)
        worth = imp[t:t + 24].max() * b.eta_charge * b.eta_discharge - b.deg_cost_per_kwh
        return solar_charge if exp[t] < worth else 0.0

    return ctl


def run_mpc(load, pv, imp, exp, b: Battery, **kw):
    return simulate(load, pv, imp, exp, b, mpc_controller(load, pv, imp, exp, b, **kw), soc0=b.soc_min)
