"""Exact (up to SoC discretisation) dynamic-programming dispatch + baselines + simulator.

Decision each hour: next state of charge (DC kWh). Hourly cost
    import_price * max(net,0) - export_price * max(-net,0) + deg_cost * dc_discharged
with net = load - pv + ac_charge - ac_discharge (kWh, 1-hour steps).
Constraints: SoC in [reserve, capacity], |AC power| <= power_kw, optional
no-grid-charging and no-battery-export rules (many interconnection agreements / tax
credit rules restrict these; defaults are conservative).
"""
import numpy as np
import scipy.sparse as sp
from scipy.optimize import linprog
from .battery import Battery

TOL = 1e-9


def soc_grid(b: Battery, n: int = 44) -> np.ndarray:
    return np.linspace(b.soc_min, b.soc_max, n)


def _step_cost_matrix(grid, L, V, pi, pe, b: Battery):
    """Cost[s, s'] for one hour; +inf where infeasible."""
    d = grid[None, :] - grid[:, None]                  # DC energy change
    ac_in = np.where(d > 0, d / b.eta_charge, 0.0)
    dc_out = np.where(d < 0, -d, 0.0)
    ac_out = dc_out * b.eta_discharge
    feas = (ac_in <= b.power_kw + TOL) & (ac_out <= b.power_kw + TOL)
    if not b.allow_grid_charge:
        feas &= ac_in <= max(V - L, 0.0) + TOL
    if not b.allow_battery_export:
        feas &= ac_out <= max(L - V, 0.0) + TOL
    net = L - V + ac_in - ac_out
    cost = pi * np.maximum(net, 0.0) - pe * np.maximum(-net, 0.0) + b.deg_cost_per_kwh * dc_out
    return np.where(feas, cost, np.inf)


def optimize_dp(load, pv, imp, exp, b: Battery, soc0: float = None, n_states: int = 44,
                terminal_value_per_kwh: float = 0.0):
    """Reference backward DP on a discrete SoC grid (used to cross-check the LP). Returns (planned SoC path len T+1, optimal cost from soc0 on the grid).

    The 'stay' action (s'=s) is always feasible, so a finite solution always exists.
    """
    T = len(load)
    grid = soc_grid(b, n_states)
    Vf = -terminal_value_per_kwh * (grid - b.soc_min)
    policy = np.empty((T, n_states), dtype=np.int32)
    for t in range(T - 1, -1, -1):
        C = _step_cost_matrix(grid, load[t], pv[t], imp[t], exp[t], b) + Vf[None, :]
        policy[t] = np.argmin(C, axis=1)
        Vf = C[np.arange(n_states), policy[t]]
    s0 = b.soc_min if soc0 is None else soc0
    i = int(np.argmin(np.abs(grid - s0)))
    total = Vf[i]
    path = [grid[i]]
    for t in range(T):
        i = policy[t, i]
        path.append(grid[i])
    return np.array(path), float(total)


def optimize(load, pv, imp, exp, b: Battery, soc0: float = None, terminal_value_per_kwh: float = 0.0,
             return_flows: bool = False, **_ignored):
    """Exact continuous optimum via linear programming (HiGHS).

    The problem is convex (piecewise-linear costs with import price >= export price), so
    the LP optimum is the GLOBAL optimum for the given inputs - a certificate, not a heuristic.
    Variables per hour: c (AC charge), x (AC discharge), gi (grid import), ge (grid export),
    plus SoC s_0..s_T. Returns (SoC path len T+1, optimal cost).
    """
    load, pv, imp, exp = (np.asarray(a, float) for a in (load, pv, imp, exp))
    if np.any(exp > imp + 1e-12):
        raise ValueError("export price above import price makes the LP non-physical (simultaneous buy/sell)")
    T = len(load)
    s0 = b.soc_min if soc0 is None else float(np.clip(soc0, b.soc_min, b.soc_max))
    nC, nX, nGi, nGe, nS = 0, T, 2 * T, 3 * T, 4 * T
    nvar = 4 * T + T + 1
    cost = np.zeros(nvar)
    cost[nX:nX + T] = b.deg_cost_per_kwh / b.eta_discharge
    cost[nGi:nGi + T] = imp
    cost[nGe:nGe + T] = -exp
    cost[nS + T] = -terminal_value_per_kwh
    I = sp.identity(T, format="csr")
    Z = sp.csr_matrix((T, T))
    # balance: c - x - gi + ge = pv - load
    A1 = sp.hstack([I, -I, -I, I, sp.csr_matrix((T, T + 1))])
    # dynamics: s_{t+1} - s_t - eta_c c + x/eta_d = 0
    D = sp.diags([-np.ones(T), np.ones(T)], [0, 1], shape=(T, T + 1))
    A2 = sp.hstack([-b.eta_charge * I, (1.0 / b.eta_discharge) * I, Z, Z, D])
    A3 = sp.csr_matrix(([1.0], ([0], [nS])), shape=(1, nvar))
    A = sp.vstack([A1, A2, A3]).tocsc()
    rhs = np.concatenate([pv - load, np.zeros(T), [s0]])
    cmax = np.full(T, b.power_kw)
    if not b.allow_grid_charge:
        cmax = np.minimum(cmax, np.maximum(pv - load, 0.0))
    xmax = np.full(T, b.power_kw)
    if not b.allow_battery_export:
        xmax = np.minimum(xmax, np.maximum(load - pv, 0.0))
    bounds = ([(0, u) for u in cmax] + [(0, u) for u in xmax] + [(0, None)] * (2 * T)
              + [(b.soc_min, b.soc_max)] * (T + 1))
    res = linprog(cost, A_eq=A, b_eq=rhs, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"LP failed: {res.message}")
    z = res.x
    path = z[nS:nS + T + 1]
    if return_flows:
        return path, float(res.fun), dict(c=z[:T], x=z[nX:nX + T], gi=z[nGi:nGi + T], ge=z[nGe:nGe + T])
    return path, float(res.fun)


