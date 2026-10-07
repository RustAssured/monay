# Candidate D: depscore, a dependency health and license-risk data product

## 1. Idea

Teams that ship software pull in hundreds of open-source packages. Some of those packages are deprecated, abandoned, archived, or licensed in a way their company can't accept (AGPL, SSPL/BUSL, unknown). Today people find this out by hand or after an incident. **depscore** is an automated pipeline that does the following:

1. **Collects** public metadata from the PyPI JSON API and the npm registry. This works live, and it was run live here. When a `GITHUB_TOKEN` is present (CI), it can also add data from the GitHub REST API.
2. **Scores** each package with transparent rules. The score is a 0-100 health number with an abandonment-risk tier and a license-risk class, and every deduction comes with a plain-English reason. Each package also gets flags: `deprecated`, `inactive_classifier`, `archived_repo`, `latest_yanked`, `single_maintainer`, `no_source_link`, `license_*`.
3. **Publishes** a static site, a static JSON API (one file per package plus an index), a CSV, README badges (SVG), and a `changes.json` feed of packages whose risk got worse since the last run. All of this can be hosted on free static hosting.
4. **Gates CI.** `python -m depscore scan requirements.txt|package.json --policy p.json` exits non-zero when a policy is violated. It ships as a composite GitHub Action (`action.yml`) that runs on the *customer's* runner, so it costs us nothing.
5. **Produces customer digests.** `python -m depscore digest <manifest> --customer X` turns the shared daily run into a per-customer Markdown report covering changes, policy failures, and the lowest-health packages. This is the paid deliverable.

**Business model (open core):**
- **Free:** the public index, the badges, the open-source CLI and the Action.
- **Paid:**
  - **Pro** at $12/mo: a watchlist digest for your manifests (posted as an issue in a sponsors-only private repo, which GitHub then emails), the full daily dataset with history, and policy curation.
  - **Team** at $49/mo: several watchlists, a custom policy, and email support.
- **Sales channel:** GitHub Sponsors tiers or a merchant-of-record checkout.

## 2. Why it is equitable

- Customers pay for **time saved and continuity**: daily monitoring, history, alerts and support. They don't pay for the data itself, which stays free. The scoring code is open, so anyone can self-host and pay nothing.
- **No dark patterns.** Pricing is flat and cancelling is easy. There are no fear-based claims. The site says plainly that scores are heuristic, not legal advice and not a security audit, that "finished" stable libraries can be false positives, and that every deduction is explained. Maintainers can ask for corrections.
- **Fair to maintainers.**
  - We publish only factual metadata the registries already expose, and we link back to it.
  - Wording is neutral ("No releases in the past 12 months"), never "bad package".
  - Recommended launch policy (not modelled as a cost): give 10% of revenue to widely used packages flagged as at risk.
- **Data and ToS.**
  - We use documented public JSON APIs, not HTML scraping.
  - Requests go out with an identifying User-Agent, at low volume (about 10k requests/day, mostly served from CDN cache).
  - We store only derived fields and keep no personal data. The npm maintainer *count* is kept; names and emails are not.
  - Scores are published as CC-BY-4.0.
  - **Open item:** the operator must re-read the current PyPI and npm terms of use before charging money. That review was not possible here.

## 3. What was built and verified

| Item | Status |
|---|---|
| Collectors for PyPI and npm (live), plus optional GitHub enrichment | PyPI and npm were run live. **GitHub could not be tested here:** the sandbox proxy only allows configured repos. The enrichment degrades gracefully when unavailable. |
| License normaliser (SPDX ids, aliases, Trove classifiers, OR/AND/WITH expressions, conservative keyword fallback) | Built and tested. |
| Scoring with reasons, flags and caps | Built and tested. |
| Static site, JSON API, CSV, badges, change feed | Built. The live sample output is in `site/`. |
| CI policy gate (CLI + `action.yml`) | CLI exit codes are tested; `action.yml` itself was not run on GitHub. |
| Customer digest | Built and tested. |
| Daily workflow (`.github/workflows/daily-index.yml`) | Written but **not executed**. It is a template the operator must copy to a repo root. |
| Economics model (`depscore/econ.py` → `ECONOMICS.md`) | Built, tested and reproducible (fixed seed). |

