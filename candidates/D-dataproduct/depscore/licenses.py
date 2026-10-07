"""License normalisation and risk classification.

Risk classes (ordered least -> most restrictive for a typical proprietary/SaaS distributor):
  permissive < weak_copyleft < strong_copyleft < network_copyleft < source_available < unknown
This is an *engineering triage signal*, not legal advice.
"""
from __future__ import annotations

import re

ORDER = ["permissive", "weak_copyleft", "strong_copyleft", "network_copyleft", "source_available", "unknown"]
RANK = {c: i for i, c in enumerate(ORDER)}

SPDX = {
    "permissive": ["MIT", "MIT-0", "ISC", "0BSD", "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear", "Apache-2.0",
                   "Unlicense", "CC0-1.0", "Zlib", "Python-2.0", "PSF-2.0", "TCL", "AFL-2.1", "AFL-3.0", "MirOS", "curl", "ZPL-2.1", "ZPL-2.0", "Artistic-1.0-Perl", "BlueOak-1.0.0", "WTFPL", "BSL-1.0",
                   "X11", "HPND", "PostgreSQL", "CNRI-Python", "MIT-CMU", "NCSA", "UPL-1.0", "Artistic-2.0"],
    "weak_copyleft": ["LGPL-2.0", "LGPL-2.1", "LGPL-3.0", "MPL-1.1", "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0",
                      "CDDL-1.1", "EUPL-1.1", "EUPL-1.2", "CC-BY-4.0", "CC-BY-3.0", "OFL-1.1"],
    "strong_copyleft": ["GPL-2.0", "GPL-3.0", "CC-BY-SA-4.0"],
    "network_copyleft": ["AGPL-3.0", "OSL-3.0"],
    "source_available": ["SSPL-1.0", "BUSL-1.1", "Elastic-2.0", "Commons-Clause", "CC-BY-NC-4.0", "PolyForm-Noncommercial-1.0.0"],
}
_LOOKUP = {k.lower(): cls for cls, ks in SPDX.items() for k in ks}

ALIASES = {
    "mit license": "MIT", "the mit license": "MIT", "mit/x11": "MIT", "expat": "MIT",
    "bsd": "BSD-3-Clause", "bsd license": "BSD-3-Clause", "new bsd": "BSD-3-Clause", "new bsd license": "BSD-3-Clause",
    "3-clause bsd": "BSD-3-Clause", "bsd-3": "BSD-3-Clause", "simplified bsd": "BSD-2-Clause", "bsd-2": "BSD-2-Clause",
    "apache 2.0": "Apache-2.0", "apache-2": "Apache-2.0", "apache license 2.0": "Apache-2.0", "apache 2": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0", "apache software license": "Apache-2.0", "asl 2.0": "Apache-2.0",
    "apache license version 2.0": "Apache-2.0", "apache": "Apache-2.0",
    "psf": "PSF-2.0", "artistic license": "Artistic-2.0", "3-clause bsd license": "BSD-3-Clause", "apache 2.0 license": "Apache-2.0", "bsd 3-clause license": "BSD-3-Clause", "bsd 3-clause": "BSD-3-Clause", "bsd-3-clause license": "BSD-3-Clause", "mit-license": "MIT", "psf license": "PSF-2.0", "python software foundation license": "PSF-2.0",
    "isc license": "ISC", "isc license (iscl)": "ISC", "public domain": "Unlicense", "the unlicense": "Unlicense",
    "mpl 2.0": "MPL-2.0", "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0", "mpl-2": "MPL-2.0",
    "lgpl": "LGPL-3.0", "lgplv3": "LGPL-3.0", "lgplv2.1": "LGPL-2.1", "lgpl-2.1+": "LGPL-2.1",
    "gpl": "GPL-3.0", "gnu gpl": "GPL-3.0", "gnu lgpl": "LGPL-3.0", "lgplv2": "LGPL-2.1", "agplv3": "AGPL-3.0", "gplv3": "GPL-3.0", "gplv2": "GPL-2.0", "gpl-3": "GPL-3.0", "gpl-2": "GPL-2.0",
    "agpl": "AGPL-3.0", "agplv3": "AGPL-3.0", "sspl": "SSPL-1.0", "dual license": None, "unlicensed": None,
}

CLASSIFIER_MAP = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Artistic License": "Artistic-2.0",
    "License :: OSI Approved :: Zope Public License": "ZPL-2.1",
    "License :: OSI Approved :: MIT No Attribution License (MIT-0)": "MIT-0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)": "LGPL-2.1",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)": "GPL-2.0",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)": "GPL-3.0",
    "License :: OSI Approved :: GNU General Public License (GPL)": "GPL-3.0",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
    "License :: Public Domain": "Unlicense",
    "License :: OSI Approved :: zlib/libpng License": "Zlib",
    "License :: OSI Approved :: Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
}

