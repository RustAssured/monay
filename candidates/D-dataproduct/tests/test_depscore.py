import json
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from depscore import collectors, econ, pipeline
from depscore.__main__ import main
from depscore.http import FetchError, RecordCache
from depscore.licenses import classify_expression, classify_record
from depscore.manifest import parse_package_json, parse_requirements
from depscore.policy import evaluate
from depscore.scoring import score
from depscore.site import write_site

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc)


def rec(**kw):
    base = {"ecosystem": "pypi", "name": "x", "latest_version": "1.0", "latest_release": "2026-09-01T00:00:00+00:00",
            "release_dates": [f"20{y}-0{m}-01T00:00:00+00:00" for y in range(20, 27) for m in (1, 5, 9)],
            "license_raw": "MIT", "license_classifiers": [], "deprecated": None, "dev_status": 5,
            "latest_yanked": False, "maintainers": None, "repo_url": "https://github.com/a/x", "fetched_at": "now"}
    base.update(kw)
    return base


# ---------- collectors (parsers on recorded real payloads) ----------
def test_parse_pypi_real_payload():
    r = collectors.parse_pypi(json.loads((FIX / "raw" / "pypi_nose.json").read_text()))
    assert r["name"] == "nose" and r["ecosystem"] == "pypi"
    assert r["release_dates"] == sorted(r["release_dates"]) and len(r["release_dates"]) > 10
    assert r["latest_release"].startswith("2015")
    assert "LGPL" in r["license_raw"]


def test_parse_npm_real_payload():
    r = collectors.parse_npm(json.loads((FIX / "raw" / "npm_left-pad.json").read_text()))
    assert r["name"] == "left-pad" and r["deprecated"]
    assert r["license_raw"] == "WTFPL"
    assert r["latest_release"] == r["release_dates"][-1]


@pytest.mark.parametrize("url,slug", [
    ("https://github.com/psf/requests", "psf/requests"),
    ("git+https://github.com/expressjs/express.git", "expressjs/express"),
    ("git@github.com:foo/bar.git", "foo/bar"),
    ("https://github.com/a/b/tree/main/pkg", "a/b"),
    ("https://gitlab.com/a/b", None), (None, None)])
def test_github_slug(url, slug):
    assert collectors.github_slug(url) == slug


# ---------- licenses ----------
@pytest.mark.parametrize("expr,cls", [
    ("MIT", "permissive"), ("Apache-2.0 OR BSD-3-Clause", "permissive"), ("MIT OR GPL-3.0", "permissive"),
    ("MIT AND GPL-3.0-only", "strong_copyleft"), ("LGPL-2.1-or-later", "weak_copyleft"), ("GPL v3", "strong_copyleft"),
    ("AGPL-3.0", "network_copyleft"), ("SSPL", "source_available"), ("BUSL-1.1", "source_available"),
    ("Apache-2.0 WITH LLVM-exception", "permissive"), ("", "unknown"), ("Proprietary", "unknown"),
    ("MPL-2.0 AND MIT", "weak_copyleft"), ("BSD", "permissive"), ("3-Clause BSD License", "permissive"),
    ("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License", "network_copyleft"),
    ("Commercial", "unknown"), ("https://www.highcharts.com/license", "unknown"),
    ("Permission is hereby granted, free of charge, to any person " * 4, "permissive")])
def test_license_classes(expr, cls):
    assert classify_expression(expr)[0] == cls


def test_license_classifier_fallback():
    r = classify_record({"license_raw": "Dual License", "license_classifiers": [
        "License :: OSI Approved :: BSD License", "License :: OSI Approved :: Apache Software License"]})
    assert r["license_class"] == "permissive" and r["license_source"] == "classifier"


# ---------- scoring ----------
def test_active_package_low_risk():
    s = score(rec(), NOW)
    assert s["abandonment_risk"] == "low" and s["health"] >= 75 and s["flags"] == []


def test_deprecated_capped_critical():
    s = score(rec(deprecated="use y instead"), NOW)
    assert s["health"] <= 10 and s["abandonment_risk"] == "critical" and "deprecated" in s["flags"]


def test_inactive_classifier_and_archived():
    assert score(rec(dev_status=7), NOW)["health"] <= 15
    assert "archived_repo" in score(rec(github={"archived": True}), NOW)["flags"]


