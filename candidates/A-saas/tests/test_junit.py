import unittest
from pathlib import Path

from flakehunter.junit import FAIL, PASS, SKIP, parse_junit

FIX = Path(__file__).parent / "fixtures"


class JUnitParseTest(unittest.TestCase):
    def test_pytest_style(self):
        rs = parse_junit(FIX / "pytest_report.xml")
        by = {r.test_id: r for r in rs}
        self.assertEqual(len(rs), 4)
        self.assertEqual(by["tests.test_api::test_ok"].outcome, PASS)
        self.assertEqual(by["tests.test_api::test_fail"].outcome, FAIL)
        self.assertEqual(by["tests.test_api::test_error"].outcome, FAIL)
        self.assertEqual(by["tests.test_api::test_skip"].outcome, SKIP)
        self.assertEqual(rs[0].run_id, "pytest_report")
        self.assertAlmostEqual(by["tests.test_api::test_ok"].duration, 0.12)

    def test_bare_testsuite_root_and_commit(self):
        xml = '<testsuite name="S" commit="abc"><testcase name="t1" time="x"/></testsuite>'
        rs = parse_junit(xml, run_id="r1")
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].test_id, "S::t1")
        self.assertEqual(rs[0].commit, "abc")
        self.assertEqual(rs[0].duration, 0.0)  # bad time attribute tolerated

    def test_nested_suites_not_double_counted(self):
        xml = ('<testsuites><testsuite name="outer"><testsuite name="inner">'
               '<testcase classname="c" name="a"/></testsuite>'
               '<testcase classname="c" name="b"/></testsuite></testsuites>')
        ids = sorted(r.test_id for r in parse_junit(xml))
        self.assertEqual(ids, ["c::a", "c::b"])

    def test_rejects_doctype(self):
        evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>'
        with self.assertRaises(ValueError):
            parse_junit(evil)


if __name__ == "__main__":
    unittest.main()
