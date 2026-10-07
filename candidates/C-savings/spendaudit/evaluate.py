"""Precision/recall of every detector against the labelled synthetic households."""
from __future__ import annotations

from . import detectors as D
from .audit import run_audit
from .synth import generate_household, series_members


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def _match(pred_sets, true_sets, thresh=0.5):
    """Greedy one-to-one matching by Jaccard overlap of transaction ids."""
    pairs = []
    for i, p in enumerate(pred_sets):
        for k, t in true_sets.items():
            j = len(p & t) / len(p | t)
            if j >= thresh:
                pairs.append((j, i, k))
    pairs.sort(reverse=True)
    used_p, used_t, matched = set(), set(), {}
    for j, i, k in pairs:
        if i not in used_p and k not in used_t:
            used_p.add(i); used_t.add(k); matched[i] = k
    return matched


def evaluate(n_households=200, seed0=1000):
    agg = {k: [0, 0, 0] for k in ("recurring_out", "recurring_out_detectable", "recurring_income", "price_creep",
                                  "duplicates", "fees", "idle_cash")}
    totals = {"identified": 0.0, "expected": 0.0, "true_fee_annual": 0.0, "households": n_households}
    for h in range(n_households):
        hh = generate_household(seed0 + h)
        truth = hh.truth
        members = series_members(hh)
        res = run_audit(hh.transactions, checking_accounts=["checking"])
        totals["identified"] += res.identified_annual
        totals["expected"] += res.expected_annual

        for direction, key in (("out", "recurring_out"), ("in", "recurring_income")):
            true_sets = {s: members[s] for s, meta in truth["series"].items()
                         if meta["direction"] == direction and s in members}
            preds = [s.txids for s in res.recurring if s.direction == direction]
            m = _match(preds, true_sets)
            agg[key][0] += len(m)
            agg[key][1] += len(preds) - len(m)
            agg[key][2] += len(true_sets) - len(m)
            if direction == "out":
                detectable = {s for s, meta in truth["series"].items() if meta["direction"] == "out" and s in members
                              and len(members[s]) >= D.MIN_OCCURRENCES[meta["cadence"]]}
                hit = set(m.values()) & detectable
                agg["recurring_out_detectable"][0] += len(hit)
                agg["recurring_out_detectable"][1] += len(preds) - len(m)
                agg["recurring_out_detectable"][2] += len(detectable) - len(hit)
                # price creep: a flagged series counts as correct if it matches a true series with a real hike
                pred_sets = [p.series.txids for p in res.price_creep]
                creep_true = {s: members[s] for s in truth["creep"]}
                mc = _match(pred_sets, creep_true)
                agg["price_creep"][0] += len(mc)
                agg["price_creep"][1] += len(pred_sets) - len(mc)
                agg["price_creep"][2] += len(creep_true) - len(mc)

        pred_dup = {d.duplicate.txid for d in res.duplicates} | set()
        # either half of a pair is acceptable evidence; count by pair
        tp = 0
        for d in res.duplicates:
            if d.duplicate.txid in truth["dup"] or d.original.txid in truth["dup"]:
                tp += 1
        agg["duplicates"][0] += tp
        agg["duplicates"][1] += len(res.duplicates) - tp
        agg["duplicates"][2] += max(0, len(truth["dup"]) - tp)

        pred_fee = {t.txid for f in res.fees for t in f.transactions}
        true_fee = set(truth["fees"])
        agg["fees"][0] += len(pred_fee & true_fee)
        agg["fees"][1] += len(pred_fee - true_fee)
        agg["fees"][2] += len(true_fee - pred_fee)

        pred_idle = bool(res.idle)
        agg["idle_cash"][0] += int(pred_idle and truth["idle"])
        agg["idle_cash"][1] += int(pred_idle and not truth["idle"])
        agg["idle_cash"][2] += int(truth["idle"] and not pred_idle)
    out = {k: _prf(*v) for k, v in agg.items()}
    totals["mean_identified_per_household"] = round(totals["identified"] / n_households, 2)
    totals["mean_expected_per_household"] = round(totals["expected"] / n_households, 2)
    return out, totals


def format_table(metrics, totals):
    lines = ["| detector | TP | FP | FN | precision | recall | F1 |", "|---|---|---|---|---|---|---|"]
    for k, v in metrics.items():
        lines.append(f"| {k} | {v['tp']} | {v['fp']} | {v['fn']} | {v['precision']:.3f} | {v['recall']:.3f} | "
                     f"{v['f1']:.3f} |")
    lines.append("")
    lines.append(f"Households: {totals['households']}. Mean identified annual value/household (synthetic): "
                 f"${totals['mean_identified_per_household']:,.2f}; confidence-weighted: "
                 f"${totals['mean_expected_per_household']:,.2f}.")
    return "\n".join(lines)