def test_stale_package_scores_lower_and_explains():
    old = rec(latest_release="2019-01-01T00:00:00+00:00",
              release_dates=["2017-01-01T00:00:00+00:00", "2019-01-01T00:00:00+00:00"], repo_url=None)
    s = score(old, NOW)
    assert s["health"] < 50 and "no_source_link" in s["flags"]
    assert any("days ago" in r for r in s["reasons"])


def test_score_monotonic_in_recency():
    hs = [score(rec(latest_release=d, release_dates=[d]), NOW)["health"]
          for d in ("2026-09-01T00:00:00+00:00", "2025-06-01T00:00:00+00:00", "2023-06-01T00:00:00+00:00",
                    "2018-01-01T00:00:00+00:00")]
    assert hs == sorted(hs, reverse=True)


def test_calibration_on_real_recorded_data():
    cache = RecordCache(FIX / "cache")
    for name in ("request", "left-pad", "node-sass", "tslint", "coffee-script"):
        assert score(cache.get("npm", name), NOW)["abandonment_risk"] == "critical", name
    for eco, name in (("pypi", "requests"), ("pypi", "django"), ("npm", "react"), ("npm", "express")):
        assert score(cache.get(eco, name), NOW)["abandonment_risk"] == "low", name
    assert score(cache.get("pypi", "fuzzywuzzy"), NOW)["license_class"] == "strong_copyleft"
    assert score(cache.get("npm", "highcharts"), NOW)["license_class"] == "unknown"


# ---------- pipeline / site ----------
def _seed(tmp_path, lines):
    p = tmp_path / "seeds.txt"
    p.write_text("# c\n" + "\n".join(lines) + "\n")
    return p


def test_offline_build_and_diff(tmp_path):
    cache_dir = tmp_path / "cache"
    shutil.copytree(FIX / "cache", cache_dir)
    seeds = _seed(tmp_path, ["pypi:requests", "npm:react", "npm:request", "pypi:does-not-exist"])
    out = tmp_path / "site"
    r = pipeline.build(seeds, cache_dir, out, offline=True, now=NOW)
    assert r == {"total": 4, "ok": 3, "errors": 1, "changes": 0, "stale": 0}
    idx = json.loads((out / "api/v1/index.json").read_text())
    assert idx["meta"]["count"] == 3
    assert (out / "api/v1/npm/react.json").exists() and (out / "api/v1/badge/npm/react.svg").exists()
    assert "Dependency Health Index" in (out / "index.html").read_text()
    assert len((out / "scores.csv").read_text().strip().splitlines()) == 4
    # simulate react getting deprecated upstream -> alert in changes feed
    rc = RecordCache(cache_dir)
    react = rc.get("npm", "react")
    react["deprecated"] = "moved"
    rc.put("npm", "react", react)
    r2 = pipeline.build(seeds, cache_dir, out, offline=True, now=NOW)
    assert r2["changes"] == 1
    ch = json.loads((out / "api/v1/changes.json").read_text())["changes"][0]
    assert ch["name"] == "react" and any("deprecated" in e for e in ch["events"])


def test_live_failure_falls_back_to_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    shutil.copytree(FIX / "cache", cache_dir)

    def boom(name):
        raise FetchError("network down")
    monkeypatch.setitem(collectors.FETCHERS, "pypi", boom)
    r = pipeline.get_record("pypi", "requests", RecordCache(cache_dir), offline=False)
    assert r["stale"] is True and r["name"] == "requests"


def test_html_is_escaped(tmp_path):
    s = score(rec(name="<script>alert(1)</script>"), NOW)
    write_site(tmp_path, [s], [], NOW)
    assert "<script>alert(1)" not in (tmp_path / "index.html").read_text()


# ---------- manifest + policy + CLI ----------
def test_manifest_parsing():
    assert parse_requirements("Requests>=2\n# c\n-r other.txt\nPyYAML==6 ; python_version>'3'\nzope.interface\n") == [
        ("pypi", "pyyaml"), ("pypi", "requests"), ("pypi", "zope-interface")]
    assert parse_package_json('{"dependencies":{"a":"1"},"devDependencies":{"@b/c":"2"}}') == [("npm", "@b/c"), ("npm", "a")]


