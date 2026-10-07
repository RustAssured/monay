"""CLI.
  python -m depscore build  --seeds seeds/seeds.txt --cache data/cache --out site [--offline] [--github]
  python -m depscore scan   requirements.txt|package.json [--cache data/cache] [--offline] [--policy p.json] [--json]
  python -m depscore digest requirements.txt --customer acme [--site site] [--policy p.json]
  python -m depscore econ   [--out ECONOMICS.md]
"""
from __future__ import annotations

import argparse
import json
import sys

from .http import RecordCache


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="depscore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--seeds", default="seeds/seeds.txt")
    b.add_argument("--cache", default="data/cache")
    b.add_argument("--out", default="site")
    b.add_argument("--offline", action="store_true")
    b.add_argument("--github", action="store_true", help="enrich with GitHub REST (needs GITHUB_TOKEN)")
    s = sub.add_parser("scan")
    s.add_argument("manifest")
    s.add_argument("--cache", default="data/cache")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--policy")
    s.add_argument("--json", action="store_true")
    d = sub.add_parser("digest")
    d.add_argument("manifest")
    d.add_argument("--customer", default="customer")
    d.add_argument("--site", default="site")
    d.add_argument("--policy")
    e = sub.add_parser("econ")
    e.add_argument("--out", default="ECONOMICS.md")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        from .pipeline import build
        print(json.dumps(build(a.seeds, a.cache, a.out, offline=a.offline, github=a.github)))
        return 0
    if a.cmd == "scan":
        from .manifest import parse_manifest
        from .pipeline import collect_scores
        from .policy import evaluate
        pairs = parse_manifest(a.manifest)
        scores = collect_scores(pairs, RecordCache(a.cache), offline=a.offline)
        pol = json.load(open(a.policy)) if a.policy else None
        res = evaluate(scores, pol)
        if a.json:
            print(json.dumps({"scores": scores, "result": res}, indent=1))
        else:
            for sc in sorted(scores, key=lambda x: x.get("health", -1)):
                if sc.get("error"):
                    print(f"  ??  {sc['ecosystem']}:{sc['name']}  ERROR {sc['error']}")
                else:
                    print(f"  {sc['health']:5.1f} {sc['abandonment_risk']:8s} {sc['license_class']:16s} "
                          f"{sc['ecosystem']}:{sc['name']}")
            for k, m in res["warnings"]:
                print(f"WARN {k}: {m}")
            for k, m in res["failures"]:
                print(f"FAIL {k}: {m}")
            print("PASSED" if res["passed"] else "POLICY FAILED")
        return 0 if res["passed"] else 1
    if a.cmd == "digest":
        from pathlib import Path
        from .digest import make_digest
        from .manifest import parse_manifest
        api = Path(a.site) / "api" / "v1"
        idx = json.loads((api / "index.json").read_text())
        ch = json.loads((api / "changes.json").read_text())["changes"]
        pol = json.load(open(a.policy)) if a.policy else None
        print(make_digest(a.customer, parse_manifest(a.manifest), idx["packages"], ch, pol, idx["meta"]["generated_at"]))
        return 0
    if a.cmd == "econ":
        from .econ import write_report
        write_report(a.out)
        print(f"wrote {a.out}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
