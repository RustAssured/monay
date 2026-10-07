import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

from flakehunter import synth
from flakehunter.analyze import BROKEN, CONFIRMED, STABLE, SUSPECTED, analyze, summarize, to_markdown, wilson_interval
from flakehunter.cli import main
from flakehunter.junit import FAIL, PASS, TestResult


def R(tid, outcome, run, commit=None):
    return TestResult(tid, outcome, 0.1, run, commit)


class ClassificationTest(unittest.TestCase):
    def test_same_commit_conflict_is_confirmed(self):
        st = analyze([R("t", FAIL, "1", "c1"), R("t", PASS, "2", "c1")])
        self.assertEqual(st["t"].verdict, CONFIRMED)
        self.assertEqual(st["t"].same_commit_conflicts, ["c1"])

    def test_broken_streak(self):
        seq = [PASS] * 5 + [FAIL] * 3
        st = analyze([R("t", o, str(i), f"c{i}") for i, o in enumerate(seq)])
        self.assertEqual(st["t"].verdict, BROKEN)

    def test_flipping_without_proof_is_suspected(self):
        seq = [PASS, FAIL, PASS, PASS, FAIL, PASS, PASS]
        st = analyze([R("t", o, str(i), f"c{i}") for i, o in enumerate(seq)])
        self.assertEqual(st["t"].verdict, SUSPECTED)

    def test_single_failure_is_not_flaky(self):
        seq = [PASS] * 9 + [FAIL] + [PASS] * 5
        st = analyze([R("t", o, str(i), f"c{i}") for i, o in enumerate(seq)])
        self.assertEqual(st["t"].verdict, STABLE)

    def test_skips_ignored_for_rates(self):
        st = analyze([R("t", "skip", "1"), R("t", PASS, "2")])["t"]
        self.assertEqual((st.runs, st.skips, st.fail_rate), (1, 1, 0.0))

    def test_wilson(self):
        lo, hi = wilson_interval(0, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        lo, hi = wilson_interval(5, 10)
        self.assertLess(lo, 0.5); self.assertGreater(hi, 0.5)
        lo2, hi2 = wilson_interval(500, 1000)
        self.assertLess(hi2 - lo2, hi - lo)  # more data -> narrower

    def test_markdown_and_summary(self):
        st = analyze([R("a", FAIL, "1", "c"), R("a", PASS, "2", "c"), R("b", PASS, "1", "c")])
        s = summarize(st)
        self.assertEqual(s["quarantine"], ["a"])
        md = to_markdown(st)
        self.assertIn("1 confirmed flaky", md)
        self.assertIn("`a`", md)
        self.assertIn("No flaky", to_markdown(analyze([R("b", PASS, "1")])))


class GroundTruthAccuracyTest(unittest.TestCase):
    """Detector accuracy on synthetic CI histories with known truth (10 seeds)."""

    def test_precision_and_recall(self):
        tp = fp = fn = broken_ok = broken_total = 0
        for seed in range(10):
            runs, truth = synth.generate(seed=seed)
            from flakehunter.junit import parse_junit
            results = [r for rid, xml in runs for r in parse_junit(xml, run_id=rid)]
            st = analyze(results)
            for tid, t in truth.items():
                flagged = st[tid].verdict in (CONFIRMED, SUSPECTED)
                tp += flagged and t == "flaky"
                fp += flagged and t != "flaky"
                fn += (not flagged) and t == "flaky"
                if t == "broken":
                    broken_total += 1
                    broken_ok += st[tid].verdict == BROKEN
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        print(f"\n[ground truth] precision={precision:.3f} recall={recall:.3f} "
              f"broken={broken_ok}/{broken_total}")
        self.assertEqual(fp, 0, "stable/broken tests must never be quarantined")
        self.assertGreaterEqual(recall, 0.9)
        self.assertEqual(broken_ok, broken_total)


class CliTest(unittest.TestCase):
    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            truth = synth.write(d, seed=3)
            q = Path(d) / "quarantine.txt"
            out = StringIO()
            with redirect_stdout(out):
                rc = main(["analyze", f"{d}/run*.xml", "--json", "--quarantine-out", str(q)])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            flaky_truth = {t for t, v in truth.items() if v == "flaky"}
            self.assertTrue(set(q.read_text().split()) <= flaky_truth)
            self.assertEqual(data["summary"]["tests"], 200)

            # gating: an empty known-list means everything is "new" -> exit 1
            known = Path(d) / "known.txt"; known.write_text("")
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["analyze", f"{d}/run*.xml", "--fail-on-new-flaky", str(known)]), 1)
            known.write_text(q.read_text())
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["analyze", f"{d}/run*.xml", "--fail-on-new-flaky", str(known)]), 0)

    def test_no_files(self):
        with redirect_stderr(StringIO()):
            self.assertEqual(main(["analyze", "/nonexistent/*.xml"]), 2)


if __name__ == "__main__":
    unittest.main()


class NoRetryAccuracyTest(unittest.TestCase):
    """Harder case: CI never reruns, so there is no same-commit proof."""

    def test_no_retry_has_zero_false_positives(self):
        from flakehunter.junit import parse_junit
        tp = fp = fn = 0
        for seed in range(20):
            runs, truth = synth.generate(seed=seed, retry_on_fail=False)
            st = analyze([r for rid, xml in runs for r in parse_junit(xml, run_id=rid)])
            for tid, t in truth.items():
                flagged = st[tid].verdict in (CONFIRMED, SUSPECTED)
                tp += flagged and t == "flaky"; fp += flagged and t != "flaky"; fn += (not flagged) and t == "flaky"
        recall = tp / (tp + fn)
        print(f"\n[no-retry ground truth] fp={fp} recall={recall:.3f}")
        self.assertEqual(fp, 0)
        self.assertGreaterEqual(recall, 0.65)  # measured 0.71 over 20 seeds; rare flakes are missed
