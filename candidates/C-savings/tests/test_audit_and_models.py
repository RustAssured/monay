import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from spendaudit.__main__ import main
from spendaudit.audit import run_audit
from spendaudit.economics import (HOUSEHOLD_RANGES, PRODUCT_RANGES, HouseholdAssumptions, ProductAssumptions,
                                  household_net, monte_carlo, product_year1, sensitivity)
from spendaudit.evaluate import evaluate
from spendaudit.parsers import load_file, merge_statements
from spendaudit.report import to_json, to_markdown
from spendaudit.synth import generate_household

FX = os.path.join(os.path.dirname(__file__), "..", "fixtures")
ALL = [os.path.join(FX, f) for f in sorted(os.listdir(FX)) if f != "usage.csv"]


class TestAudit(unittest.TestCase):
    def test_fixture_audit_end_to_end(self):
        stmts = [load_file(p) for p in ALL]
        bal = {}
        for s in stmts:
            bal.update(s.balances)
        res = run_audit(merge_statements(stmts), balances=bal)
        kinds = {a.kind for a in res.actions}
        self.assertTrue({"fee", "duplicate", "price_creep", "idle_cash"} <= kinds, kinds)
        res2 = run_audit(merge_statements(stmts), balances=bal, usage={"NETFLIX": __import__("datetime").date(2026, 1, 1)})
        self.assertNotIn("price_creep", {a.kind for a in res2.actions})  # cancel suggestion supersedes creep
        ev = [a.expected_value_12m for a in res.actions]
        self.assertEqual(ev, sorted(ev, reverse=True))
        self.assertEqual(len(res.forecast), 6)
        md = to_markdown(res)
        self.assertIn("Ranked actions", md)
        data = json.loads(to_json(res))
        self.assertAlmostEqual(data["expected_annual"], round(res.expected_annual, 2), places=2)

    def test_synthetic_household_values_consistent(self):
        res = run_audit(generate_household(33).transactions, checking_accounts=["checking"])
        self.assertGreater(res.identified_annual, 0)
        self.assertLessEqual(res.expected_annual, res.identified_annual)
        for f in res.forecast:
            self.assertGreaterEqual(f["net_after_actions"], f["net"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.md")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["audit", *ALL, "--usage", os.path.join(FX, "usage.csv"), "--out", out])
            self.assertEqual(rc, 0)
            self.assertIn("NETFLIX", open(out).read())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["demo", "--seed", "33", "--dir", d, "--json", "--out",
                                       os.path.join(d, "demo.json")]), 0)
            self.assertGreater(json.load(open(os.path.join(d, "demo.json")))["identified_annual"], 0)


class TestEvaluationRegression(unittest.TestCase):
    """Guards measured quality on the labelled synthetic benchmark (40 households, fixed seeds)."""

    @classmethod
    def setUpClass(cls):
        cls.m, cls.t = evaluate(40, 1000)

    def test_thresholds(self):
        m = self.m
        self.assertGreaterEqual(m["recurring_out_detectable"]["precision"], 0.95)
        self.assertGreaterEqual(m["recurring_out_detectable"]["recall"], 0.95)
        self.assertGreaterEqual(m["price_creep"]["precision"], 0.9)
        self.assertGreaterEqual(m["price_creep"]["recall"], 0.9)
        self.assertGreaterEqual(m["duplicates"]["recall"], 0.9)
        self.assertGreaterEqual(m["duplicates"]["precision"], 0.6)
        self.assertGreaterEqual(m["fees"]["precision"], 0.99)
        self.assertGreaterEqual(m["fees"]["recall"], 0.7)
        self.assertGreaterEqual(m["idle_cash"]["f1"], 0.8)

    def test_deterministic(self):
        m2, t2 = evaluate(5, 1000)
        m3, t3 = evaluate(5, 1000)
        self.assertEqual(m2, m3)


class TestEconomics(unittest.TestCase):
    def test_household_breakeven(self):
        a = HouseholdAssumptions(expected_savings_per_year=400, realisation_rate=0.5, setup_hours=2, rerun_hours_per_year=0,
                                 value_of_time_per_hour=20, tool_cost_per_year=0)
        h = household_net(a)
        self.assertAlmostEqual(h["net"], 200 - 40)
        self.assertAlmostEqual(h["breakeven_savings"], 80)

    def test_product_breakeven(self):
        a = ProductAssumptions(price_per_year=36, refund_rate=0.0, payment_pct=0.0, payment_fixed=0.0,
                               fixed_costs_per_year=1800, free_users_year1=1000, conversion_rate=0.05)
        p = product_year1(a)
        self.assertAlmostEqual(p["breakeven_customers"], 50)
        self.assertAlmostEqual(p["net"], 50 * 36 - 1800)

    def test_sensitivity_and_mc(self):
        b, rows = sensitivity(ProductAssumptions(), PRODUCT_RANGES, product_year1)
        spans = [r[5] for r in rows]
        self.assertEqual(spans, sorted(spans, reverse=True))
        mc1 = monte_carlo(HouseholdAssumptions(), HOUSEHOLD_RANGES, household_net, n=2000)
        mc2 = monte_carlo(HouseholdAssumptions(), HOUSEHOLD_RANGES, household_net, n=2000)
        self.assertEqual(mc1, mc2)
        self.assertLessEqual(mc1["p05"], mc1["p50"])
        self.assertLessEqual(mc1["p50"], mc1["p95"])


if __name__ == "__main__":
    unittest.main()
