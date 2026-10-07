import unittest
from dataclasses import replace

import numpy as np

from sim import model as M


class UnitEconomicsTest(unittest.TestCase):
    def setUp(self):
        self.b = M.Params.base()

    def test_net_per_customer_hand_calc(self):
        arpa = 0.85 * 29 + 0.15 * 99 + 2.0  # 41.5
        fees = arpa * (0.029 + 0.007 + 0.005) + 0.30
        self.assertAlmostEqual(M.arpa(self.b), 41.5)
        self.assertAlmostEqual(M.net_per_customer(self.b), arpa - fees - 0.3)

    def test_breakeven_definition(self):
        n = M.breakeven_customers(self.b)
        self.assertAlmostEqual(n * M.net_per_customer(self.b), M.fixed_cost(self.b))
        self.assertLess(M.breakeven_customers(self.b, include_time=False), n)

    def test_deterministic_steady_state(self):
        p = self.b
        paid, profit = M.simulate_path(p, None, months=400, kill_rule=False)
        self.assertAlmostEqual(paid[-1], p.new_free_orgs_per_month * p.free_to_paid / p.monthly_churn, places=3)
        # at steady state profit == paid*net - fixed
        self.assertAlmostEqual(profit[-1], paid[-1] * M.net_per_customer(p) - M.fixed_cost(p), places=3)

    def test_monotone_in_funnel_and_churn(self):
        tot = lambda p: M.simulate_path(p, None, kill_rule=False)[1].sum()
        self.assertGreater(tot(replace(self.b, new_free_orgs_per_month=80)), tot(self.b))
        self.assertLess(tot(replace(self.b, monthly_churn=0.1)), tot(self.b))

    def test_kill_rule_bounds_cash_loss(self):
        dead = replace(self.b, new_free_orgs_per_month=0)
        _, prof = M.simulate_path(dead, None, kill_rule=True, include_time=False)
        self.assertAlmostEqual(prof.sum(), -M.KILL_MONTH * dead.hosting_fixed)
        self.assertTrue(np.all(prof[M.KILL_MONTH:] == 0))

    def test_monte_carlo_reproducible_and_sane(self):
        a = M.monte_carlo(n=300, seed=1)
        b = M.monte_carlo(n=300, seed=1)
        self.assertEqual(a, b)
        self.assertTrue(0 <= a["p_cum_profit_positive_36m"] <= 1)
        c = a["cum_net_36m_after_tax"]
        self.assertLessEqual(c["p10"], c["p50"]); self.assertLessEqual(c["p50"], c["p90"])
        cash = M.monte_carlo(n=300, seed=1, include_time=False)
        self.assertGreaterEqual(cash["p_cum_profit_positive_36m"], a["p_cum_profit_positive_36m"])

    def test_after_tax(self):
        self.assertEqual(M.after_tax(100), 75)
        self.assertEqual(M.after_tax(-100), -100)  # no tax credit assumed on losses

    def test_tornado_sorted(self):
        t = M.tornado()
        swings = [r["swing"] for r in t[1:]]
        self.assertEqual(swings, sorted(swings, reverse=True))
        self.assertEqual(t[1]["param"], "new_free_orgs_per_month")  # funnel dominates


if __name__ == "__main__":
    unittest.main()