**Test command:** `./run_tests.sh`. Result: **47 passed, 1 skipped**. The skipped test is the live-network one; `DEPSCORE_LIVE=1 ./run_tests.sh` passes it too. The tests run offline against real recorded fixtures (`tests/fixtures/`). They cover:
- the parsers, run on real payloads;
- license classes;
- scoring monotonicity and caps;
- calibration on real data (known-deprecated `request`, `left-pad`, `node-sass`, `tslint` and `coffee-script` score "critical", while `requests`, `django`, `react` and `express` score "low");
- the full offline build, change detection, and fallback to the cache when the network fails;
- HTML escaping and path-traversal safety (one real XSS bug was found and fixed this way);
- the policy gate and CLI exit codes, and the digest;
- the economics: break-even consistency, monotonicity, a reproducible Monte Carlo, and the negative-margin guard.

**Live results from 2026-10-07:**
- 124 mixed packages: 90 low risk, 10 medium, 17 high, 7 critical. On license: 113 permissive, 5 weak copyleft, 4 strong copyleft, 2 unknown. One of the unknowns is `highcharts`, which really does have a commercial license, so that is the correct triage.
- Benchmark of the top 500 PyPI packages: **25 s** wall-clock with 8 threads, about 9 KB of output per package. It correctly flagged `bleach` (Inactive) and `msrest` (deprecated).
- Extrapolation: about 10k packages would take roughly 8-15 min a day and produce about 90 MB.

## 4. Economics (full tables in `ECONOMICS.md`, regenerated by `python -m depscore econ`)

Base assumptions:
- Pro $12, Team $49, 15% of paying accounts on Team, so revenue per paying account (ARPA) is **$17.55/mo**.
- Fees of 5% + $0.50, so **net $16.17** per account.
- Infrastructure is **$0**: GitHub Actions and Pages are free for public repos. That is from GitHub docs as I remember them; it was not re-verified live, because docs.github.com is blocked here.
- Domain about $1.25/mo.
- 6 h/mo of operator time at $40/h.
- 30% tax on positive profit.
- 25 h of launch time.

| Metric | Value |
|---|---|
| Cash break-even (no operator time) | **1 paying account** |
| Break-even when operator time is valued at $40/h | **15 paying accounts** |
| Same, range across $6-$29 price × 2-15 h/mo | 3 to 54 accounts |
| Profit at 25 / 50 / 100 accounts, after tax and time | $42 / $325 / $891 per month |
| Deterministic base case, 24 months | 81 accounts by month 24; cash +$7,971; economic +$1,211 |
| Monte Carlo (5,000 runs, wide demand priors, 35% chance nobody meaningfully pays) | P(cash > 0) = **89%**; **P(beats $40/h) = 16%**; median economic result −$5,671 over 24 months; P90 +$3,065 |

**Sensitivity (tornado):**
- The result is driven almost entirely by **traffic and conversion**: starting visitors (−$5.0k to +$20.7k), growth, and conversion rate.
- The next biggest driver is operator hours.
- Price and fees matter much less.

**Bottom line:**
- Because infrastructure is close to $0, *making more cash than is spent* needs only about one customer, and it is very likely (89% in the model).
- *Paying the operator a fair wage* for their time is **unlikely** under the base assumptions (16%).
- So treat this as a low-risk, low-cost side product or portfolio asset, not as income you can count on.

## 5. Proven vs assumed

**Proven here (by running code):**
- The pipeline works end to end against the live PyPI and npm registries.
- The scoring gives sensible results on known cases.
- The outputs are generated correctly.
- It runs about 20 packages per second on one machine.
- The policy gate and digest work.
- The economics model's arithmetic is internally consistent (tested).

