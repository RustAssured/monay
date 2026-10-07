import os
import unittest
from datetime import date, timedelta

from spendaudit import detectors as D
from spendaudit.models import Transaction
from spendaudit.normalize import fee_type, is_transfer, merchant_family, merchant_key
from spendaudit.parsers import load_file

FX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def T(d, amt, desc, acct="card", bal=None, i=[0]):
    i[0] += 1
    return Transaction(d, amt, desc, acct, bal, f"t{i[0]}")


def monthly(desc, amt, n, start=date(2025, 1, 10), acct="card"):
    out, d = [], start
    for k in range(n):
        m = start.month - 1 + k
        out.append(T(date(start.year + m // 12, m % 12 + 1, start.day), -amt, desc, acct))
    return out


class TestNormalize(unittest.TestCase):
    def test_merchant_keys(self):
        self.assertEqual(merchant_key("NETFLIX.COM 866-579-7172 CA"), "NETFLIX")
        self.assertEqual(merchant_key("Netflix.com Los Gatos CA"), "NETFLIX")
        self.assertEqual(merchant_key("HLU*HULUPLUS 888-265-6650"), "HULU")
        self.assertEqual(merchant_key("SQ *BLUE BOTTLE COFFEE"), "BLUE BOTTLE COFFEE")
        self.assertEqual(merchant_key("AMZN Mktp US*2K4LM8"), "AMAZON MARKETPLACE")
        self.assertEqual(merchant_key("Amazon Prime*2K4LM8 Amzn.com/bill WA"), "AMAZON PRIME")
        self.assertEqual(merchant_family("PLANET FITNESS #1123 800-7766"),
                         merchant_family("PLANET FITNESS CLUB FEES"))

    def test_fee_types_and_decoys(self):
        self.assertEqual(fee_type("OVERDRAFT ITEM FEE", -35), "overdraft")
        self.assertEqual(fee_type("NON-NETWORK ATM FEE-WITHDRAWAL", -3), "atm")
        self.assertEqual(fee_type("FOREIGN TRANSACTION FEE", -6), "fx")
        self.assertEqual(fee_type("MONTHLY MAINTENANCE FEE", -12), "maintenance")
        self.assertEqual(fee_type("PURCHASE INTEREST CHARGE", -48.12), "interest")
        self.assertIsNone(fee_type("COFFEE BEAN & TEA LEAF 123", -5))
        self.assertIsNone(fee_type("ATMOSPHERE BAR & GRILL", -40))
        self.assertIsNone(fee_type("MONTHLY SERVICE FEE REVERSAL", -12))
        self.assertIsNone(fee_type("MONTHLY SERVICE FEE", 12))       # inflow is not a fee
        self.assertIsNone(fee_type("ATM WITHDRAWAL 4321 MAIN ST", -60))  # cash, not a fee

    def test_transfers(self):
        self.assertTrue(is_transfer("Payment Thank You-Mobile"))
        self.assertTrue(is_transfer("CHASE CREDIT CRD AUTOPAY PPD ID: 4760039224"))
        self.assertTrue(is_transfer("ONLINE TRANSFER TO SAV XXXXXX4821"))
        self.assertFalse(is_transfer("NETFLIX.COM"))


class TestRecurring(unittest.TestCase):
    def test_monthly_subscription_and_price_creep(self):
        txs = monthly("NETFLIX.COM 866-579-7172 CA", 15.49, 6) + monthly("NETFLIX.COM", 17.99, 3, date(2025, 7, 10))
        rec = D.detect_recurring(txs)
        self.assertEqual(len(rec), 1)
        s = rec[0]
        self.assertEqual((s.merchant, s.cadence, s.kind, s.active), ("NETFLIX", "monthly", "fixed", True))
        self.assertAlmostEqual(s.annual_cost, 17.99 * 12)
        creep = D.detect_price_creep(rec)
        self.assertEqual(len(creep), 1)
        self.assertAlmostEqual(creep[0].old_amount, 15.49)
        self.assertAlmostEqual(creep[0].annual_increase, 2.50 * 12)

    def test_two_subscriptions_same_descriptor_split(self):
        txs = monthly("APPLE.COM/BILL 866-712-7753 CA", 2.99, 8, date(2025, 1, 3)) + \
            monthly("APPLE.COM/BILL 866-712-7753 CA", 10.99, 8, date(2025, 1, 21))
        rec = D.detect_recurring(txs)
        self.assertEqual(sorted(round(s.last_amount, 2) for s in rec), [2.99, 10.99])

    def test_variable_grocery_not_recurring(self):
        import random
        rng = random.Random(1)
        txs = [T(date(2025, 1, 4) + timedelta(days=7 * k), -rng.uniform(30, 190), "TRADER JOE'S #552 OAKLAND CA")
               for k in range(30)]
        self.assertEqual(D.detect_recurring(txs), [])

    def test_annual_needs_isolated_merchant(self):
        sub = [T(date(2025, 3, 1), -139.0, "Amazon Prime*2K4LM8"), T(date(2026, 3, 1), -139.0, "AMAZON PRIME*RT5QW1")]
        rec = D.detect_recurring(sub)
        self.assertEqual([(s.merchant, s.cadence) for s in rec], [("AMAZON PRIME", "annual")])
        noise = [T(date(2025, 2, 1), -61.0, "OLIVE GARDEN 1234"), T(date(2025, 2, 20), -33.0, "OLIVE GARDEN 1234"),
                 T(date(2026, 2, 21), -34.0, "OLIVE GARDEN 1234")]
        self.assertEqual(D.detect_recurring(noise), [])

    def test_cancelled_subscription_inactive(self):
        txs = monthly("SPOTIFY USA 8777781161", 11.99, 5) + [T(date(2026, 1, 1), -5.0, "MISC SHOP")]
        rec = D.detect_recurring(txs)
        self.assertFalse(rec[0].active)
        self.assertEqual(D.detect_price_creep(rec), [])

    def test_weekly_with_skip(self):
        d0 = date(2025, 1, 6)
        days = [0, 7, 14, 28, 35, 42, 49, 56]
        txs = [T(d0 + timedelta(days=k), -69.99, "HELLOFRESH 646-846-3663 NY") for k in days]
        rec = D.detect_recurring(txs)
        self.assertEqual(rec[0].cadence, "weekly")


class TestDuplicatesFees(unittest.TestCase):
    def test_fixture_duplicate_and_creep(self):
        txs = load_file(os.path.join(FX, "chase_credit.csv")).transactions
        dups = D.detect_duplicates(txs, D.detect_recurring(txs))
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0].amount, 42.18)
        creep = D.detect_price_creep(D.detect_recurring(txs))
        self.assertEqual(creep[0].new_amount, 17.99)

    def test_refunded_duplicate_not_flagged(self):
        txs = [T(date(2025, 5, 1), -80.0, "BEST BUY 1234"), T(date(2025, 5, 1), -80.0, "BEST BUY 1234"),
               T(date(2025, 5, 6), 80.0, "BEST BUY 1234")]
        self.assertEqual(D.detect_duplicates(txs), [])

    def test_small_habitual_purchases_not_flagged(self):
        txs = [T(date(2025, 5, 1), -5.25, "STARBUCKS STORE 123"), T(date(2025, 5, 1), -5.25, "STARBUCKS STORE 123")]
        self.assertEqual(D.detect_duplicates(txs), [])

    def test_fee_annualisation(self):
        txs = load_file(os.path.join(FX, "chase_checking.csv")).transactions
        fees = {f.kind: f for f in D.detect_fees(txs)}
        self.assertEqual(set(fees), {"overdraft", "maintenance"})
        span = (date(2026, 4, 3) - date(2026, 3, 1)).days + 1
        self.assertLess(span, 90)
        self.assertAlmostEqual(fees["overdraft"].annualized, 35.0)  # short history: no extrapolation


class TestOverlapIdle(unittest.TestCase):
    def test_streaming_overlap_and_usage(self):
        txs = []
        for name, p in (("NETFLIX.COM", 15.49), ("HULU 877-8244858", 7.99), ("DISNEY PLUS 888", 13.99)):
            txs += monthly(name, p, 6)
        rec = D.detect_recurring(txs)
        rev = D.detect_overlap_and_unused(rec)
        self.assertEqual(len(rev), 1)
        self.assertAlmostEqual(rev[0].potential_annual, 7.99 * 12)
        rev2 = D.detect_overlap_and_unused(rec, usage={"NETFLIX": date(2025, 2, 1)})
        self.assertTrue(any("not used" in r.reason and r.series[0].merchant == "NETFLIX" for r in rev2))

    def test_idle_cash_from_ofx_closing_balance(self):
        st = load_file(os.path.join(FX, "sample_v1.ofx"))
        acct = st.transactions[0].account
        ic = D.detect_idle_cash(st.transactions, acct, closing=st.balances[acct], target_apy=0.04, current_apy=0.0)
        self.assertIsNotNone(ic)
        opening = 30141.50 - (-55 + 4200 - 60.5 - 2.5)
        self.assertAlmostEqual(ic.p10_balance, opening - 55, places=2)
        self.assertAlmostEqual(ic.annual_gain, ic.excess * 0.04)

    def test_no_idle_cash_for_lean_account(self):
        txs = load_file(os.path.join(FX, "chase_checking.csv")).transactions
        self.assertIsNone(D.detect_idle_cash(txs, txs[0].account))


if __name__ == "__main__":
    unittest.main()
