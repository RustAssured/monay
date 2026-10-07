import itertools
import unittest
import numpy as np

from tou_battery.battery import Battery
from tou_battery.tariff import TARIFFS, CA_TOU_ILLUSTRATIVE, CHEAP_NIGHTS_ILLUSTRATIVE, FLAT_ILLUSTRATIVE, hour_index
from tou_battery.profiles import synthetic_load, synthetic_pv
from tou_battery import dispatch as D
from tou_battery.mpc import run_mpc, mpc_controller
from tou_battery import economics as E

N = 24 * 14  # two weeks in July (summer rates) keeps tests fast
OFF = 24 * 181


def data(pv_kw=6.0, seed=0, tariff=CA_TOU_ILLUSTRATIVE, n=N, off=OFF):
    L = synthetic_load(seed)[off:off + n]
    V = synthetic_pv(seed, kw_dc=pv_kw)[off:off + n]
    imp, exp = tariff.prices()
    return L, V, imp[off:off + n], exp[off:off + n]


class TestOptimalityCertificates(unittest.TestCase):
    def test_dp_equals_exhaustive_bruteforce(self):
        rng = np.random.default_rng(1)
        b = Battery(capacity_kwh=4.0, reserve_frac=0.0, power_kw=2.0, allow_battery_export=True)
        T, n = 5, 5
        L, V = rng.uniform(0, 2, T), rng.uniform(0, 1, T)
        imp = rng.uniform(0.1, 0.6, T); exp = imp * 0.3
        _, dp_cost = D.optimize_dp(L, V, imp, exp, b, soc0=0.0, n_states=n)
        grid = D.soc_grid(b, n)
        best = np.inf
        for path in itertools.product(range(n), repeat=T):
            i, c = 0, 0.0
            for t, j in enumerate(path):
                c += D._step_cost_matrix(grid, L[t], V[t], imp[t], exp[t], b)[i, j]
                i = j
            best = min(best, c)
        self.assertAlmostEqual(dp_cost, best, places=9)

    def test_lp_is_global_optimum_vs_dp(self):
        """LP (continuous) must be <= DP (discrete subset) and DP must converge to it as the grid refines."""
        b = Battery(allow_battery_export=True)
        L, V, imp, exp = data(n=72)
        _, lp = D.optimize(L, V, imp, exp, b)
        gaps = []
        for n in (10, 28, 55):
            _, dp = D.optimize_dp(L, V, imp, exp, b, soc0=b.soc_min, n_states=n)
            self.assertLessEqual(lp, dp + 1e-6)
            gaps.append(dp - lp)
        self.assertLess(gaps[-1], 0.05 * abs(lp) + 0.5)

    def test_perfect_foresight_never_worse_than_any_baseline(self):
        for tariff in TARIFFS.values():
            for pv_kw in (0.0, 6.0):
                for b in (Battery(), Battery(allow_grid_charge=False), Battery(allow_battery_export=True)):
                    L, V, imp, exp = data(pv_kw, tariff=tariff)
                    pf = D.perfect_foresight(L, V, imp, exp, b)["total_cost"]
                    for r in (D.no_battery(L, V, imp, exp, b),
                              D.simulate(L, V, imp, exp, b, D.self_consumption_controller(L, V, b)),
                              D.simulate(L, V, imp, exp, b, D.tou_rule_controller(L, V, imp, b)),
                              D.simulate(L, V, imp, exp, b, D.tou_rule_controller(L, V, imp, b, grid_charge=True)),
                              run_mpc(L, V, imp, exp, b, replan_every=12)):
                        self.assertLessEqual(pf, r["total_cost"] + 1e-3, (tariff.name, pv_kw, b))

    def test_flat_tariff_means_no_cycling(self):
        b = Battery()
        L, V, imp, exp = data(0.0, tariff=FLAT_ILLUSTRATIVE)
        r = D.perfect_foresight(L, V, imp, exp, b)
        self.assertAlmostEqual(r["cycles"], 0.0, places=6)
        self.assertAlmostEqual(r["total_cost"], D.no_battery(L, V, imp, exp, b)["total_cost"], places=6)
        self.assertAlmostEqual(run_mpc(L, V, imp, exp, b)["cycles"], 0.0, places=6)

    def test_analytic_threshold_matches_lp(self):
        b = Battery(capacity_kwh=10, reserve_frac=0.0)
        p_off = 0.10
        thr = E.arbitrage_threshold_price(p_off, b)
        for p, expect_trade in ((thr * 0.98, False), (thr * 1.02, True)):
            L = np.array([0.0, 1.0]); V = np.zeros(2)
            imp = np.array([p_off, p]); exp = np.zeros(2)
            path, _, fl = D.optimize(L, V, imp, exp, b, soc0=0.0, return_flows=True)
            self.assertEqual(fl["c"][0] > 1e-6, expect_trade, p)

    def test_savings_bounded_by_theoretical_maximum(self):
        b = Battery()
        L, V, imp, exp = data(0.0, tariff=CHEAP_NIGHTS_ILLUSTRATIVE)
        saving = D.no_battery(L, V, imp, exp, b)["total_cost"] - D.perfect_foresight(L, V, imp, exp, b)["total_cost"]
        bound = (N / 24 + 1) * E.daily_arbitrage_upper_bound(imp.min(), imp.max(), b)
        self.assertGreater(saving, 0)
        self.assertLessEqual(saving, bound)

    def test_lp_rejects_export_above_import(self):
        with self.assertRaises(ValueError):
            D.optimize(np.ones(2), np.zeros(2), np.array([0.1, 0.1]), np.array([0.2, 0.0]), Battery())


