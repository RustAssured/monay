"""Parse dependency manifests into (ecosystem, name) pairs."""
from __future__ import annotations

import json
import re
from pathlib import Path

_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def canon_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:")):
            continue
        m = _REQ.match(line)
        if m:
            out.append(("pypi", canon_pypi(m.group(1))))
    return sorted(set(out))


def parse_package_json(text: str, include_dev: bool = True) -> list[tuple[str, str]]:
    d = json.loads(text)
    names = set(d.get("dependencies") or {})
    if include_dev:
        names |= set(d.get("devDependencies") or {})
    return sorted(("npm", n) for n in names)


def parse_manifest(path: str | Path) -> list[tuple[str, str]]:
    p = Path(path)
    text = p.read_text()
    if p.name == "package.json":
        return parse_package_json(text)
    if p.suffix in (".txt", ".in") or "requirements" in p.name:
        return parse_requirements(text)
    raise ValueError(f"unsupported manifest: {p.name}")