def clip_action(soc, desired_dc_delta, L, V, b: Battery):
    """Make a desired DC SoC change physically/contractually feasible. Returns (dc_delta, ac_in, ac_out)."""
    d = desired_dc_delta
    if d > 0:
        lim = min(d, b.power_kw * b.eta_charge, b.soc_max - soc)
        if not b.allow_grid_charge:
            lim = min(lim, max(V - L, 0.0) * b.eta_charge)
        d = max(lim, 0.0)
        return d, d / b.eta_charge, 0.0
    if d < 0:
        out = min(-d, b.power_kw / b.eta_discharge, soc - b.soc_min)
        if not b.allow_battery_export:
            out = min(out, max(L - V, 0.0) / b.eta_discharge)
        out = max(out, 0.0)
        return -out, 0.0, out * b.eta_discharge
    return 0.0, 0.0, 0.0


class Result(dict):
    """Simulation outcome. bill = energy charges only (fixed charges identical in all scenarios)."""


def simulate(load, pv, imp, exp, b: Battery, controller, soc0=None) -> Result:
    """Run a causal controller: controller(t, soc) -> desired DC delta for hour t."""
    T = len(load)
    soc = b.soc_min if soc0 is None else soc0
    socs = np.empty(T + 1); socs[0] = soc
    gi = np.empty(T); ge = np.empty(T); acin = np.empty(T); acout = np.empty(T)
    for t in range(T):
        d, ai, ao = clip_action(soc, controller(t, soc), load[t], pv[t], b)
        soc = soc + d
        net = load[t] - pv[t] + ai - ao
        gi[t] = max(net, 0.0); ge[t] = max(-net, 0.0); acin[t] = ai; acout[t] = ao
        socs[t + 1] = soc
    return _summarise(gi, ge, acin, acout, socs, imp, exp, b)


def _summarise(gi, ge, acin, acout, socs, imp, exp, b):
    dc_out = acout / b.eta_discharge
    bill = float(np.dot(imp, gi) - np.dot(exp, ge))
    deg = float(b.deg_cost_per_kwh * dc_out.sum())
    return Result(grid_import=gi, grid_export=ge, ac_in=acin, ac_out=acout, soc=socs,
                  bill=bill, degradation=deg, total_cost=bill + deg,
                  cycles=float(dc_out.sum() / max(b.capacity_kwh - b.soc_min, 1e-9)))


# ---------------- controllers ----------------

def no_battery(load, pv, imp, exp, b: Battery) -> Result:
    net = load - pv
    z = np.zeros(len(load))
    return _summarise(np.maximum(net, 0), np.maximum(-net, 0), z, z, np.full(len(load) + 1, b.soc_min), imp, exp, b)


def self_consumption_controller(load, pv, b: Battery):
    """Typical factory default: store solar surplus, discharge to cover any deficit."""
    def ctl(t, soc):
        s = pv[t] - load[t]
        return s * b.eta_charge if s > 0 else s / b.eta_discharge
    return ctl


def tou_rule_controller(load, pv, imp, b: Battery):
    """Simple vendor-style 'time-based control': store solar; discharge only in hours whose
    import price exceeds that day's minimum price; never grid-charge."""
    T = len(load)
    daymin = np.repeat(imp.reshape(-1, 24).min(axis=1), 24)[:T] if T % 24 == 0 else np.full(T, imp.min())

    def ctl(t, soc):
        s = pv[t] - load[t]
        if s > 0:
            return s * b.eta_charge
        if imp[t] > daymin[t] + 1e-6:
            return s / b.eta_discharge
        return 0.0
    return ctl


def plan_controller(plan):
    """Track a planned SoC path (used for perfect-foresight execution)."""
    return lambda t, soc: plan[t + 1] - soc


def perfect_foresight(load, pv, imp, exp, b: Battery) -> Result:
    """Upper bound on achievable savings: global LP optimum with the true future known."""
    plan, _ = optimize(load, pv, imp, exp, b, soc0=b.soc_min)
    return simulate(load, pv, imp, exp, b, plan_controller(plan), soc0=b.soc_min)
