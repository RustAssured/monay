"""Parsers for bank exports: OFX/QFX (SGML v1 and XML v2) and CSV layouts of common US banks.

CSV layouts supported (auto-detected from the header row):
  chase_checking  Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
  chase_credit    Transaction Date,Post Date,Description,Category,Type,Amount,Memo
  bofa            (summary preamble) then Date,Description,Amount,Running Bal.
  amex            Date,Description,Card Member,Account #,Amount   (charges positive -> negated)
  capital_one     Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
  wells_fargo     no header: "date","amount","*","","description"
  generic         any header with a date column, a description column and Amount or Debit/Credit
The layouts reflect the export formats as commonly documented by users; banks change them,
so the generic fallback exists and each layout is covered by a fixture test.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from datetime import date, datetime

from .models import Statement, Transaction

_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d %b %Y", "%b %d, %Y", "%Y%m%d")


def parse_date(s: str) -> date:
    s = s.strip().strip('"')
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {s!r}")


def parse_amount(s) -> float:
    if s is None:
        return 0.0
    s = str(s).strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    v = float(s)
    return -v if neg else v


def _rid(source: str, i: int, *parts) -> str:
    h = hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]
    return f"{os.path.basename(source)}:{i}:{h}"


# ---------------------------------------------------------------- OFX / QFX
def _ofx_tag(block: str, tag: str):
    m = re.search(rf"<{tag}>([^<\r\n]*)", block, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _ofx_date(s: str) -> date:
    return datetime.strptime(s[:8], "%Y%m%d").date()


def parse_ofx(text: str, source: str = "ofx") -> Statement:
    """Handles OFX 1.x SGML (unclosed leaf tags) and OFX 2.x XML, which are also what QFX files contain."""
    txs, balances = [], {}
    stmts = re.split(r"<(?:STMTRS|CCSTMTRS)>", text, flags=re.IGNORECASE)[1:] or [text]
    for si, stmt in enumerate(stmts):
        acct = _ofx_tag(stmt, "ACCTID") or f"acct{si}"
        acct_type = _ofx_tag(stmt, "ACCTTYPE") or ("CREDITCARD" if "CCACCTFROM" in stmt.upper() else "")
        account = f"{acct_type.lower() or 'account'}-{acct[-4:]}"
        for i, m in enumerate(re.finditer(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)", stmt,
                                          re.IGNORECASE | re.DOTALL)):
            b = m.group(1)
            name = _ofx_tag(b, "NAME") or ""
            memo = _ofx_tag(b, "MEMO") or ""
            desc = name if name else memo
            if name and memo and memo.upper() not in name.upper() and len(name) < 20:
                desc = f"{name} {memo}"
            fitid = _ofx_tag(b, "FITID") or _rid(source, i, b)
            txs.append(Transaction(
                date=_ofx_date(_ofx_tag(b, "DTPOSTED")), amount=parse_amount(_ofx_tag(b, "TRNAMT")),
                description=desc.replace("&amp;", "&"), account=account, txid=f"{account}:{fitid}",
                source=source, category_hint=(_ofx_tag(b, "TRNTYPE") or "")))
        lb = re.search(r"<LEDGERBAL>(.*?)(?:</LEDGERBAL>|<AVAILBAL>|$)", stmt, re.IGNORECASE | re.DOTALL)
        if lb:
            amt, dt = _ofx_tag(lb.group(1), "BALAMT"), _ofx_tag(lb.group(1), "DTASOF")
            if amt is not None:
                balances[account] = (_ofx_date(dt) if dt else None, parse_amount(amt))
    return Statement(transactions=txs, balances=balances, format="ofx")


# ---------------------------------------------------------------- CSV
def _norm(h: str) -> str:
    return re.sub(r"[^a-z#]", "", h.lower())


LAYOUTS = {
    "chase_checking": ["details", "postingdate", "description", "amount", "type", "balance"],
    "chase_credit": ["transactiondate", "postdate", "description", "category", "type", "amount"],
    "capital_one": ["transactiondate", "posteddate", "cardno", "description", "category", "debit", "credit"],
    "amex": ["date", "description", "cardmember", "account#", "amount"],
    "bofa": ["date", "description", "amount", "runningbal"],
}


def detect_layout(header: list) -> str:
    h = [_norm(c) for c in header]
    for name, cols in LAYOUTS.items():
        if all(c in h for c in cols):
            return name
    return "generic"


def _find_header(lines: list):
    """Returns (index_of_header_line or None). BofA puts a summary block above the header."""
    for i, line in enumerate(lines[:40]):
        row = next(csv.reader([line]), [])
        normed = [_norm(c) for c in row]
        if any(c in normed for c in ("date", "postingdate", "transactiondate", "posteddate")) and \
           any(c in normed for c in ("description", "payee", "name", "memo")):
            return i
    return None


def parse_csv(text: str, source: str = "csv", account: str | None = None) -> Statement:
    lines = [l for l in text.splitlines() if l.strip()]
    account = account or os.path.splitext(os.path.basename(source))[0]
    hidx = _find_header(lines)
    txs = []
    if hidx is None:
        # Wells Fargo style: no header, 5 columns: date, amount, *, check#, description
        for i, row in enumerate(csv.reader(lines)):
            if len(row) >= 5:
                try:
                    d = parse_date(row[0])
                except ValueError:
                    continue
                amt = parse_amount(row[1])
                txs.append(Transaction(d, amt, row[4].strip(), account, txid=_rid(source, i, *row), source=source))
        return Statement(txs, format="wells_fargo")

    reader = csv.reader(lines[hidx:])
    header = next(reader)
    layout = detect_layout(header)
    idx = {_norm(c): j for j, c in enumerate(header)}

    def col(row, *names):
        for n in names:
            if n in idx and idx[n] < len(row):
                return row[idx[n]]
        return None

    for i, row in enumerate(reader):
        if not any(c.strip() for c in row):
            continue
        dstr = col(row, "postingdate", "transactiondate", "date", "posteddate")
        try:
            d = parse_date(dstr or "")
        except ValueError:
            continue  # e.g. BofA "Beginning balance" rows without dates are skipped
        desc = (col(row, "description", "payee", "name", "memo") or "").strip()
        bal = None
        if layout == "capital_one" or (("debit" in idx or "credit" in idx) and "amount" not in idx):
            amt = parse_amount(col(row, "credit")) - parse_amount(col(row, "debit"))
        else:
            amt = parse_amount(col(row, "amount"))
            if layout == "amex":
                amt = -amt  # Amex exports charges as positive numbers
        if layout in ("chase_checking", "bofa") or "balance" in idx or "runningbal" in idx:
            b = col(row, "balance", "runningbal")
            bal = parse_amount(b) if b and b.strip() else None
        if layout == "bofa" and desc.lower().startswith("beginning balance"):
            continue
        txs.append(Transaction(d, amt, desc, account, balance=bal, txid=_rid(source, i, *row), source=source,
                               category_hint=col(row, "category") or ""))
    return Statement(txs, format=layout)


def load_file(path: str) -> Statement:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ofx", ".qfx") or "<OFX>" in text.upper()[:2000]:
        return parse_ofx(text, source=path)
    return parse_csv(text, source=path)


def merge_statements(statements: list) -> list:
    """Combine files; drop rows that appear in more than one file (overlapping exports) but keep
    identical rows within one file, because a genuine double charge looks exactly like that."""
    seen_by_key = {}
    out = []
    for si, st in enumerate(statements):
        local_counts = {}
        for t in st.transactions:
            key = (t.account, t.date, round(t.amount, 2), t.description.strip().upper())
            local_counts[key] = local_counts.get(key, 0) + 1
            prior = seen_by_key.get(key, {})
            already_from_other = max([c for s, c in prior.items() if s != si], default=0)
            seen_by_key.setdefault(key, {})[si] = local_counts[key]
            if local_counts[key] <= already_from_other:
                continue
            out.append(t)
    out.sort(key=lambda t: (t.date, t.account, t.txid))
    return out
