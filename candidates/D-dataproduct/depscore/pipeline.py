"""End-to-end pipeline: seeds -> fetch (live or cache) -> score -> diff vs previous -> publish static site/API."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import collectors
from .http import FetchError, RecordCache
from .scoring import score


def read_seeds(path: str | Path) -> list[tuple[str, str]]:
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            eco, name = line.split(":", 1)
            out.append((eco.strip(), name.strip()))
    return out


def get_record(eco: str, name: str, cache: RecordCache, offline: bool, github: bool = False) -> dict:
    if offline:
        rec = cache.get(eco, name)
        if rec is None:
            raise FetchError(f"no cached record for {eco}:{name}")
        return rec
    try:
        rec = collectors.FETCHERS[eco](name)
        if github:
            slug = collectors.github_slug(rec.get("repo_url"))
            if slug:
                gh = collectors.fetch_github(slug)
                if gh:
                    rec["github"] = gh
        cache.put(eco, name, rec)
        return rec
    except FetchError:
        rec = cache.get(eco, name)  # graceful degradation: serve last known good
        if rec is None:
            raise
        rec["stale"] = True
        return rec


def collect_scores(pairs, cache: RecordCache, offline=False, github=False, now=None, workers=8) -> list[dict]:
    def one(p):
        eco, name = p
        try:
            return score(get_record(eco, name, cache, offline, github), now=now)
        except FetchError as e:
            return {"ecosystem": eco, "name": name, "error": str(e)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, pairs))


def diff(prev: list[dict], cur: list[dict]) -> list[dict]:
    """Changes worth alerting on: risk level worsened, new flags, license class changed."""
    pm = {(s["ecosystem"], s["name"]): s for s in prev if not s.get("error")}
    order = ["low", "medium", "high", "critical"]
    changes = []
    for s in cur:
        if s.get("error"):
            continue
        p = pm.get((s["ecosystem"], s["name"]))
        if not p:
            continue
        ev = []
        if order.index(s["abandonment_risk"]) > order.index(p["abandonment_risk"]):
            ev.append(f"risk {p['abandonment_risk']} -> {s['abandonment_risk']}")
        new_flags = sorted(set(s["flags"]) - set(p["flags"]))
        if new_flags:
            ev.append("new flags: " + ", ".join(new_flags))
        if s["license_class"] != p["license_class"]:
            ev.append(f"license {p['license_class']} -> {s['license_class']}")
        if ev:
            changes.append({"ecosystem": s["ecosystem"], "name": s["name"], "events": ev})
    return changes


def build(seeds_path, cache_dir, out_dir, offline=False, github=False, now=None) -> dict:
    from .site import write_site

    now = now or datetime.now(timezone.utc)
    cache = RecordCache(cache_dir)
    pairs = read_seeds(seeds_path)
    scores = collect_scores(pairs, cache, offline=offline, github=github, now=now)
    out = Path(out_dir)
    prev_path = out / "api" / "v1" / "index.json"
    prev = json.loads(prev_path.read_text())["packages"] if prev_path.exists() else []
    changes = diff(prev, scores)
    write_site(out, scores, changes, now)
    ok = [s for s in scores if not s.get("error")]
    return {"total": len(scores), "ok": len(ok), "errors": len(scores) - len(ok), "changes": len(changes),
            "stale": sum(1 for s in ok if s.get("stale"))}