def test_policy():
    good, bad = score(rec(), NOW), score(rec(name="y", license_raw="AGPL-3.0", deprecated="x"), NOW)
    assert evaluate([good])["passed"]
    res = evaluate([good, bad])
    assert not res["passed"] and len(res["failures"]) == 3
    assert evaluate([good, bad], {"allow": ["pypi:y"]})["passed"]


def test_cli_scan_exit_codes(tmp_path, capsys):
    ok = tmp_path / "requirements.txt"
    ok.write_text("requests\ndjango\n")
    assert main(["scan", str(ok), "--cache", str(FIX / "cache"), "--offline"]) == 0
    bad = tmp_path / "package.json"
    bad.write_text('{"dependencies": {"express": "^4", "request": "^2"}}')
    assert main(["scan", str(bad), "--cache", str(FIX / "cache"), "--offline"]) == 1
    assert "FAIL npm:request" in capsys.readouterr().out


# ---------- economics ----------
def test_break_even_is_consistent():
    a = econ.BASE
    n = econ.break_even_accounts(a)
    assert econ.monthly_profit(n, a, include_time=True, after_tax=False) >= 0
    assert econ.monthly_profit(n - 1, a, include_time=True, after_tax=False) < 0
    assert econ.break_even_accounts(a, include_time=False) == 1


def test_break_even_monotonic():
    grid = econ.sensitivity_grid()
    for row in grid:
        assert row[1:] == sorted(row[1:], reverse=True)       # higher price -> fewer accounts
    cols = list(zip(*grid))[1:]
    for c in cols:
        assert list(c) == sorted(c)                           # more hours -> more accounts


def test_negative_unit_margin_never_breaks_even():
    assert econ.break_even_accounts(replace(econ.BASE, price_pro=0.4, price_team=0.4)) >= 10**9


def test_monte_carlo_reproducible_and_sane():
    a, b = econ.monte_carlo(n=300, seed=1), econ.monte_carlo(n=300, seed=1)
    assert a == b
    assert 0 <= a["p_econ_positive"] <= a["p_cash_positive"] <= 1
    assert a["econ_p10"] <= a["econ_p50"] <= a["econ_p90"]


def test_econ_report_written(tmp_path):
    p = tmp_path / "E.md"
    txt = econ.write_report(str(p))
    assert "Cash break-even" in txt and p.exists()


@pytest.mark.skipif(os.environ.get("DEPSCORE_LIVE") != "1", reason="set DEPSCORE_LIVE=1 to hit live registries")
def test_live_registries():
    assert collectors.fetch_pypi("requests")["name"].lower() == "requests"
    assert collectors.fetch_npm("left-pad")["deprecated"]


def test_no_path_traversal(tmp_path):
    s = score(rec(name="../../evil"), NOW)
    write_site(tmp_path / "out", [s], [], NOW)
    assert not (tmp_path / "evil.json").exists()
    assert all(str(p).startswith(str(tmp_path / "out")) for p in (tmp_path).rglob("*evil*"))


def test_digest_for_customer(tmp_path, capsys):
    cache_dir = tmp_path / "cache"
    shutil.copytree(FIX / "cache", cache_dir)
    seeds = _seed(tmp_path, ["pypi:requests", "npm:express", "npm:request", "pypi:PyYAML"])
    out = tmp_path / "site"
    pipeline.build(seeds, cache_dir, out, offline=True, now=NOW)
    rc = RecordCache(cache_dir)
    e = rc.get("npm", "express")
    e["license_raw"] = "SSPL-1.0"
    rc.put("npm", "express", e)
    pipeline.build(seeds, cache_dir, out, offline=True, now=NOW)
    m = tmp_path / "package.json"
    m.write_text('{"dependencies": {"express": "1", "request": "1", "left-pad": "1"}}')
    assert main(["digest", str(m), "--customer", "acme", "--site", str(out)]) == 0
    txt = capsys.readouterr().out
    assert "digest for acme" in txt
    assert "license permissive -> source_available" in txt
    assert "npm:request: flag deprecated" in txt
    assert "npm:left-pad" in txt.split("Not yet indexed")[1]
    assert "pypi:requests" not in txt
