"""CI policy gate: turn scores into pass/fail with explicit, configurable rules."""
from __future__ import annotations

DEFAULT_POLICY = {
    "min_health": 30,                       # fail below this (critical abandonment risk)
    "deny_license_classes": ["network_copyleft", "source_available"],
    "warn_license_classes": ["strong_copyleft", "unknown"],
    "deny_flags": ["deprecated"],
    "allow": [],                            # "eco:name" exceptions
}


def evaluate(scores: list[dict], policy: dict | None = None) -> dict:
    pol = {**DEFAULT_POLICY, **(policy or {})}
    allow = set(pol["allow"])
    failures, warnings = [], []
    for s in scores:
        key = f"{s['ecosystem']}:{s['name']}"
        if key in allow:
            continue
        if s.get("error"):
            warnings.append((key, f"could not fetch metadata: {s['error']}"))
            continue
        if s["health"] < pol["min_health"]:
            failures.append((key, f"health {s['health']} < {pol['min_health']}"))
        if s["license_class"] in pol["deny_license_classes"]:
            failures.append((key, f"license class {s['license_class']} ({s['license_raw'] or 'none'}) denied"))
        elif s["license_class"] in pol["warn_license_classes"]:
            warnings.append((key, f"license class {s['license_class']} ({s['license_raw'] or 'none'})"))
        for f in s["flags"]:
            if f in pol["deny_flags"]:
                failures.append((key, f"flag {f}"))
    return {"passed": not failures, "failures": failures, "warnings": warnings}
