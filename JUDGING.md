# monay — 5-agent bake-off: judging

**Brief:** find a feasible, provable, buildable, equitable, automated way to make more money consistently than is spent.
Five agents each built one strategy under `candidates/`. All tests were re-run by the judge (not just self-reported).

| | Candidate | What it is | Tests (re-run) | Needs customers? | Cash cost | Judge's net-value read |
|---|---|---|---|---|---|---|
| A | `A-saas/` flakehunter | Open-core flaky-test detector + paid hosted tier | 35 pass | Yes | ~$40/mo | Cash-positive likely; **negative once operator time counted** (median −$4.3k/36 mo, sim) |
| B | `B-invest/` investengine | Index allocation, band rebalancing, cash sweep, tax-loss harvesting, paper/CSV/Alpaca brokers | 37 pass | No | ~0.1%/yr | Highest absolute $ on capital; **not consistent** — ~24% chance of a losing year, 63% max drawdown (1929–32) |
| C | `C-savings/` spendaudit | Local-first auditor of bank CSV/OFX exports: subscriptions, price creep, duplicates, fees, idle cash, forecast | 39 pass | No | **$0** | **Positive with zero spend**; synthetic median ~$185/household/yr; each action verifiable on next statement |
| D | `D-dataproduct/` depscore | PyPI/npm dependency-health scoring pipeline → static site/API, paid digests | 47 pass, 1 skip (network) | Yes | ~$1/mo | Same shape as A; strong free competitors; median −$5.7k incl. time (sim) |
| E | `E-wildcard/` tou_battery | LP-optimal home-battery dispatch for time-of-use tariffs, with M&V | 22 pass | No | ~$80 once | Real but niche: +$149–267/yr vs factory default for existing owners; ~$0 vs a good vendor mode |

## Scores (judge, 1–10)

| | Feasible | Provable | Buildable | Equitable | "More than spent, consistently" | **Total** |
|---|---|---|---|---|---|---|
| A | 7 | 6 | 9 | 9 | 3 | 34 |
| B | 9 | 7 | 9 | 10 | 5 | 40 |
| **C** | **9** | **8** | **9** | **9** | **8** | **43** |
| D | 8 | 6 | 8 | 9 | 3 | 34 |
| E | 6 | 9 | 9 | 10 | 5 | 39 |

## Winner: C — `spendaudit`

It got farthest against the literal brief:
- **"More than spent":** it costs $0 to run, so any action taken is net positive. That holds in every scenario, unlike A/D (depend on finding customers) or B (depends on markets).
- **Consistent:** savings from fees, cancelled subscriptions and idle-cash yield recur every month and don't depend on market direction.
- **Provable:** each finding cites the transaction ids behind it, and the saving shows up as a checkable difference on the next statement.
- **Equitable:** runs offline, sends no data anywhere, uses no affiliate links, takes no cut of savings, and never acts on the user's behalf.

**Honest limits:**
- **Synthetic data only:** detection accuracy and dollar values were measured on synthetic households written by the same agent that wrote the detectors, so they are optimistic.
- **Modest and front-loaded:** the dollar amounts are small, and part of the value is one-time. Most of it comes from idle cash and fees.
- **Not a business:** as a product its own model gives only a 23% chance of paying for founder time.

**Runner-up / pairing: B.** C's largest finding is usually idle cash. B is the honest place for that cash to go long-term (positive expected return, ~0.1%/yr cost), provided the risks in its results are accepted.

## Not proven anywhere
- No candidate makes money that is guaranteed or consistent month to month without someone acting on it.
- No candidate proves its customer numbers (A, D, C-as-product).
- No candidate was tested on real bank exports, real tariffs or live broker APIs.

## Process notes
- The five per-agent `REPORT.md` files were not written. Subagents were blocked from writing `.md` files, and the judge did not write them on their behalf. Each agent's generated results are in its own folder:
  - A: `sim/results.json`
  - B: `results/RESULTS.md`
  - D: `REPORT.md` (written)
  - E: `results/summary.md`
  - C: `python -m spendaudit evaluate`
- CI (`.github/workflows/ci.yml`) runs every candidate's test suite on each push.

## Use the winner
```
cd candidates/C-savings
python -m pytest -q                                   # 39 tests
python -m spendaudit audit ~/Downloads/*.csv ~/Downloads/*.qfx --target-apy 0.04 --out audit.md
```
Re-run it every quarter. The change in fee and subscription totals is the saving actually realized.
