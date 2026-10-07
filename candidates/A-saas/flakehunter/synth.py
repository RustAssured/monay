"""Synthetic CI history with KNOWN ground truth, to measure detector accuracy."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Dict, List, Tuple
from xml.sax.saxutils import quoteattr


def _case(cls, name, outcome, t):
    body = {"pass": "", "fail": "<failure message=\"assert\">boom</failure>",
            "skip": "<skipped/>"}[outcome]
    return f"<testcase classname={quoteattr(cls)} name={quoteattr(name)} time=\"{t:.3f}\">{body}</testcase>"


def generate(n_tests=200, n_commits=30, n_flaky=10, n_broken=2, retry_on_fail=True,
             seed=0) -> Tuple[List[Tuple[str, str]], Dict[str, str]]:
    """Return ([(run_id, xml)], truth{test_id: 'flaky'|'broken'|'stable'}).

    CI policy modelled: each commit runs once; if any test fails the job is
    retried once on the same commit (common `rerun failed jobs` practice).
    Broken tests start failing permanently at a random commit in the last third.
    """
    rng = random.Random(seed)
    tests = [(f"pkg.mod{i // 20}", f"test_{i}") for i in range(n_tests)]
    ids = [f"{c}::{n}" for c, n in tests]
    idx = list(range(n_tests)); rng.shuffle(idx)
    flaky = {ids[i]: rng.uniform(0.05, 0.35) for i in idx[:n_flaky]}
    broken = {ids[i]: rng.randint(2 * n_commits // 3, n_commits - 3) for i in idx[n_flaky:n_flaky + n_broken]}
    truth = {t: "flaky" if t in flaky else "broken" if t in broken else "stable" for t in ids}

    runs = []
    for c in range(n_commits):
        sha = f"{rng.getrandbits(40):010x}"
        attempt = 0
        while True:
            cases, any_fail = [], False
            for (cls, name), tid in zip(tests, ids):
                if tid in flaky:
                    o = "fail" if rng.random() < flaky[tid] else "pass"
                elif tid in broken and c >= broken[tid]:
                    o = "fail"
                else:
                    o = "pass"
                any_fail |= o == "fail"
                cases.append(_case(cls, name, o, rng.uniform(0.001, 0.5)))
            xml = (f"<testsuites commit=\"{sha}\"><testsuite name=\"suite\" tests=\"{n_tests}\">"
                   + "".join(cases) + "</testsuite></testsuites>")
            runs.append((f"run{c:03d}_{attempt}", xml))
            attempt += 1
            if not (retry_on_fail and any_fail and attempt < 2):
                break
    return runs, truth


def write(dirpath, **kw) -> Dict[str, str]:
    d = Path(dirpath); d.mkdir(parents=True, exist_ok=True)
    runs, truth = generate(**kw)
    for rid, xml in runs:
        (d / f"{rid}.xml").write_text(xml)
    return truth
