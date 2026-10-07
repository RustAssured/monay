import os
import unittest
from datetime import date

from spendaudit.parsers import load_file, merge_statements, parse_amount, parse_csv, parse_ofx
from spendaudit.synth import generate_household, to_chase_checking_csv, to_chase_credit_csv, to_ofx

FX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def fx(name):
    return os.path.join(FX, name)


class TestCSV(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount("$1,024.50"), 1024.5)
        self.assertEqual(parse_amount("(12.00)"), -12.0)
        self.assertEqual(parse_amount(""), 0.0)

    def test_chase_checking(self):
        st = load_file(fx("chase_checking.csv"))
        self.assertEqual(st.format, "chase_checking")
        self.assertEqual(len(st.transactions), 6)
        od = [t for t in st.transactions if "OVERDRAFT" in t.description][0]
        self.assertEqual(od.amount, -35.0)
        self.assertEqual(od.balance, -77.5)
        self.assertEqual(od.date, date(2026, 4, 3))

    def test_chase_credit(self):
        st = load_file(fx("chase_credit.csv"))
        self.assertEqual(st.format, "chase_credit")
        self.assertEqual(len(st.transactions), 7)
        self.assertTrue(all(t.amount < 0 for t in st.transactions if "NETFLIX" in t.description.upper()))
        self.assertEqual(st.transactions[0].category_hint, "Entertainment")

    def test_bofa_preamble_skipped(self):
        st = load_file(fx("bofa.csv"))
        self.assertEqual(st.format, "bofa")
        self.assertEqual([t.amount for t in st.transactions], [-11.99, 3000.0, -11.99])
        self.assertAlmostEqual(st.transactions[-1].balance, 3976.02)

    def test_amex_sign_flipped(self):
        st = load_file(fx("amex.csv"))
        self.assertEqual(st.format, "amex")
        amts = {t.description: t.amount for t in st.transactions}
        self.assertEqual(amts["HOTEL LISBOA CENTRO"], -200.0)
        self.assertEqual(amts["AUTOPAY PAYMENT - THANK YOU"], 206.0)

    def test_capital_one_debit_credit(self):
        st = load_file(fx("capital_one.csv"))
        self.assertEqual(st.format, "capital_one")
        self.assertEqual([t.amount for t in st.transactions], [-39.99, 250.0, -1024.5])

    def test_wells_fargo_headerless(self):
        st = load_file(fx("wells_fargo.csv"))
        self.assertEqual(st.format, "wells_fargo")
        self.assertEqual(len(st.transactions), 3)
        self.assertEqual(st.transactions[0].amount, -3.0)

    def test_generic_layout(self):
        st = parse_csv("Date,Payee,Debit,Credit\n2026-01-02,SHELL OIL 123,40.00,\n2026-01-03,REFUND,,5.00\n")
        self.assertEqual(st.format, "generic")
        self.assertEqual([t.amount for t in st.transactions], [-40.0, 5.0])


class TestOFX(unittest.TestCase):
    def test_sgml_v1(self):
        st = load_file(fx("sample_v1.ofx"))
        self.assertEqual(len(st.transactions), 4)
        acct = st.transactions[0].account
        self.assertEqual(acct, "checking-3210")
        self.assertEqual(st.balances[acct], (date(2026, 3, 31), 30141.50))
        self.assertEqual(st.transactions[0].date, date(2026, 1, 5))
        self.assertEqual(st.transactions[1].amount, 4200.0)
        self.assertIn("COMCAST", st.transactions[0].description)

    def test_xml_v2_credit_card(self):
        st = load_file(fx("sample_v2.qfx"))
        self.assertEqual(len(st.transactions), 4)
        self.assertEqual(st.transactions[0].account, "creditcard-1111")
        self.assertEqual(st.transactions[3].description, "BARNES & NOBLE #2231")
        self.assertEqual(st.balances["creditcard-1111"][1], -1520.40)

    def test_synthetic_ofx_roundtrip(self):
        hh = generate_household(3)
        chk = [t for t in hh.transactions if t.account == "checking"]
        st = parse_ofx(to_ofx(chk, closing=chk[-1].balance))
        self.assertEqual(len(st.transactions), len(chk))
        self.assertAlmostEqual(sum(t.amount for t in st.transactions), sum(t.amount for t in chk), places=2)
        self.assertAlmostEqual(list(st.balances.values())[0][1], chk[-1].balance, places=2)


class TestRoundTripAndMerge(unittest.TestCase):
    def test_synthetic_csv_roundtrip(self):
        hh = generate_household(5)
        chk = [t for t in hh.transactions if t.account == "checking"]
        card = [t for t in hh.transactions if t.account == "card"]
        a = parse_csv(to_chase_checking_csv(chk), "checking.csv")
        b = parse_csv(to_chase_credit_csv(card), "card.csv")
        self.assertEqual(len(a.transactions), len(chk))
        self.assertEqual(len(b.transactions), len(card))
        self.assertAlmostEqual(sum(t.amount for t in b.transactions), sum(t.amount for t in card), places=2)

    def test_overlapping_exports_deduped_but_true_duplicates_kept(self):
        head = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        jan = head + "01/05/2026,01/05/2026,TST* PIZZA,,Sale,-42.18,\n01/05/2026,01/05/2026,TST* PIZZA,,Sale,-42.18,\n"
        jan_feb = jan + "02/01/2026,02/01/2026,SHELL OIL,,Sale,-30.00,\n"
        s1, s2 = parse_csv(jan, "card.csv", account="card"), parse_csv(jan_feb, "card2.csv", account="card")
        merged = merge_statements([s1, s2])
        self.assertEqual(len(merged), 3)  # both pizza rows kept (genuine double charge), no overlap copies


if __name__ == "__main__":
    unittest.main()
