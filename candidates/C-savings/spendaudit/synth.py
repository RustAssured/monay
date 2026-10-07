"""Synthetic, labelled household transaction generator used to measure detector precision/recall.

Everything here is invented data. Patterns are modelled on what real exports look like (noisy descriptors,
store numbers, weekend shifts, variable utility bills, habitual same-price purchases, refunds, card payments
that are transfers, overdrafts that arise from balance timing) and deliberately include decoys that a naive
detector would mis-flag. Each transaction carries ground-truth labels in `Transaction.labels`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from .models import Transaction

START, END = date(2025, 1, 1), date(2026, 6, 30)

# name, price, cadence, descriptor variants, category
SUBSCRIPTIONS = [
    ("netflix", 15.49, "monthly", ["NETFLIX.COM 866-579-7172 CA", "NETFLIX.COM LOS GATOS CA", "Netflix.com"], "video"),
    ("hulu", 7.99, "monthly", ["HULU 877-8244858 CA", "HLU*HULUPLUS 888-265-6650"], "video"),
    ("disney", 13.99, "monthly", ["DISNEY PLUS 888-905-7888 CA", "DisneyPLUS BURBANK CA"], "video"),
    ("max", 16.99, "monthly", ["MAX.COM 855-442-6629 NY", "HBO MAX HBOMAX.COM NY"], "video"),
    ("spotify", 11.99, "monthly", ["SPOTIFY USA 8777781161", "Spotify P2C4F1A9B3 New York NY"], "music"),
    ("icloud", 2.99, "monthly", ["APPLE.COM/BILL 866-712-7753 CA"], "cloud"),
    ("applemusic", 10.99, "monthly", ["APPLE.COM/BILL 866-712-7753 CA"], "music"),
    ("prime_annual", 139.00, "annual", ["Amazon Prime*2K4LM8 Amzn.com/bill WA", "AMAZON PRIME*RT5QW1 AMZN.COM/BILLWA"], "shopping"),
    ("youtube", 13.99, "monthly", ["GOOGLE *YouTubePremium g.co/helppay#", "GOOGLE *YOUTUBE PREMIUM 650-2530000 CA"], "video"),
    ("gym", 39.99, "monthly", ["PLANET FITNESS #1123 800-7766", "PLANET FITNESS CLUB FEES"], "fitness"),
    ("nyt", 17.00, "monthly", ["NYTIMES*NYTimes Digital 800-698-4637", "NYT DIGITAL SUBSCRIPTION"], "news"),
    ("dropbox", 119.88, "annual", ["DROPBOX*7HG2KL9 DROPBOX.COM CA", "DROPBOX*QR83NM DB.TT/CC_HELP CA"], "cloud"),
    ("m365", 99.99, "annual", ["MICROSOFT*MICROSOFT 365 P MSBILL.INFO WA", "MSFT * E0300ABC12 MSBILL.INFO WA"], "software"),
    ("audible", 14.95, "monthly", ["AUDIBLE*2B4CX7 AMZN.COM/BILL NJ", "AUDIBLE US*1M2N3O amzn.com/bill"], "books"),
    ("chatgpt", 20.00, "monthly", ["OPENAI *CHATGPT SUBSCR OPENAI.COM CA", "OPENAI *CHATGPT SUBSCR"], "software"),
    ("mealkit", 69.99, "weekly", ["HELLOFRESH 646-846-3663 NY", "HELLOFRESH HELLOFRESH.CO NY"], "food"),
    ("adobe", 22.99, "monthly", ["ADOBE *CREATIVE CLOUD 408-536-6000 CA", "ADOBE *ADOBE 800-833-6687 CA"], "software"),
    ("carins", 612.00, "semiannual", ["GEICO *AUTO 800-841-3000 DC", "GEICO *AUTO WASHINGTON DC"], "insurance"),
    ("petins", 38.50, "monthly", ["LEMONADE INS PET 844-7336627 NY"], "insurance"),
    ("patreon", 5.00, "monthly", ["PATREON* MEMBERSHIP INTERNET CA", "Patreon* Membership"], "creator"),
]
GROCERS = ["SAFEWAY #{n} {c}", "TRADER JOE'S #{n} {c}", "WHOLEFDS MKT #{n} {c}", "KROGER #{n} {c}", "ALDI {n} {c}"]
RESTAURANTS = ["CHIPOTLE {n}", "SQ *TACOS EL GORDO", "TST* THE PIZZA PLACE", "SWEETGREEN {c}", "PANERA BREAD #{n}",
               "DD *DOORDASH THAITIME", "SQ *BAO HAUS", "OLIVE GARDEN {n}", "SHAKE SHACK {n}", "ATMOSPHERE BAR & GRILL",
               "TST* JOE'S DINER", "IN-N-OUT {c} {n}", "SQ *RAMEN NAKAMURA", "UBER *EATS PENDING", "NOODLES & CO {n}"]
SHOPS = ["TARGET {n} {c}", "AMZN Mktp US*{r}", "AMAZON.COM*{r} AMZN.COM/BILL WA", "WALGREENS #{n}", "HOME DEPOT #{n}",
         "BEST BUY {n}", "CVS/PHARMACY #{n}", "COSTCO WHSE #{n}", "IKEA {c}", "UNIQLO {c}"]
COFFEE = ["STARBUCKS STORE {n}", "SQ *BLUE BOTTLE COFFEE", "COFFEE BEAN & TEA LEAF {n}", "PEET'S COFFEE {n}"]
GAS = ["SHELL OIL {n}", "CHEVRON {n}", "76 - {n} {c}", "ARCO #{n}"]
CITIES = ["OAKLAND CA", "SAN JOSE CA", "PORTLAND OR", "AUSTIN TX", "DENVER CO", "SEATTLE WA"]
FOREIGN = ["HOTEL LISBOA CENTRO", "PINGO DOCE LISBOA", "RESTAURANTE O FADO", "CP COMBOIOS PORTUGAL", "MUSEU NACIONAL"]

# Fee descriptors as different banks word them. The last entry of each list is a HELD-OUT variant that the
# fee regexes were NOT written against, so fee recall on synthetic data is not trivially 100%.
FEE_TEXT = {
    "overdraft": ["OVERDRAFT ITEM FEE", "INSUFFICIENT FUNDS FEE", "NSF RETURNED ITEM FEE", "OD FEE", "PAID ITEM CHG"],
    "atm": ["NON-NETWORK ATM FEE-WITHDRAWAL", "ATM SURCHARGE FEE", "ATM WITHDRAWAL FEE", "OTHER BANK ATM CHARGE"],
    "fx": ["FOREIGN TRANSACTION FEE", "INTL TRANSACTION FEE", "FOREIGN CURRENCY CONVERSION FEE", "FOREIGN EXCH RT ADJ"],
    "maintenance": ["MONTHLY SERVICE FEE", "MONTHLY MAINTENANCE FEE", "SERVICE CHARGE", "ACCT ANALYSIS CHG"],
    "interest": ["PURCHASE INTEREST CHARGE", "INTEREST CHARGE ON PURCHASES", "FINANCE CHARGE", "INT CHG PURCH"],
}

STEP = {"weekly": 7, "monthly": None, "annual": None, "semiannual": None}


def _fmt(rng, tpl, city=None):
    return tpl.format(n=rng.randint(100, 9999), c=city or rng.choice(CITIES),
                      r="".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(6)))


def _add_months(d, k, day=None):
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    day = day or d.day
    for dd in (day, 30, 29, 28):
        try:
            return date(y, m, dd)
        except ValueError:
            continue


@dataclass
class Household:
    seed: int
    transactions: list
    truth: dict = field(default_factory=dict)


def generate_household(seed: int, start=START, end=END) -> Household:
    rng = random.Random(seed)
    fee_text = {k: rng.choice(v) for k, v in FEE_TEXT.items()}   # each household's bank uses one wording
    city = rng.choice(CITIES)
    profile = rng.choices(["lean", "comfortable", "idle"], [0.35, 0.45, 0.20])[0]
    monthly_income = rng.uniform(3200, 9000)
    events = []  # (date, account, amount, desc, labels)
    truth = {"series": {}, "creep": set(), "dup": set(), "fees": {}, "idle": profile == "idle", "profile": profile}
    sid_counter = [0]

    def ev(d, acct, amt, desc, **labels):
        if start <= d <= end:
            events.append([d, acct, round(amt, 2), desc, labels])

    def new_series(name, cadence, direction="out"):
        sid_counter[0] += 1
        sid = f"{seed}-{name}-{sid_counter[0]}"
        truth["series"][sid] = {"name": name, "cadence": cadence, "direction": direction}
        return sid

    # ---- income
    pay_sched = rng.choice(["semimonthly", "biweekly"])
    sid = new_series("payroll", pay_sched, "in")
    employer = rng.choice(["ACME CORP PAYROLL PPD ID: 9876543210", "GUSTO PAY 123456 PPD", "ADP WAGE PAY 774411"])
    if pay_sched == "semimonthly":
        d = date(start.year, start.month, 1)
        while d <= end:
            for day in (1, 15):
                pd = date(d.year, d.month, day)
                while pd.weekday() >= 5:
                    pd -= timedelta(days=1)
                ev(pd, "checking", monthly_income / 2 * rng.uniform(0.98, 1.02), employer, series=sid)
            d = _add_months(d, 1, 1)
    else:
        pd = start + timedelta(days=(4 - start.weekday()) % 7)
        while pd <= end:
            ev(pd, "checking", monthly_income * 12 / 26 * rng.uniform(0.98, 1.02), employer, series=sid)
            pd += timedelta(days=14)

    # ---- housing & utilities (checking)
    rent = monthly_income * rng.uniform(0.25, 0.38)
    sid = new_series("rent", "monthly")
    d = date(start.year, start.month, 1)
    while d <= end:
        ev(d, "checking", -rent, "BRIDGEWATER PROPERTY MGMT RENT WEB ID: 1234567", series=sid)
        d = _add_months(d, 1, 1)
    sid = new_series("electric", "monthly")
    base, day = rng.uniform(60, 160), rng.randint(5, 25)
    d = date(start.year, start.month, day)
    while d <= end:
        season = 1 + 0.18 * (1 if d.month in (1, 2, 7, 8, 12) else 0)
        ev(d + timedelta(days=rng.randint(0, 2)), "checking", -base * season * rng.uniform(0.85, 1.15),
           f"PG&E WEB ONLINE {rng.randint(10**5, 10**6)}", series=sid)
        d = _add_months(d, 1, day)
    for name, price, desc in (("internet", rng.choice([55.0, 70.0, 80.0]), "COMCAST XFINITY 800-266-2278"),
                              ("phone", rng.choice([45.0, 65.0, 90.0]), "VERIZON WIRELESS PAYMENTS")):
        sid = new_series(name, "monthly")
        day = rng.randint(3, 27)
        hike = rng.random() < 0.35
        hike_on = _add_months(start, rng.randint(5, 15), day)
        d = date(start.year, start.month, day)
        charged_after = False
        while d <= end:
            p = price
            if hike and d >= hike_on:
                p = round(price * rng.choice([1.07, 1.1, 1.15]) if not charged_after else p_new, 2)
                p_new = p
                charged_after = True
            ev(d, "checking", -p, desc, series=sid)
            d = _add_months(d, 1, day)
        if hike and charged_after:
            truth["creep"].add(sid)

    # ---- subscriptions (card)
    n_subs = rng.randint(3, 10)
    for name, price, cadence, variants, cat in rng.sample(SUBSCRIPTIONS, n_subs):
        sid = new_series(name, cadence)
        if cadence == "weekly":
            sub_start = start + timedelta(days=rng.randint(0, 200))
        elif cadence in ("annual", "semiannual"):
            sub_start = start - timedelta(days=rng.randint(0, 300))
        else:
            sub_start = start - timedelta(days=rng.randint(0, 400)) if rng.random() < 0.7 else \
                start + timedelta(days=rng.randint(0, 480))
        cancel = end - timedelta(days=rng.randint(60, 300)) if rng.random() < 0.2 else None
        hike = rng.random() < 0.3 and cadence in ("monthly", "annual")
        hike_on = start + timedelta(days=rng.randint(90, 500))
        jitter = rng.random() < 0.3
        d, k, any_after = sub_start, 0, False
        p_after = round(price * rng.choice([1.08, 1.12, 1.16, 1.2]), 2)
        while d <= end and (cancel is None or d <= cancel):
            p = price
            if hike and d >= hike_on:
                p, any_after = p_after, any_after or d >= start
            dd = d + timedelta(days=rng.randint(0, 2) if jitter else 0)
            ev(dd, "card", -p, rng.choice(variants), series=sid)
            k += 1
            if cadence == "weekly":
                d = sub_start + timedelta(days=7 * k)
                if rng.random() < 0.08:  # skipped week (meal kit pause)
                    k += 1
                    d = sub_start + timedelta(days=7 * k)
            elif cadence == "monthly":
                d = _add_months(sub_start, k)
            elif cadence == "semiannual":
                d = _add_months(sub_start, 6 * k)
            else:
                d = _add_months(sub_start, 12 * k)
        if hike and any_after and cancel is None:
            truth["creep"].add(sid)

    # ---- variable card spending
    grocers = [_fmt(rng, g, city) for g in rng.sample(GROCERS, 2)]
    coffee_amt = rng.choice([4.75, 5.25, 5.65, 6.10])
    coffee = _fmt(rng, rng.choice(COFFEE), city)
    gas_station = _fmt(rng, rng.choice(GAS), city)
    spend_scale = monthly_income / 6000 * (1.15 if profile == "lean" else 0.8)
    d = start
    while d <= end:
        if rng.random() < 0.35:
            ev(d, "card", -rng.uniform(25, 190) * spend_scale, rng.choice(grocers))
        if rng.random() < 0.30:
            ev(d, "card", -rng.uniform(9, 85) * spend_scale, _fmt(rng, rng.choice(RESTAURANTS), city))
        if rng.random() < 0.40 and d.weekday() < 5:
            ev(d, "card", -coffee_amt, coffee)       # habitual identical-amount purchases (decoy)
            if rng.random() < 0.08:
                ev(d, "card", -coffee_amt, coffee)   # a second coffee same day: legit, not a duplicate
        if rng.random() < 0.12:
            ev(d, "card", -rng.uniform(25, 70), gas_station)
        if rng.random() < 0.18:
            ev(d, "card", -rng.uniform(8, 160) * spend_scale, _fmt(rng, rng.choice(SHOPS), city))
        d += timedelta(days=1)

    # ---- injected duplicate charges and decoys
    card_purchases = [e for e in events if e[1] == "card" and e[2] <= -15 and not e[4] and
                      not e[3].startswith(tuple(c.split(" ")[0] for c in COFFEE))]
    rng.shuffle(card_purchases)
    n_dup = rng.choice([0, 0, 1, 1, 2])
    for e in card_purchases[:n_dup]:
        dd = e[0] + timedelta(days=rng.choice([0, 0, 1]))
        if dd <= end:
            ev(dd, "card", e[2], e[3], dup=True)
    if rng.random() < 0.2 and len(card_purchases) > n_dup:   # duplicate that the merchant already refunded
        e = card_purchases[n_dup]
        ev(e[0], "card", e[2], e[3], dup_refunded=True)
        ev(e[0] + timedelta(days=rng.randint(3, 10)), "card", -e[2], e[3])
    if rng.random() < 0.2:   # legit repeat purchase, same amount same day (e.g. two separate tickets)
        dd = start + timedelta(days=rng.randint(0, (end - start).days))
        amt = -rng.choice([16.5, 18.0, 22.0])
        ev(dd, "card", amt, "CINEMARK THEATRES 0123")
        ev(dd, "card", amt, "CINEMARK THEATRES 0123", legit_repeat=True)

    # ---- fees and fee-like decoys
    if rng.random() < 0.25:   # travel abroad -> FX fees
        t0 = start + timedelta(days=rng.randint(30, 450))
        for i in range(rng.randint(5, 12)):
            amt = rng.uniform(15, 220)
            dd = t0 + timedelta(days=rng.randint(0, 8))
            ev(dd, "card", -amt, rng.choice(FOREIGN))
            ev(dd, "card", -round(amt * 0.03, 2), fee_text["fx"], fee="fx")
    if rng.random() < 0.4:   # cash user with out-of-network ATMs
        d = start + timedelta(days=rng.randint(0, 20))
        while d <= end:
            ev(d, "checking", -rng.choice([40, 60, 100]), f"ATM WITHDRAWAL {rng.randint(1000, 9999)} MAIN ST {city}")
            if rng.random() < 0.7:
                ev(d, "checking", -3.00, fee_text["atm"], fee="atm")
                ev(d, "checking", -rng.choice([2.5, 3.0, 3.5]), "ATM SURCHARGE FEE", fee="atm")
            d += timedelta(days=rng.randint(10, 35))
    if rng.random() < 0.35:   # monthly maintenance fee
        d = date(start.year, start.month, 28)
        while d <= end:
            ev(d, "checking", -12.00, fee_text["maintenance"], fee="maintenance")
            if rng.random() < 0.05:
                ev(d + timedelta(days=2), "checking", 12.00, fee_text["maintenance"] + " REVERSAL")
            d = _add_months(d, 1, 28)
    if rng.random() < 0.15:
        ev(start + timedelta(days=rng.randint(10, 500)), "card", -120.0, "TURBOTAX FEES ONLINE TAX PREP")
    if rng.random() < 0.3:
        ev(start + timedelta(days=rng.randint(10, 500)), "checking", rng.uniform(1, 9), "INTEREST PAYMENT")

    # ---- card statement payments (transfers) and interest for revolvers
    carry = profile == "lean" and rng.random() < 0.5
    carry_bal = rng.uniform(1500, 6000) if carry else 0.0
    month = date(start.year, start.month, 1)
    while month <= end:
        nxt = _add_months(month, 1, 1)
        total = -sum(e[2] for e in events if e[1] == "card" and month <= e[0] < nxt)
        pay_day = date(nxt.year, nxt.month, 20)
        if carry:
            interest = round(carry_bal * rng.uniform(0.019, 0.024), 2)
            ev(date(nxt.year, nxt.month, 18), "card", -interest, fee_text["interest"], fee="interest")
            total += interest
        if total > 0:
            ev(pay_day, "card", total, "PAYMENT THANK YOU-MOBILE")
            ev(pay_day, "checking", -total, "CHASE CREDIT CRD AUTOPAY PPD ID: 4760039224")
        month = nxt

    # ---- checking balance simulation with overdraft fees
    opening = {"lean": rng.uniform(150, 1200), "comfortable": rng.uniform(4000, 9000),
               "idle": rng.uniform(25000, 70000)}[profile]
    events.sort(key=lambda e: (e[0], 0 if e[2] > 0 else 1, e[2]))
    target = monthly_income * (rng.uniform(0.6, 1.0) if profile == "comfortable" else 0.3)
    txs, bal, od_today, swept = [], opening, {}, set()
    for i, (d, acct, amt, desc, labels) in enumerate(events):
        if profile != "idle" and d.day >= 25 and (d.year, d.month) not in swept:
            swept.add((d.year, d.month))
            if bal - target > 200:   # this household sweeps surplus to savings each month
                amt_sw = round(bal - target, 2)
                bal -= amt_sw
                txs.append(Transaction(d, -amt_sw, "ONLINE TRANSFER TO SAV XXXXXX4821", "checking", round(bal, 2),
                                       f"h{seed}:sw{d:%Y%m}", "synthetic"))
        if acct == "checking":
            bal += amt
            t = Transaction(d, amt, desc, acct, round(bal, 2), f"h{seed}:{i}", "synthetic", labels=labels)
            txs.append(t)
            if amt < 0 and bal < 0 and od_today.get(d, 0) < 3 and not labels.get("fee"):
                od_today[d] = od_today.get(d, 0) + 1
                bal -= 35.0
                txs.append(Transaction(d, -35.0, fee_text["overdraft"], acct, round(bal, 2), f"h{seed}:{i}od",
                                       "synthetic", labels={"fee": "overdraft"}))
        else:
            txs.append(Transaction(d, amt, desc, acct, None, f"h{seed}:{i}", "synthetic", labels=labels))
    # price-creep truth = a hike that is observable in the window (old and new price both charged, still active)
    amounts = {}
    for t in txs:
        if t.labels.get("series"):
            amounts.setdefault(t.labels["series"], []).append((t.date, -t.amount))
    truth["creep"] = {sid for sid in truth["creep"] if sid in amounts and len(amounts[sid]) >= 3 and
                      sorted(amounts[sid])[-1][1] > sorted(amounts[sid])[0][1] + 0.01}
    for t in txs:
        if t.labels.get("fee"):
            truth["fees"][t.txid] = t.labels["fee"]
        if t.labels.get("dup"):
            truth["dup"].add(t.txid)
    truth["opening"] = {"checking": opening}
    return Household(seed, txs, truth)


def series_members(hh: Household):
    out = {}
    for t in hh.transactions:
        s = t.labels.get("series")
        if s:
            out.setdefault(s, set()).add(t.txid)
    return out


# ---------------------------------------------------------------- export to bank formats
def to_chase_checking_csv(txs):
    lines = ["Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #,"]
    for t in sorted(txs, key=lambda t: t.date, reverse=True):
        kind = "CREDIT" if t.amount > 0 else "DEBIT"
        lines.append(f'{kind},{t.date:%m/%d/%Y},"{t.description}",{t.amount:.2f},ACH_DEBIT,'
                     f'{"" if t.balance is None else f"{t.balance:.2f}"},,')
    return "\n".join(lines) + "\n"


def to_chase_credit_csv(txs):
    lines = ["Transaction Date,Post Date,Description,Category,Type,Amount,Memo"]
    for t in sorted(txs, key=lambda t: t.date, reverse=True):
        kind = "Payment" if t.amount > 0 else "Sale"
        lines.append(f'{t.date:%m/%d/%Y},{t.date:%m/%d/%Y},"{t.description}",,{kind},{t.amount:.2f},')
    return "\n".join(lines) + "\n"


def to_ofx(txs, acctid="000123456789", acct_type="CHECKING", closing=None):
    body = []
    for t in txs:
        trntype = "CREDIT" if t.amount > 0 else "DEBIT"
        name = t.description.replace("&", "&amp;")
        body.append(f"<STMTTRN>\n<TRNTYPE>{trntype}\n<DTPOSTED>{t.date:%Y%m%d}120000\n<TRNAMT>{t.amount:.2f}\n"
                    f"<FITID>{t.txid}\n<NAME>{name[:32]}\n<MEMO>{name}\n</STMTTRN>")
    end = max(t.date for t in txs)
    bal = closing if closing is not None else 0.0
    return ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\n"
            "COMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n<OFX>\n<BANKMSGSRSV1>\n<STMTTRNRS>\n<TRNUID>1\n"
            f"<STMTRS>\n<CURDEF>USD\n<BANKACCTFROM>\n<BANKID>021000021\n<ACCTID>{acctid}\n<ACCTTYPE>{acct_type}\n"
            "</BANKACCTFROM>\n<BANKTRANLIST>\n<DTSTART>20250101\n<DTEND>" + f"{end:%Y%m%d}\n" + "\n".join(body) +
            f"\n</BANKTRANLIST>\n<LEDGERBAL>\n<BALAMT>{bal:.2f}\n<DTASOF>{end:%Y%m%d}\n</LEDGERBAL>\n</STMTRS>\n"
            "</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n")
