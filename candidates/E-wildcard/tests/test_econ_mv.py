import unittest
import numpy as np

from tou_battery.battery import Battery
from tou_battery.tariff import CHEAP_NIGHTS_ILLUSTRATIVE, TARIFFS
from tou_battery.profiles import synthetic_load, synthetic_pv
from tou_battery import dispatch as D
from tou_battery import economics as E
from tou_battery.mpc import run_mpc
from tou_battery.mv import verify, recommend, best_plan


class TestEconomics(unittest.TestCase):
    def test_npv_known_value(self):
        # 100/yr for 3 yrs at 10%: 90.909 + 82.645 + 75.131 = 248.685
        self.assertAlmostEqual(E.npv(100, 3, 0.10), 248.6851991, places=5)
        self.assertAlmostEqual(E.npv(100, 3, 0.0, fade=0.5), 175.0)

    def test_break_even_capex_zeroes_npv(self):
        be = E.break_even_capex(500, 12, 0.05, 0.02, annual_cost=5)
        self.assertAlmostEqual(E.npv(500, 12, 0.05, 0.02, capex=be, annual_cost=5), 0.0, places=9)

    def test_payback(self):
        self.assertEqual(E.simple_payback_years(80, 5, 5), float("inf"))
        self.assertAlmostEqual(E.simple_payback_years(80, 45, 5), 2.0)

    def test_thresholds(self):
        b = Battery()
        self.assertAlmostEqual(E.arbitrage_threshold_price(0.0, b), b.deg_cost_per_kwh / b.eta_discharge)
        self.assertEqual(E.daily_arbitrage_upper_bound(0.3, 0.3, b), 0.0)


class TestMeasurementAndVerification(unittest.TestCase):
    def setUp(self):
        n = 24 * 10
        self.b = Battery()
        self.L, self.V = synthetic_load(1)[:n], synthetic_pv(1, kw_dc=0)[:n]
        imp, exp = CHEAP_NIGHTS_ILLUSTRATIVE.prices()
        self.imp, self.exp = imp[:n], exp[:n]
        self.r = run_mpc(self.L, self.V, self.imp, self.exp, self.b)

    def test_verify_consistent_and_exact(self):
        r = self.r
        v = verify(self.L, self.V, r["grid_import"], r["grid_export"], r["ac_in"], r["ac_out"], self.imp, self.exp, self.b)
        self.assertTrue(v["meter_data_consistent"])
        self.assertAlmostEqual(v["actual_bill"], r["bill"], places=9)
        nb = D.no_battery(self.L, self.V, self.imp, self.exp, self.b)["total_cost"]
        self.assertAlmostEqual(v["savings_vs_no_battery_net_of_degradation"], nb - r["total_cost"], places=9)
        self.assertGreater(v["savings_vs_default_mode_net_of_degradation"], 0)

    def test_verify_flags_tampered_meter_data(self):
        r = self.r
        gi = r["grid_import"].copy(); gi[5] -= 0.5
        v = verify(self.L, self.V, gi, r["grid_export"], r["ac_in"], r["ac_out"], self.imp, self.exp, self.b)
        self.assertFalse(v["meter_data_consistent"])

    def test_recommend_reverts_when_not_paying(self):
        self.assertTrue(recommend(10.0).startswith("KEEP"))
        self.assertTrue(recommend(10.0, fee=12.0).startswith("REVERT"))
        self.assertTrue(recommend(-1.0).startswith("REVERT"))

    def test_best_plan_sorted(self):
        rows = best_plan(self.L, self.V, self.b, TARIFFS.values())
        costs = [r["total_cost"] for r in rows]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(len(rows), 3 * len(TARIFFS))


if __name__ == "__main__":
    unittest.main()