**Assumed (not verified):**
- **All demand numbers:** visitors, conversion, churn, and willingness to pay. No customer has been asked.
- **Platform terms:** GitHub Actions and Pages free-tier limits, the Lemon Squeezy and GitHub Sponsors fee schedules, and whether GitHub Sponsors can give sponsors access to a private repo. These come from memory, because the doc sites are blocked here.
- **Registry terms:** PyPI and npm terms of use for commercial use of derived metadata.
- **Operator side:** tax rate and operator hours.
- **GitHub enrichment:** archived and pushed-at data were not exercised live.

## 6. Risks

- **Strong free competitors.** deps.dev, OpenSSF Scorecard, Socket, Snyk and Dependabot already cover much of this space. Differentiation has to come from the combination of license and abandonment signals with explainable reasons, plus the simple flat-price digest. This is the main reason for the 35% "no fit" mass in the model.
- **False positives on finished software.** Examples are `six`, `sortedcontainers` and `defusedxml`. The other direction is a gap too: `moment` is in maintenance mode but recently released, so it scores low risk. Mitigations are the reasons shown with every score, an allowlist, and a maintainer correction process.
- **No vulnerability data yet.** `pycrypto` shows as "high", not "critical". Next step: add OSV.dev, whose data is CC-BY-4.0. Its API was blocked in this sandbox.
- **Registry API changes or rate limits.** If a live fetch fails, the pipeline serves the last known good record and marks it `stale`.
- **GitHub disables scheduled workflows** in repos with no activity for 60 days. The workflow's daily data commit counters this.
- **Legal.** License classification is a triage aid, not legal advice. Say so everywhere, which is already done.

## 7. Launch checklist (human operator)

1. **Legal and terms:**
   - Read the current PyPI terms of use and npm policies for commercial use of derived metadata.
   - Confirm current GitHub Actions and Pages limits.
   - Pick a payment rail: GitHub Sponsors (check its fee page) or a merchant of record that handles VAT.
2. **Set up the repo:**
   - Create a **public** GitHub repo and copy this directory into it.
   - Move `.github/workflows/daily-index.yml` to the repo root.
   - Enable Pages, with GitHub Actions as the source.
   - Run the workflow manually once and check the site and `changes.json`.
3. **Expand the seeds:** grow `seeds/seeds.txt` to 2k-10k packages, for example the top PyPI and npm packages by downloads. Check the license of any list you import. `seeds/bench-pypi-500.txt` holds package names taken from hugovk/top-pypi-packages, used only for the benchmark; I could not find a LICENSE file for that list.
4. **Run a 2-week calibration:** review the 50 lowest scores by hand, then adjust weights and the allowlist. Publish the method page and a corrections process (an issue template).
5. **Add OSV vulnerability data** (CC-BY-4.0) to the collector before charging.
6. **Set up paid delivery:**
   - Create a sponsors-only private repo.
   - Add a scheduled job that runs `depscore digest` per customer manifest and opens or updates an issue for each.
   - Set up the Pro and Team tiers with a clear refund and cancel policy.
7. **Validate demand:**
   - Post the free index and badges in relevant communities: one post per venue, with disclosure, no spam.
   - Reach out to 20 small teams directly.
   - **Kill criterion:** fewer than 3 paying accounts after 90 days means stop paid work and keep it as a free OSS tool.
8. **Bookkeeping:**
   - Track hours honestly.
   - Re-run `python -m depscore econ` with real conversion and churn numbers each month.
   - Set aside 30% of profit for tax (adjust for your jurisdiction).

## 8. Self-score (1-10)

| Criterion | Score | Why |
|---|---|---|
| Feasibility | 8 | Every component runs on free infrastructure, and the pipeline was exercised live. |
| Provability | 7 | Tests, live runs, measured throughput and a reproducible economics model; demand is still unproven. |
| Buildability | 9 | Standard library plus numpy, one test command, and a CI workflow template. |
| Equity | 9 | The data is free and the code is open; customers pay for convenience; neutral wording toward maintainers. |
| Expected net value | 4 | Cash-positive is very likely, but paying the operator properly is about 16% likely, against strong free competitors. |
