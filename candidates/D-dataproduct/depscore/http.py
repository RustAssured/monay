"""Tiny HTTP client: stdlib only, polite User-Agent, retries, optional on-disk cache."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "depscore/0.1 (+https://github.com/OWNER/depscore; dependency health research)"


class FetchError(Exception):
    pass


def get_json(url: str, headers: dict | None = None, timeout: float = 30, retries: int = 2,
             max_bytes: int = 60_000_000) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise FetchError(f"response too large: {url}")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FetchError(f"404 {url}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"failed {url}: {last}")


class RecordCache:
    """Caches *normalized* records (small) keyed by ecosystem/name. Used as test fixtures too."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def _path(self, eco: str, name: str) -> Path:
        safe = name.replace("/", "__").replace("@", "_at_")
        return self.root / eco / f"{safe}.json"

    def get(self, eco: str, name: str) -> dict | None:
        p = self._path(eco, name)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def put(self, eco: str, name: str, rec: dict) -> None:
        p = self._path(eco, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, indent=1, sort_keys=True))


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]
