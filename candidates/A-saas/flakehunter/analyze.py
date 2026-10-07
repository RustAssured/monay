"""Flakiness analysis over a history of test results.

Classification (per test, runs ordered as given):
  * confirmed_flaky : passed AND failed on the same commit (reruns/retries) -
                      the code did not change, the outcome did.
  * suspected_flaky : outcome flips between consecutive runs at least
                      `min_flip_rate` of the time and it has >=2 failures
                      and >=1 pass, but no same-commit proof.
  * broken          : the most recent `broken_streak` executed runs all failed
                      and the test is not confirmed flaky (a real regression).
  * stable          : everything else.
Wilson score interval on the failure rate is reported so users can see how much
evidence backs each verdict (small samples -> wide intervals).
"""
from __future__ import annotations

import math
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .junit import FAIL, PASS, TestResult

CONFIRMED, SUSPECTED, BROKEN, STABLE = "confirmed_flaky", "suspected_flaky", "broken", "stable"


def wilson_interval(failures: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    p = failures / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class TestStats:
    test_id: str
    runs: int = 0
    passes: int = 0
    failures: int = 0
    skips: int = 0
    flips: int = 0
    flip_rate: float = 0.0
    fail_rate: float = 0.0
    fail_rate_ci: tuple = (0.0, 1.0)
    same_commit_conflicts: List[str] = field(default_factory=list)
    total_duration: float = 0.0
    verdict: str = STABLE

    def to_dict(self):
        d = asdict(self)
        d["fail_rate_ci"] = [round(x, 4) for x in self.fail_rate_ci]
        d["flip_rate"] = round(self.flip_rate, 4)
        d["fail_rate"] = round(self.fail_rate, 4)
        d["total_duration"] = round(self.total_duration, 3)
        return d


def analyze(results: Iterable[TestResult], min_flip_rate: float = 0.2,
            broken_streak: int = 3) -> Dict[str, TestStats]:
    by_test: "OrderedDict[str, List[TestResult]]" = OrderedDict()
    for r in results:
        by_test.setdefault(r.test_id, []).append(r)

    out: Dict[str, TestStats] = {}
    for tid, rs in by_test.items():
        st = TestStats(tid)
        executed = [r for r in rs if r.outcome in (PASS, FAIL)]
        st.skips = len(rs) - len(executed)
        st.runs = len(executed)
        st.passes = sum(r.outcome == PASS for r in executed)
        st.failures = st.runs - st.passes
        st.total_duration = sum(r.duration for r in rs)
        st.flips = sum(1 for a, b in zip(executed, executed[1:]) if a.outcome != b.outcome)
        st.flip_rate = st.flips / (st.runs - 1) if st.runs > 1 else 0.0
        st.fail_rate = st.failures / st.runs if st.runs else 0.0
        st.fail_rate_ci = wilson_interval(st.failures, st.runs)

        per_commit = defaultdict(set)
        for r in executed:
            if r.commit:
                per_commit[r.commit].add(r.outcome)
        st.same_commit_conflicts = sorted(c for c, o in per_commit.items() if len(o) > 1)

        tail = executed[-broken_streak:]
        if st.same_commit_conflicts:
            st.verdict = CONFIRMED
        elif len(tail) == broken_streak and all(r.outcome == FAIL for r in tail):
            st.verdict = BROKEN
        elif st.failures >= 2 and st.passes >= 1 and st.flip_rate >= min_flip_rate:
            st.verdict = SUSPECTED
        else:
            st.verdict = STABLE
        out[tid] = st
    return out


def summarize(stats: Dict[str, TestStats]) -> dict:
    counts = {CONFIRMED: 0, SUSPECTED: 0, BROKEN: 0, STABLE: 0}
    for s in stats.values():
        counts[s.verdict] += 1
    flaky = [s for s in stats.values() if s.verdict in (CONFIRMED, SUSPECTED)]
    # CI minutes burnt re-running flaky tests: every failure of a flaky test
    # typically triggers a manual or automatic rerun of the job.
    wasted_failures = sum(s.failures for s in flaky)
    return {"tests": len(stats), "counts": counts, "flaky_failures": wasted_failures,
            "quarantine": sorted(s.test_id for s in flaky)}


def to_markdown(stats: Dict[str, TestStats], top: int = 20) -> str:
    rows = sorted((s for s in stats.values() if s.verdict != STABLE),
                  key=lambda s: ({CONFIRMED: 0, BROKEN: 1, SUSPECTED: 2}[s.verdict], -s.flip_rate, s.test_id))
    summ = summarize(stats)
    c = summ["counts"]
    lines = ["## flakehunter report", "",
             f"{summ['tests']} tests analysed: **{c[CONFIRMED]} confirmed flaky**, "
             f"{c[SUSPECTED]} suspected flaky, {c[BROKEN]} broken, {c[STABLE]} stable.", ""]
    if not rows:
        lines.append("No flaky or broken tests detected.")
        return "\n".join(lines) + "\n"
    lines += ["| verdict | test | runs | fail rate (95% CI) | flip rate | same-commit conflicts |",
              "|---|---|---|---|---|---|"]
    for s in rows[:top]:
        lo, hi = s.fail_rate_ci
        lines.append(f"| {s.verdict} | `{s.test_id}` | {s.runs} | {s.fail_rate:.0%} ({lo:.0%}-{hi:.0%}) | "
                     f"{s.flip_rate:.0%} | {len(s.same_commit_conflicts)} |")
    if len(rows) > top:
        lines.append(f"\n...and {len(rows) - top} more.")
    return "\n".join(lines) + "\n"
