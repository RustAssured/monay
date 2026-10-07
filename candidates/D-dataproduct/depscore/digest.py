"""Per-customer watchlist digest (the paid 'Pro' deliverable): what changed in *your* dependencies this run,
plus everything currently failing your policy. Output is Markdown, posted as a GitHub issue in a
sponsors-only private repo (GitHub emails watchers) or sent by any mailer."""
from __future__ import annotations

from .policy import evaluate


def make_digest(customer: str, watch: list[tuple[str, str]], index: list[dict], changes: list[dict],
                policy: dict | None = None, generated_at: str = "") -> str:
    keys = set(watch)
    mine = [s for s in index if (s["ecosystem"], s["name"]) in keys or
            (s["ecosystem"], s["name"].lower().replace("_", "-").replace(".", "-")) in keys]
    found = {(s["ecosystem"], s["name"].lower().replace("_", "-").replace(".", "-")) for s in mine} | \
            {(s["ecosystem"], s["name"]) for s in mine}
    missing = sorted(k for k in keys if k not in found)
    names = {(s["ecosystem"], s["name"]) for s in mine}
    my_changes = [c for c in changes if (c["ecosystem"], c["name"]) in names]
    res = evaluate(mine, policy)
    L = [f"# Dependency digest for {customer}", "", f"Generated {generated_at}. Watching {len(keys)} packages "
         f"({len(mine)} scored, {len(missing)} not yet in index).", "", "## Changes since last run", ""]
    L += [f"- **{c['ecosystem']}:{c['name']}**: {'; '.join(c['events'])}" for c in my_changes] or ["- none"]
    L += ["", "## Policy failures", ""] + ([f"- {k}: {m}" for k, m in res["failures"]] or ["- none"])
    L += ["", "## Warnings", ""] + ([f"- {k}: {m}" for k, m in res["warnings"]] or ["- none"])
    L += ["", "## Lowest health", "", "| package | health | risk | license | why |", "|---|---|---|---|---|"]
    for s in sorted(mine, key=lambda s: s["health"])[:10]:
        L.append(f"| {s['ecosystem']}:{s['name']} | {s['health']:.0f} | {s['abandonment_risk']} | "
                 f"{s['license_class']} | {'; '.join(s['reasons'])[:160] or '-'} |")
    if missing:
        L += ["", "Not yet indexed (added to next run's seed list): " + ", ".join(f"{e}:{n}" for e, n in missing)]
    L += ["", "_Heuristic signals, not legal advice or a security audit._"]
    return "\n".join(L)
