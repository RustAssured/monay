"""Static site + static JSON API generator (no server needed: GitHub Pages / Cloudflare Pages / any CDN)."""
from __future__ import annotations

import csv
import html
import re
import json
from pathlib import Path

from .scoring import METHOD_VERSION

COLORS = {"low": "#2e7d32", "medium": "#b26a00", "high": "#c62828", "critical": "#6a1b9a"}


def badge_svg(label: str, value: str, color: str) -> str:
    lw, vw = 6 * len(label) + 12, 6 * len(value) + 12
    w = lw + vw
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">'
            f'<rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/>'
            f'<g fill="#fff" font-family="Verdana,sans-serif" font-size="11">'
            f'<text x="6" y="14">{html.escape(label)}</text><text x="{lw + 6}" y="14">{html.escape(value)}</text></g></svg>')


def _safe(name: str) -> str:
    """Filesystem/URL-safe slug; never allows path traversal."""
    s = name.replace("/", "__").replace("@", "_at_")
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s).lstrip(".")
    return s or "_"


def write_site(out: Path, scores: list[dict], changes: list[dict], now) -> None:
    api = out / "api" / "v1"
    api.mkdir(parents=True, exist_ok=True)
    ok = sorted((s for s in scores if not s.get("error")), key=lambda s: (s["health"], s["name"]))
    meta = {"generated_at": now.replace(microsecond=0).isoformat(), "method": METHOD_VERSION,
            "count": len(ok), "sources": ["https://pypi.org/pypi/<name>/json", "https://registry.npmjs.org/<name>"],
            "license": "Scores: CC-BY-4.0. Underlying metadata belongs to the respective registries/authors."}
    (api / "index.json").write_text(json.dumps({"meta": meta, "packages": ok}, indent=1))
    (api / "changes.json").write_text(json.dumps({"meta": meta, "changes": changes}, indent=1))
    for s in ok:
        p = api / _safe(s["ecosystem"]) / f"{_safe(s['name'])}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=1))
        b = api / "badge" / _safe(s["ecosystem"]) / f"{_safe(s['name'])}.svg"
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(badge_svg("dep health", f"{s['health']:.0f} {s['abandonment_risk']}", COLORS[s["abandonment_risk"]]))
    with open(out / "scores.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ecosystem", "name", "version", "health", "abandonment_risk", "license_class", "license_raw",
                    "days_since_release", "releases_last_year", "flags"])
        for s in ok:
            w.writerow([s["ecosystem"], s["name"], s["version"], s["health"], s["abandonment_risk"],
                        s["license_class"], s["license_raw"], s["days_since_release"], s["releases_last_year"],
                        ";".join(s["flags"])])
    rows = "\n".join(
        f'<tr><td>{html.escape(s["ecosystem"])}</td><td><a href="api/v1/{_safe(s["ecosystem"])}/{_safe(s["name"])}.json">'
        f'{html.escape(s["name"])}</a></td><td>{html.escape(str(s["version"]))}</td>'
        f'<td data-v="{s["health"]}">{s["health"]:.0f}</td>'
        f'<td><span class="r" style="background:{COLORS[s["abandonment_risk"]]}">{s["abandonment_risk"]}</span></td>'
        f'<td>{html.escape(s["license_class"])}</td><td>{s["days_since_release"]}</td>'
        f'<td>{html.escape("; ".join(s["reasons"]) or "-")}</td></tr>' for s in ok)
    ch = "".join(f"<li><b>{html.escape(c['ecosystem'])}:{html.escape(c['name'])}</b> - "
                 f"{html.escape('; '.join(c['events']))}</li>" for c in changes) or "<li>No changes since last run.</li>"
    (out / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Dependency Health Index</title>
<style>body{{font-family:system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px;background:#fff;color:#222}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}
th{{cursor:pointer;background:#f4f4f4}}.r{{color:#fff;padding:2px 6px;border-radius:4px}}.wrap{{overflow-x:auto}}</style></head><body>
<h1>Dependency Health Index</h1>
<p>Abandonment and license-risk triage for {len(ok)} popular PyPI and npm packages, rebuilt automatically from public
registry metadata. Generated {meta['generated_at']} &middot; method {METHOD_VERSION} &middot;
<a href="api/v1/index.json">JSON API</a> &middot; <a href="scores.csv">CSV</a> &middot; <a href="api/v1/changes.json">changes</a>.</p>
<p><small>Scores are heuristic engineering signals, not legal advice or a security audit. A low score on a
finished, stable library can be a false positive; every deduction is explained. Maintainers can request corrections
via the issue tracker.</small></p>
<h2>Changes since previous run</h2><ul>{ch}</ul>
<h2>All packages (lowest health first)</h2><div class="wrap"><table id="t"><thead><tr><th>eco</th><th>package</th><th>version</th>
<th>health</th><th>risk</th><th>license class</th><th>days since release</th><th>why</th></tr></thead><tbody>
{rows}</tbody></table></div>
<script>document.querySelectorAll('#t th').forEach((th,i)=>th.onclick=()=>{{const tb=th.closest('table').tBodies[0];
const rs=[...tb.rows];const num=r=>{{const c=r.cells[i];return c.dataset.v!==undefined?+c.dataset.v:(isNaN(+c.textContent)?c.textContent:+c.textContent)}};
const dir=th.dataset.d=th.dataset.d==='1'?'-1':'1';rs.sort((a,b)=>(num(a)>num(b)?1:-1)*dir);rs.forEach(r=>tb.appendChild(r));}});</script>
</body></html>""")