_SUFFIX = re.compile(r"(-only|-or-later|\+)$", re.I)


def normalize_id(token: str) -> str | None:
    t = token.strip().strip("()").strip()
    if not t:
        return None
    low = re.sub(r"\s+", " ", t.lower())
    low = re.sub(r"^(gnu )?(a?gpl|lgpl) ?v? ?(\d)(\.\d)?\+?$", lambda m: f"{m.group(2)}v{m.group(3)}", low)
    if low in ALIASES:
        return ALIASES[low]
    base = _SUFFIX.sub("", t)
    if base.lower() in _LOOKUP:
        # return canonical-cased id
        for cls, ks in SPDX.items():
            for k in ks:
                if k.lower() == base.lower():
                    return k
    return None


def classify_id(spdx: str | None) -> str:
    if not spdx:
        return "unknown"
    return _LOOKUP.get(_SUFFIX.sub("", spdx).lower(), "unknown")


def classify_expression(expr: str) -> tuple[str, list[str]]:
    """Return (risk_class, normalized ids). OR -> least restrictive option; AND/WITH -> most restrictive."""
    if not expr:
        return "unknown", []
    if len(expr) > 120 or "\n" in expr:  # full license text pasted in metadata
        return _classify_text(expr)
    whole = normalize_id(expr)
    if whole:
        return classify_id(whole), [whole]
    e = re.sub(r"\s+WITH\s+[\w.-]+", "", expr, flags=re.I)
    or_parts = re.split(r"\s+OR\s+|\s*/\s*|\s+or\s+", e)
    best = None
    ids: list[str] = []
    for part in or_parts:
        and_parts = re.split(r"\s+AND\s+|\s+and\s+", part)
        worst = None
        for a in and_parts:
            nid = normalize_id(a)
            if nid:
                ids.append(nid)
            c = classify_id(nid)
            worst = c if worst is None or RANK[c] > RANK[worst] else worst
        best = worst if best is None or RANK[worst] < RANK[best] else best
    if best in (None, "unknown"):
        kw = _keywords(expr)
        if kw:
            return kw
    return best or "unknown", ids


_KW = [  # conservative order: most restrictive signal wins
    (r"affero|\bagpl", "AGPL-3.0"), (r"\bsspl|server side public", "SSPL-1.0"), (r"business source", "BUSL-1.1"),
    (r"lesser|\blgpl", "LGPL-3.0"), (r"\bgpl|general public license", "GPL-3.0"), (r"\bmpl|mozilla", "MPL-2.0"),
    (r"apache", "Apache-2.0"), (r"\bbsd\b", "BSD-3-Clause"), (r"\bmit\b", "MIT"), (r"\bisc\b", "ISC"),
]


def _keywords(expr: str):
    low = expr.lower()
    if re.search(r"commercial|proprietary|non-commercial|noncommercial", low) and not re.search(r"affero|agpl|gpl", low):
        return None
    for pat, sid in _KW:
        if re.search(pat, low):
            return classify_id(sid), [sid]
    return None


def _classify_text(text: str) -> tuple[str, list[str]]:
    t = text.lower()
    checks = [
        ("affero general public license", "AGPL-3.0"), ("server side public license", "SSPL-1.0"),
        ("lesser general public license", "LGPL-3.0"), ("gnu general public license", "GPL-3.0"),
        ("mozilla public license", "MPL-2.0"), ("apache license", "Apache-2.0"),
        ("permission is hereby granted, free of charge", "MIT"),
        ("redistribution and use in source and binary forms", "BSD-3-Clause"),
        ("permission to use, copy, modify, and/or distribute", "ISC"),
    ]
    for needle, sid in checks:
        if needle in t:
            return classify_id(sid), [sid]
    return "unknown", []


def classify_record(rec: dict) -> dict:
    cls, ids = classify_expression(rec.get("license_raw") or "")
    source = "metadata"
    if cls == "unknown":
        cids = [CLASSIFIER_MAP[c] for c in rec.get("license_classifiers") or [] if c in CLASSIFIER_MAP]
        if cids:
            # multiple classifiers usually mean "choose any" -> least restrictive
            ranked = sorted(cids, key=lambda s: RANK[classify_id(s)])
            cls, ids, source = classify_id(ranked[0]), cids, "classifier"
    gh = rec.get("github") or {}
    if cls == "unknown" and gh.get("spdx") and gh["spdx"] != "NOASSERTION":
        cls, ids, source = classify_id(gh["spdx"]), [gh["spdx"]], "github"
    return {"license_class": cls, "license_ids": sorted(set(ids)), "license_source": source}
