# watchlist-backtest-gate

[![CI](https://github.com/pg2342-hub/watchlist-backtest-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/pg2342-hub/watchlist-backtest-gate/actions/workflows/ci.yml)

Demo page: https://pranavgudise.dev/watchlist-backtest-gate/ (every customer, hit, call and narrative from the latest recorded live run; no install needed)

A backtest and go-live gate for an AI reviewer of sanctions screening hits. Constructed customers are screened against the **real OFAC SDN list**. Claude reviews every hit under a written procedure, proposes clear or escalate, and writes the case narrative. Code confirms the deciding fact before any clear is posted to Footprint. The gate then compares every call with the call an analyst already made on that case: **go, partial or no-go**, and a single wrong clear blocks go-live.

Built on [golive-gate](https://github.com/pg2342-hub/golive-gate).

{{LATEST_RUN}}

## Why a backtest comes first

Most sanctions name hits are not the listed person, so a reviewer that agrees with analysts most of the time can look ready long before it is. The question a bank asks before it lets anything clear a hit is narrower: on our own past cases, did it ever clear someone our analyst escalated? The list makes that harder than it looks. One listed person can carry several dates of birth, a year-only date, a "circa" year or a range, and the same person can onboard under another spelling or an alias.

## What is in it

| Part | File |
|---|---|
| The OFAC SDN list (SDN, ALT and ADD CSV files), fetched from the Treasury Sanctions List Service by this repo's own workflow, with a manifest of sizes and hashes | `scripts/fetch_sdn.py`, `data/ofac/` |
| Parsing the list's free-text remarks into every listed DOB (full date, month, year, circa, range), place of birth, nationality, citizenship and gender | `watchlist/sdn.py` |
| The screener: each customer name against every listed individual and alias, token-order independent, accents and titles stripped, Jaro-Winkler per token | `watchlist/names.py`, `watchlist/screen.py` |
| 40 historical reviews in six groups: listed people (by name, by another spelling, by alias), same name born years apart, same name and the other gender, records that cannot rule the listed person out, listed people with several DOBs, and common names with several hits. Each hit carries the analyst's call | `scripts/make_cases.py` → `evals/cases.yml` |
| The review procedure the agent is given and the analysts followed | `procedures/sanctions_review_sop.md` |
| The Claude review step: one call per customer, structured output, a proposed call, the attributes compared with both values, the deciding fact, a narrative and a next step for every hit | `watchlist/reviewer.py` |
| Guardrails in code: a clear stands only when code confirms its deciding fact; a listed full DOB equal to the customer's always escalates; a hit with no review escalates; Footprint gets a pass decision only when every hit is cleared, and never a fail | `watchlist/pipeline.py` |
| The Footprint side: risk signals (`watchlist_hit_ofac`) and the manual decision request (`POST /users/{fp_id}/decisions`, `status` pass or fail plus `annotation`), validated against Footprint's public API reference | `watchlist/footprint.py` |
| 6 rule checks (3 critical) and 2 Claude judge checks (narratives grounded in the evidence, critical; escalations say what to obtain) | `evals/checks.py`, `evals/judge_checks.py` |
| The gate, on each of 3 runs: zero critical failures, 37 of 40 customers, every listed person, every record that cannot be ruled out, every multi-DOB case; over-escalating clearable hits holds rollout at partial | `evals/gate.yml` |
| A name-score cutoff rule the gate must block | `watchlist/baseline.py` |

## Footprint

Footprint's sandbox needs an approved account, so this repo does not call Footprint. The two requests it makes follow Footprint's public API reference (read November 24, 2026): `GET /users/{fp_id}/onboardings/{onboarding_id}/risk_signals` and `POST /users/{fp_id}/decisions`. `FakeFootprint` validates and records every request so the checks can grade them; `FootprintClient` sends the same requests when `FOOTPRINT_API_KEY` is set, and is untested against the live API.

## Run it

```
pip install -e ".[dev]"
pytest -q                                      # tests, no network or key
golive run evals/golive.baseline.yml           # name-score cutoff: NO-GO
golive run evals/golive.yml                    # replays the recorded Claude run: no key needed
GOLIVE_LIVE=1 ANTHROPIC_API_KEY=... golive run evals/golive.live.yml   # a fresh live run, recorded to recordings/
python scripts/fetch_sdn.py                    # refresh the SDN snapshot
python scripts/make_cases.py                   # rebuild the 40 cases from the snapshot
python scripts/build_demo.py                   # rebuild the demo page
```

## How the live run is recorded

The review step and the judge call Claude (`claude-opus-5-5`, low effort). A live run writes every reply to `recordings/`, with cost and latency per call. CI and the demo page replay those replies through the current code (screening, guardrails and the Footprint writes always run for real), so they need no API key and give the same result every time. A recording made with a different prompt, schema or model does not replay. The **Record a live run** workflow (Actions tab, needs the `ANTHROPIC_API_KEY` secret) refreshes the recording and the demo page.

## CI

Every push runs ruff, the tests on Python 3.10 and 3.12, the cutoff rule (which must come out no-go), and the gate on the recorded run, which fails the build on no-go and writes the report to the job summary.

List entries are real public data from OFAC. Customers, their records, the analysts' calls and the review procedure are constructed; no data here belongs to any bank or customer.
