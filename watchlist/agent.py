"""The hit-review agent: one Footprint webhook in, one reviewed customer out.

On footprint.onboarding.completed with requires_manual_review, it:
  1. reads the onboarding's risk signals and stops unless one is an OFAC watchlist hit
  2. decrypts only the fields the review needs (name, DOB, nationality, address, ID document DOB and gender),
     with a logged reason
  3. pulls the candidate SDN entries for the name and has Claude review each hit against the SOP
  4. applies the guardrails in code (pipeline.guard)
  5. posts a pass decision only when every hit is cleared; otherwise leaves the manual review open
     and writes a case brief for the analyst. It never posts fail.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from . import pipeline
from .footprint import VAULT_FIELDS, FootprintClient, customer_record

DECRYPT_REASON = "OFAC watchlist hit review: compare the customer to the listed entries"


def handle(event: dict, fp: FootprintClient, review: Callable[[dict], dict]) -> dict:
    t0 = time.perf_counter()
    base = {"event_kind": event.get("event_kind"), "fp_id": event.get("fp_id")}
    if event.get("event_kind") != "footprint.onboarding.completed" or not event.get("requires_manual_review"):
        return {**base, "action": "ignored", "why": "not an onboarding waiting on manual review", "trace": []}
    fp_id, ob_id = event["fp_id"], event["onboarding_id"]
    signals = fp.get_risk_signals(fp_id, ob_id)
    if not any(s["reason_code"] == "watchlist_hit_ofac" for s in signals):
        return {**base, "action": "ignored", "why": "no OFAC hit signal on this onboarding", "trace": fp.trace}
    customer = customer_record(fp.decrypt(fp_id, VAULT_FIELDS, DECRYPT_REASON))
    out = pipeline.run({"fp_id": fp_id, "onboarding_id": ob_id, "customer": customer}, review, footprint=fp)
    cleared = bool(out["hits"]) and all(h["final"]["call"] == "clear" for h in out["hits"])
    return {
        **base,
        "onboarding_id": ob_id,
        "action": "posted_pass" if cleared else "sent_to_analyst",
        "customer": customer,
        "review": out,
        "brief": None if cleared else brief(out),
        "trace": fp.trace,
        "seconds": round(time.perf_counter() - t0, 3),
    }


def brief(out: dict) -> str:
    """The case note an analyst opens: one block per hit, escalations first."""
    lines = [f"OFAC review for {out['screened_name']} ({out['fp_id']})", ""]
    for h in sorted(out["hits"], key=lambda h: h["final"]["call"] != "escalate"):
        f = h["final"]
        lines.append(f"Hit {h['uid']} {h['matched_name']} (name score {h['name_score']:.2f}): {f['call']} ({f['kind']})")
        if h["guardrail"]:
            lines.append(f"  Guardrail: {h['guardrail']}")
        lines.append(f"  {h['narrative']}")
        if f["call"] == "escalate" and h["next_step"]:
            lines.append(f"  Next step: {h['next_step']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
