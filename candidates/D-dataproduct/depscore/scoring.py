"""Transparent, rule-based health scoring. Every point deducted comes with a human-readable reason.

health (0-100, higher is better) = weighted sum of components, then hard caps for explicit
abandonment signals. Weights are deliberately simple so users can audit and override them.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .licenses import classify_record

WEIGHTS = {"recency": 0.40, "cadence": 0.25, "maturity": 0.15, "provenance": 0.10, "bus_factor": 0.10}
METHOD_VERSION = "2026.10-1"


def _days(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    return (now - datetime.fromisoformat(iso)).total_seconds() / 86400


def recency_score(days: float | None) -> float:
    if days is None:
        return 0
    if days <= 365:
        return 100
    if days <= 730:
        return 70
    if days <= 1460:
        return 40
    return 15


def cadence_score(n_last_year: int) -> float:
    return {0: 20, 1: 60}.get(n_last_year, 85 if n_last_year <= 5 else 100)


def maturity_score(n_releases: int, age_days: float | None) -> float:
    if not age_days or n_releases == 0:
        return 0
    s = 0
    s += 50 if age_days >= 730 else 25 if age_days >= 180 else 5
    s += 50 if n_releases >= 10 else 30 if n_releases >= 3 else 10
    return s


def score(rec: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    flags: list[str] = []
    dates = rec.get("release_dates") or []
    d_latest = _days(rec.get("latest_release"), now)
    d_first = _days(dates[0], now) if dates else None
    n_year = sum(1 for d in dates if (_days(d, now) or 1e9) <= 365)

    comp = {
        "recency": recency_score(d_latest),
        "cadence": cadence_score(n_year),
        "maturity": maturity_score(len(dates), d_first),
        "provenance": 100 if rec.get("repo_url") else 0,
        "bus_factor": 60,  # neutral when unknown (PyPI does not expose maintainers)
    }
    m = rec.get("maintainers")
    if m is not None:
        comp["bus_factor"] = 30 if m <= 1 else 70 if m == 2 else 100
        if m <= 1:
            flags.append("single_maintainer")
            reasons.append("Only one registry maintainer (bus-factor risk).")
    if d_latest is not None and d_latest > 730:
        reasons.append(f"Last release {int(d_latest)} days ago.")
    if n_year == 0:
        reasons.append("No releases in the past 12 months.")
    if not rec.get("repo_url"):
        flags.append("no_source_link")
        reasons.append("No source repository link in registry metadata.")

    health = sum(WEIGHTS[k] * v for k, v in comp.items())
    gh = rec.get("github") or {}
    d_push = _days(gh.get("pushed_at"), now)
    if d_push is not None and d_push <= 180 and comp["recency"] < 100:
        health = min(100, health + 10)
        reasons.append("Repository has recent commits (+10).")

    cap = 100
    if rec.get("deprecated"):
        flags.append("deprecated")
        reasons.append(f"Marked deprecated on registry: {rec['deprecated'][:120]}")
        cap = min(cap, 10)
    if rec.get("dev_status") == 7:
        flags.append("inactive_classifier")
        reasons.append("Trove classifier 'Development Status :: 7 - Inactive'.")
        cap = min(cap, 15)
    if gh.get("archived"):
        flags.append("archived_repo")
        reasons.append("Source repository is archived (read-only).")
        cap = min(cap, 15)
    if rec.get("latest_yanked"):
        flags.append("latest_yanked")
        reasons.append("Latest version is yanked.")
        cap = min(cap, 40)
    health = round(min(health, cap), 1)

    if health >= 75:
        risk = "low"
    elif health >= 55:
        risk = "medium"
    elif health >= 30:
        risk = "high"
    else:
        risk = "critical"
    out = {
        "ecosystem": rec["ecosystem"],
        "name": rec["name"],
        "version": rec.get("latest_version"),
        "health": health,
        "abandonment_risk": risk,
        "components": comp,
        "flags": flags,
        "reasons": reasons,
        "days_since_release": None if d_latest is None else int(d_latest),
        "releases_last_year": n_year,
        "n_releases": len(dates),
        "repo_url": rec.get("repo_url"),
        "license_raw": ((rec.get("license_raw") or "").splitlines() or [""])[0][:80],
        "method": METHOD_VERSION,
        "fetched_at": rec.get("fetched_at"),
    }
    out.update(classify_record(rec))
    if out["license_class"] in ("network_copyleft", "source_available", "unknown"):
        out["flags"].append(f"license_{out['license_class']}")
    return out