class TestPhysicsAndRules(unittest.TestCase):
    def check(self, r, L, V, b):
        self.assertTrue(np.all(r["soc"] >= b.soc_min - 1e-9) and np.all(r["soc"] <= b.soc_max + 1e-9))
        self.assertTrue(np.all(r["ac_in"] <= b.power_kw + 1e-9) and np.all(r["ac_out"] <= b.power_kw + 1e-9))
        bal = (L - V + r["ac_in"] - r["ac_out"]) - (r["grid_import"] - r["grid_export"])
        self.assertLess(np.abs(bal).max(), 1e-9)
        dsoc = np.diff(r["soc"]) - (r["ac_in"] * b.eta_charge - r["ac_out"] / b.eta_discharge)
        self.assertLess(np.abs(dsoc).max(), 1e-9)
        if not b.allow_battery_export:
            self.assertTrue(np.all(r["ac_out"] <= np.maximum(L - V, 0) + 1e-9))
        if not b.allow_grid_charge:
            self.assertTrue(np.all(r["ac_in"] <= np.maximum(V - L, 0) + 1e-9))

    def test_constraints_respected_by_all_controllers(self):
        for b in (Battery(), Battery(allow_grid_charge=False), Battery(allow_battery_export=True)):
            L, V, imp, exp = data(6.0, tariff=CHEAP_NIGHTS_ILLUSTRATIVE)
            for r in (D.perfect_foresight(L, V, imp, exp, b), run_mpc(L, V, imp, exp, b),
                      D.simulate(L, V, imp, exp, b, D.self_consumption_controller(L, V, b))):
                self.check(r, L, V, b)


class TestCausalController(unittest.TestCase):
    def test_mpc_does_not_peek_at_future(self):
        b = Battery()
        L, V, imp, exp = data(6.0)
        L2, V2 = L.copy(), V.copy()
        cut = 24 * 5 + 7
        L2[cut:] *= 3.0; V2[cut:] *= 0.1
        c1, c2 = mpc_controller(L, V, imp, exp, b), mpc_controller(L2, V2, imp, exp, b)
        soc1 = soc2 = b.soc_min
        for t in range(cut):
            a1, a2 = c1(t, soc1), c2(t, soc2)
            self.assertEqual(a1, a2, t)
            soc1 += D.clip_action(soc1, a1, L[t], V[t], b)[0]
            soc2 += D.clip_action(soc2, a2, L2[t], V2[t], b)[0]

    def test_mpc_with_oracle_forecast_captures_most_of_optimum(self):
        b = Battery()
        L, V, imp, exp = data(0.0, tariff=CHEAP_NIGHTS_ILLUSTRATIVE)
        nb = D.no_battery(L, V, imp, exp, b)["total_cost"]
        pf = nb - D.perfect_foresight(L, V, imp, exp, b)["total_cost"]
        mo = nb - run_mpc(L, V, imp, exp, b, oracle=True)["total_cost"]
        self.assertGreater(mo, 0.9 * pf)

    def test_realistic_mpc_beats_factory_default_without_solar(self):
        b = Battery()
        for tariff in (CA_TOU_ILLUSTRATIVE, CHEAP_NIGHTS_ILLUSTRATIVE):
            L, V, imp, exp = data(0.0, tariff=tariff)
            sc = D.simulate(L, V, imp, exp, b, D.self_consumption_controller(L, V, b))["total_cost"]
            self.assertLess(run_mpc(L, V, imp, exp, b)["total_cost"], sc - 1.0)


class TestTariffAndProfiles(unittest.TestCase):
    def test_tariff_shapes(self):
        month, hod, wd = hour_index()
        self.assertEqual(len(month), 8760)
        self.assertEqual(wd[0], 2)  # 2025-01-01 Wednesday
        for t in TARIFFS.values():
            imp, exp = t.prices()
            self.assertTrue(np.all(exp <= imp))
        imp, _ = CA_TOU_ILLUSTRATIVE.prices()
        jul = (month == 7)
        self.assertTrue(np.all(imp[jul & (hod == 18)] == 0.60))
        self.assertTrue(np.all(imp[jul & (hod == 3)] == 0.38))

    def test_profiles_deterministic(self):
        np.testing.assert_array_equal(synthetic_load(3), synthetic_load(3))
        self.assertAlmostEqual(synthetic_load(3).sum(), 7000.0, places=6)
        self.assertAlmostEqual(synthetic_pv(3).sum(), 9000.0, places=6)
        self.assertTrue(np.all(synthetic_pv(3) >= 0))
        self.assertEqual(synthetic_pv(3, kw_dc=0).sum(), 0.0)


if __name__ == "__main__":
    unittest.main()
