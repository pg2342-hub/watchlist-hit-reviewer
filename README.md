# watchlist-hit-reviewer

[![CI](https://github.com/pg2342-hub/watchlist-hit-reviewer/actions/workflows/ci.yml/badge.svg)](https://github.com/pg2342-hub/watchlist-hit-reviewer/actions/workflows/ci.yml)

Demo page: https://pranavgudise.dev/watchlist-hit-reviewer/ (pick any of 40 customers and follow the agent through every Footprint call; no install needed)

An agent for KYC teams on Footprint. When an onboarding lands in manual review with an OFAC watchlist hit, it reads the risk signals, decrypts only the fields the review needs, has Claude compare each hit with the customer under a written procedure, and posts a pass only when code confirms the hit is not the customer. Everything else stays in manual review, with a case brief for the analyst. It never posts a fail.

**Latest recorded live run (November 24, 2026, `claude-opus-5-5`, medium effort, 40 customers x 3 runs):** the agent made the analyst's call on **59 of 59 hits on every run**, with **zero wrong clears**, and posted a pass for **22 of 40** customers, every one of them a customer the analyst had cleared. The gate is still **NO-GO**, on wording alone: on 2 escalations the next step left for the analyst was too vague to act on, and the gate requires every listed person's case to pass every check. A name-score cutoff cleared 2 listed people who onboarded under another spelling (NO-GO with critical errors). Mean $0.035 and 9.6 s per customer for the review; the judge cost $1.63 for all 3 runs.

## What it does, step by step

1. **Receive.** `python -m watchlist.server` takes Footprint's `footprint.onboarding.completed` webhook, checks its Svix signature, answers 202 and queues the work. Events without `requires_manual_review` are ignored.
2. **Read.** `GET /users/{fp_id}/onboardings/{onboarding_id}/risk_signals`. No `watchlist_hit_ofac` signal, nothing to do.
3. **Decrypt, scoped.** `POST /users/{fp_id}/vault/decrypt` for name, DOB, nationality, address and the ID document's DOB and gender, with a reason Footprint logs. No SSN, email or phone.
4. **Review.** Candidate entries come from the real OFAC SDN list. Claude (`claude-opus-5-5`, structured output) compares DOB, gender, nationality, place of birth and aliases for every hit and proposes clear or escalate, with the deciding fact, an audit note and, for an escalation, the next thing to obtain.
5. **Guard.** Code decides what may be written. A clear stands only if code confirms its deciding fact (the customer's DOB differs from every listed DOB, or both genders are on file and differ). A listed full DOB equal to the customer's always escalates. A hit with no review, or a review with no note, escalates.
6. **Write.** `POST /users/{fp_id}/decisions` with `status: pass` and the notes as the annotation, only when every hit is cleared. Otherwise the review stays open and the analyst gets the brief.

## Backtest before it touches a queue

Before a bank lets anything clear a hit, it wants one answer: on our own past cases, did it ever clear someone our analyst escalated? The repo answers that with 40 past reviews built on real SDN entries (listed people under their own name, another spelling and an alias; namesakes born years apart; the other gender; records that cannot rule the person out; people with several listed DOBs; common names with several hits), each with the analyst's call. [golive-gate](https://github.com/pg2342-hub/golive-gate) runs the same agent 3 times on all 40 and holds go-live unless there are zero wrong clears, 37 of 40 customers pass, and every listed person is escalated. A name-score cutoff rule runs on the same cases as the shortcut it must beat.

## Footprint

Footprint's sandbox needs company approval, so this repo does not call Footprint. `FootprintClient` sends the requests in Footprint's public API reference (read November 24, 2026), authenticated with `X-Footprint-Secret-Key`. Tests and the demo run it over HTTP against `watchlist/sandbox.py`, a stand-in that answers in the same shapes and rejects what Footprint would reject (no key, a decrypt without a reason or for an unknown field, a decision body with extra fields). The document field names (`document.passport.dob` and similar) should be confirmed on an account.

To point it at Footprint:

```
FOOTPRINT_API_KEY=sk_test_... FOOTPRINT_WEBHOOK_SECRET=whsec_... ANTHROPIC_API_KEY=... \
  python -m watchlist.server --port 8000 --out cases/
```

then register `https://<host>/webhooks/footprint` in the Footprint dashboard.

## Files

| Part | File |
|---|---|
| The agent: one webhook in, one reviewed customer out, plus the analyst brief | `watchlist/agent.py` |
| Webhook receiver and Svix signature check | `watchlist/server.py`, `watchlist/svix.py` |
| Footprint client and request validation | `watchlist/footprint.py` |
| Footprint stand-in for tests and the demo | `watchlist/sandbox.py` |
| Claude review step (prompt, schema, one retry on an empty note) | `watchlist/reviewer.py`, `procedures/sanctions_review_sop.md` |
| Guardrails and the decision write | `watchlist/pipeline.py` |
| OFAC SDN snapshot, fetched by this repo's own workflow, and its parser | `scripts/fetch_sdn.py`, `data/ofac/`, `watchlist/sdn.py` |
| Name screen (token-order independent, accents and titles stripped, Jaro-Winkler per token) | `watchlist/names.py`, `watchlist/screen.py` |
| 40 past reviews, checks (6 rule, 2 Claude judge), gate | `scripts/make_cases.py`, `evals/` |

## Run it

```
pip install -e ".[dev]"
pytest -q                               # tests, no network or key
python scripts/build_demo.py            # runs the agent on all 40 customers and both gates, writes docs/
golive run evals/golive.yml             # the backtest, replaying the recorded Claude run: no key needed
golive run evals/golive.baseline.yml    # the name-score cutoff: NO-GO
```

The review step and the judge call Claude only in the **Record a live run** workflow (Actions tab, needs the `ANTHROPIC_API_KEY` secret), which writes every reply with its cost and latency to `recordings/`. CI and the demo replay those replies through the current code, so screening, guardrails and every Footprint request always run for real, with no key. A recording made with a different prompt, schema or model does not replay.

List entries are real public data from OFAC. Customers, their records, the analysts' calls and the review procedure are constructed; no data here belongs to any bank or customer.
