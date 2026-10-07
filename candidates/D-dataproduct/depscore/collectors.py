"""Registry collectors. Each returns a *normalized record* (plain dict) so scoring is source-agnostic.

Sources (public, documented JSON APIs; we only read metadata and always link back):
  - PyPI JSON API: https://pypi.org/pypi/<name>/json
  - npm registry:  https://registry.npmjs.org/<name>
  - GitHub REST (optional enrichment, needs GITHUB_TOKEN in CI): https://api.github.com/repos/<o>/<r>
"""
from __future__ import annotations

import os
import re
import urllib.parse
from datetime import datetime, timezone

from .http import FetchError, get_json

_DEV_STATUS = re.compile(r"Development Status :: (\d)")
_GH = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def github_slug(url: str | None) -> str | None:
    if not url:
        return None
    m = _GH.search(url.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}".lower()


def parse_pypi(d: dict) -> dict:
    info = d.get("info", {})
    releases = d.get("releases", {}) or {}
    dates = []
    for files in releases.values():
        ups = [f.get("upload_time_iso_8601") or f.get("upload_time") for f in files if not f.get("yanked")]
        ups = [u for u in ups if u]
        if ups:
            dates.append(min(ups))
    dates = sorted(_norm_date(x) for x in dates)
    urls = d.get("urls") or []
    latest_date = None
    if urls:
        latest_date = _norm_date(min(u.get("upload_time_iso_8601") or u["upload_time"] for u in urls))
    elif dates:
        latest_date = dates[-1]
    classifiers = info.get("classifiers") or []
    dev = None
    for c in classifiers:
        m = _DEV_STATUS.match(c)
        if m:
            dev = int(m.group(1))
    lic_cls = [c for c in classifiers if c.startswith("License ::")]
    proj = info.get("project_urls") or {}
    candidates = list(proj.values()) + [info.get("home_page") or ""]
    repo = next((u for u in candidates if github_slug(u)), None) or next((u for u in candidates if u), None)
    lic = info.get("license_expression") or info.get("license") or ""
    return {
        "ecosystem": "pypi",
        "name": info.get("name"),
        "latest_version": info.get("version"),
        "latest_release": latest_date,
        "release_dates": dates,
        "license_raw": lic.strip()[:200],
        "license_classifiers": lic_cls,
        "deprecated": None,
        "dev_status": dev,
        "latest_yanked": bool(info.get("yanked")),
        "maintainers": None,  # PyPI JSON API does not expose maintainer list
        "repo_url": repo,
        "description": (info.get("summary") or "")[:200],
        "fetched_at": _now_iso(),
    }


def parse_npm(d: dict) -> dict:
    latest = (d.get("dist-tags") or {}).get("latest")
    versions = d.get("versions") or {}
    man = versions.get(latest, {}) if latest else {}
    time_ = d.get("time") or {}
    dates = sorted(_norm_date(v) for k, v in time_.items() if k not in ("created", "modified") and k in versions)
    lic = man.get("license") or d.get("license") or ""
    if isinstance(lic, dict):
        lic = lic.get("type", "")
    if isinstance(lic, list):
        lic = " OR ".join(x.get("type", "") if isinstance(x, dict) else str(x) for x in lic)
    repo = man.get("repository") or d.get("repository") or ""
    if isinstance(repo, dict):
        repo = repo.get("url", "")
    dep = man.get("deprecated")
    return {
        "ecosystem": "npm",
        "name": d.get("name"),
        "latest_version": latest,
        "latest_release": _norm_date(time_[latest]) if latest in time_ else (dates[-1] if dates else None),
        "release_dates": dates,
        "license_raw": str(lic).strip()[:200],
        "license_classifiers": [],
        "deprecated": (str(dep)[:300] if dep else None),
        "dev_status": None,
        "latest_yanked": False,
        "maintainers": len(d.get("maintainers") or man.get("maintainers") or []) or None,
        "repo_url": str(repo)[:300] or None,
        "description": (d.get("description") or "")[:200],
        "fetched_at": _now_iso(),
    }


def parse_github(d: dict) -> dict:
    return {
        "archived": bool(d.get("archived")),
        "pushed_at": _norm_date(d["pushed_at"]) if d.get("pushed_at") else None,
        "stars": d.get("stargazers_count"),
        "open_issues": d.get("open_issues_count"),
        "spdx": (d.get("license") or {}).get("spdx_id"),
    }


def _norm_date(s: str) -> str:
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def fetch_pypi(name: str) -> dict:
    return parse_pypi(get_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json"))


def fetch_npm(name: str) -> dict:
    enc = name.replace("/", "%2F") if name.startswith("@") else urllib.parse.quote(name)
    return parse_npm(get_json(f"https://registry.npmjs.org/{enc}"))


def fetch_github(slug: str) -> dict | None:
    tok = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        return parse_github(get_json(f"https://api.github.com/repos/{slug}", headers=headers, retries=0))
    except FetchError:
        return None


FETCHERS = {"pypi": fetch_pypi, "npm": fetch_npm}
