"""Parse JUnit XML (pytest, Jest-junit, Maven Surefire, Go gotestsum, etc.)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

PASS, FAIL, SKIP = "pass", "fail", "skip"


@dataclass(frozen=True)
class TestResult:
    test_id: str          # "<classname>::<name>"
    outcome: str          # pass | fail | skip
    duration: float
    run_id: str           # CI run identifier (file name or attribute)
    commit: Optional[str] = None
    timestamp: Optional[str] = None


def _outcome(case: ET.Element) -> str:
    tags = {child.tag for child in case}
    if "failure" in tags or "error" in tags:
        return FAIL
    if "skipped" in tags:
        return SKIP
    return PASS


def _iter_suites(root: ET.Element) -> Iterable[ET.Element]:
    if root.tag == "testsuite":
        yield root
    for s in root.iter("testsuite"):
        if s is not root:
            yield s


def parse_junit(source, run_id: Optional[str] = None, commit: Optional[str] = None) -> List[TestResult]:
    """Parse a JUnit XML file path or XML string into TestResults.

    Safe against entity-expansion: we reject DOCTYPE declarations outright
    (JUnit reports never need them).
    """
    if isinstance(source, Path) or (isinstance(source, str) and not source.lstrip().startswith("<")):
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        run_id = run_id or path.stem
    else:
        text = source
        run_id = run_id or "inline"
    if "<!DOCTYPE" in text.upper():
        raise ValueError("DOCTYPE not allowed in JUnit XML (XXE / billion-laughs guard)")
    root = ET.fromstring(text)
    commit = commit or root.get("commit")
    results: List[TestResult] = []
    seen_cases = set()
    suites = list(_iter_suites(root)) or [root]
    for suite in suites:
        ts = suite.get("timestamp")
        for case in suite.findall("testcase"):
            if id(case) in seen_cases:
                continue
            seen_cases.add(id(case))
            cls = case.get("classname") or suite.get("name") or ""
            name = case.get("name") or "<unnamed>"
            try:
                dur = float(case.get("time") or 0.0)
            except ValueError:
                dur = 0.0
            results.append(TestResult(f"{cls}::{name}" if cls else name, _outcome(case), dur,
                                      run_id, suite.get("commit") or commit, ts))
    return results
