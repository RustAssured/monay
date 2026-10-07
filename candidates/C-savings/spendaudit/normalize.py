"""Merchant-descriptor normalisation and light categorisation.

Bank descriptors are noisy ("NETFLIX.COM 866-579-7172 CA", "SQ *BLUE BOTTLE #123 OAKLAND").
We reduce them to a stable merchant key so the same merchant groups together.
"""
from __future__ import annotations

import re

_PREFIXES = [
    r"^POS (DEBIT|PURCHASE)\s+", r"^DEBIT CARD PURCHASE\s+", r"^PURCHASE AUTHORIZED ON \d\d/\d\d\s+",
    r"^RECURRING PAYMENT AUTHORIZED ON \d\d/\d\d\s+", r"^CHECKCARD \d{4}\s+", r"^CHECKCARD\s+",
    r"^SQ \*", r"^SQ\*", r"^TST\* ?", r"^PAYPAL \*", r"^PP\*", r"^SP \* ?", r"^SP\*", r"^PY \*",
    r"^DD \*", r"^IN \*", r"^BT\*",
]
_ALIASES = [
    (r"^NETFLIX", "NETFLIX"),
    (r"^SPOTIFY", "SPOTIFY"),
    (r"^HULU|^HLU\*", "HULU"),
    (r"^DISNEY ?PLUS|^DISNEY\+|^DISNEYPLUS", "DISNEY PLUS"),
    (r"^(HBO ?MAX|MAX\.COM|WARNER ?MEDIA ?MAX)", "MAX"),
    (r"^APPLE\.COM/BILL|^APPLE COM BILL", "APPLE.COM/BILL"),
    (r"^AMAZON PRIME|^AMZN PRIME|^PRIME VIDEO", "AMAZON PRIME"),
    (r"^AMZN MKTP|^AMAZON\.COM|^AMAZON MKTPL|^AMZN\.COM", "AMAZON MARKETPLACE"),
    (r"^YOUTUBE ?PREMIUM|^GOOGLE \*YOUTUBE", "YOUTUBE PREMIUM"),
    (r"^GOOGLE \*GOOGLE ONE|^GOOGLE ONE", "GOOGLE ONE"),
    (r"^UBER (TRIP|\*TRIP)|^UBER\b", "UBER"),
    (r"^AUDIBLE", "AUDIBLE"),
    (r"^ADOBE", "ADOBE"),
    (r"^DROPBOX", "DROPBOX"),
    (r"^MICROSOFT|^MSFT", "MICROSOFT"),
    (r"^OPENAI|^CHATGPT", "OPENAI"),
    (r"^NYTIMES|^NYT ", "NYTIMES"),
]

_ALIAS_NAMES = {name for _, name in _ALIASES}
STREAMING = {"NETFLIX", "HULU", "DISNEY PLUS", "MAX", "PEACOCK", "PARAMOUNT PLUS", "YOUTUBE PREMIUM"}
# billers that front many unrelated products (an APPLE.COM/BILL line can be iCloud, Music, TV+ or an app)
AMBIGUOUS_BILLERS = {"APPLE.COM/BILL", "GOOGLE", "PAYPAL"}
MUSIC = {"SPOTIFY", "APPLE.COM/BILL", "YOUTUBE PREMIUM", "TIDAL", "PANDORA"}
CLOUD_STORAGE = {"DROPBOX", "GOOGLE ONE", "APPLE.COM/BILL", "MICROSOFT"}

_TRANSFER = re.compile(
    r"\b(PAYMENT THANK YOU|AUTOPAY|AUTO PAY|ONLINE PAYMENT|ONLINE TRANSFER|TRANSFER (TO|FROM)|"
    r"MOBILE PAYMENT|(MOBILE|ONLINE) PYMT|CARD PAYMENT|PAYMENT TO .*CARD|EPAY|CRCARDPMT|ACH PMT .*CARD|INTERNAL TRANSFER)\b")

_US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
              "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()


def merchant_key(description: str) -> str:
    s = description.upper().strip()
    s = re.sub(r"\s+", " ", s)
    for p in _PREFIXES:
        s = re.sub(p, "", s)
    for pat, name in _ALIASES:
        if re.search(pat, s):
            return name
    # drop anything after common separators carrying reference numbers
    s = re.sub(r"\*[A-Z0-9]{4,}", " ", s)
    s = re.sub(r"#\s*\d+", " ", s)
    s = re.sub(r"\b\d{3}[- .]?\d{3}[- .]?\d{4}\b", " ", s)    # phone numbers
    s = re.sub(r"\b[A-Z0-9]*\d[A-Z0-9]*\b", " ", s)            # tokens containing digits (store ids, refs)
    s = re.sub(r"[^A-Z&.'/ +]", " ", s)
    toks = [t for t in s.split() if t]
    # drop trailing 2-letter US state and a trailing city-ish token after it was removed
    while toks and toks[-1] in _US_STATES and len(toks) > 1:
        toks.pop()
    toks = toks[:3]  # first three tokens are the merchant in practice; the rest is usually location
    return " ".join(toks).strip(" .") or description.upper().strip()


def merchant_family(description: str) -> str:
    """Coarser grouping used for recurring detection: a merchant often bills under several descriptor
    variants ("PLANET FITNESS #1123" vs "PLANET FITNESS CLUB FEES"). Known aliases are kept as-is."""
    key = merchant_key(description)
    if key in _ALIAS_NAMES:
        return key
    toks = key.replace("*", " ").split()
    if not toks:
        return key
    return toks[0] if len(toks[0]) >= 5 else " ".join(toks[:2])


def is_transfer(description: str) -> bool:
    return bool(_TRANSFER.search(description.upper()))


FEE_PATTERNS = [
    ("overdraft", re.compile(r"\b(OVERDRAFT|OD ITEM|NSF|INSUFFICIENT FUNDS|RETURNED ITEM)\b.*\bFEE|\bOVERDRAFT\b|\bNSF FEE\b|\bOD FEE\b")),
    ("atm", re.compile(r"\bATM\b.*\bFEE\b|\bNON-?[A-Z ]*ATM\b.*FEE|\bATM SURCHARGE\b|\bATM WITHDRAWAL FEE\b")),
    ("fx", re.compile(r"\bFOREIGN (TRANSACTION|TRANS|EXCHANGE|CURRENCY)\b.*\bFEE\b|\bINTL? (TRANSACTION|TXN) FEE\b|\bFX FEE\b")),
    ("maintenance", re.compile(r"\b(MONTHLY|MAINTENANCE|SERVICE|ACCOUNT) (SERVICE |MAINTENANCE )?(FEE|CHARGE)\b")),
    ("late", re.compile(r"\bLATE (PAYMENT )?FEE\b")),
    ("interest", re.compile(r"\b(PURCHASE |CASH ADVANCE )?INTEREST CHARGE\b|\bINTEREST CHARGED\b|\bFINANCE CHARGE\b")),
    ("wire", re.compile(r"\bWIRE (TRANSFER )?FEE\b")),
]
_FEE_REVERSAL = re.compile(r"\b(REFUND|REVERSAL|REVERSED|WAIVED|WAIVER|REBATE|CREDIT)\b")


def fee_type(description: str, amount: float):
    """Return the fee category for an outflow that is a bank/card fee, else None."""
    if amount >= 0:
        return None
    s = description.upper()
    if _FEE_REVERSAL.search(s):
        return None
    for name, pat in FEE_PATTERNS:
        if pat.search(s):
            return name
    return None
