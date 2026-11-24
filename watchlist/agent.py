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

import re
import time
from collections.abc import Callable

from . import pipeline
from .footprint import VAULT_FIELDS, FootprintClient, customer_record

DECRYPT_REASON = "OFAC watchlist hit review: compare the customer to the listed entries"
OFAC = {"watchlist_hit_ofac", "watchlist_clear_ofac"}


def flagged_uids(signal: dict) -> list[str] | None:
    """The SDN entries Footprint says it matched, or None if the signal does not say.

    Read from a `matches` list when present, else from "SDN entry <uid>" in the description. Footprint's
    exact match-detail shape needs an account to confirm; until it is wired in, a signal that names no
    entry keeps the customer with an analyst.
    """
    if isinstance(signal.get("matches"), list):
        uids = [str(m.get("uid") or m.get("id")) for m in signal["matches"] if m.get("uid") or m.get("id")]
        return uids or None
    uids = re.findall(r"SDN entry (\d+)", signal.get("description", ""))
    return uids or None


def hold_reason(signals: list[dict], reviewed: set[str]) -> str | None:
    """Why no pass may be posted even if every reviewed hit clears."""
    other = sorted({s["reason_code"] for s in signals if s["reason_code"] not in OFAC and s["severity"] in ("medium", "high")})
    if other:
        return "other risk signals need a person: " + ", ".join(other)
    for s in signals:
        if s["reason_code"] != "watchlist_hit_ofac":
            continue
        uids = flagged_uids(s)
        if uids is None:
            return "Footprint's OFAC signal does not name the matched entry, so the review cannot be tied to it"
        missing = sorted(set(uids) - reviewed)
        if missing:
            return "Footprint flagged SDN entries the review did not cover: " + ", ".join(missing)
    return None


def handle(event: dict, fp: FootprintClient, review: Callable[[dict], dict]) -> dict:
    t0 = time.perf_counter()
    base = {"event_kind": event.get("event_kind"), "fp_id": event.get("fp_id")}
    if event.get("event_kind") != "footprint.onboarding.completed" or not event.get("requires_manual_review"):
        return {**base, "action": "ignored", "why": "not an onboarding waiting on manual review", "trace": []}
    fp_id = event["fp_id"]
    ob_id = event.get("onboarding_id") or fp.list_onboardings(fp_id)[0]["id"]
    signals = fp.get_risk_signals(fp_id, ob_id)
    if not any(s["reason_code"] == "watchlist_hit_ofac" for s in signals):
        return {**base, "action": "ignored", "why": "no OFAC hit signal on this onboarding", "trace": fp.trace}
    customer = customer_record(fp.decrypt(fp_id, VAULT_FIELDS, DECRYPT_REASON))
    from .screen import screen

    name = " ".join(
        x
        for x in (customer["vault"].get("id.first_name"), customer["vault"].get("id.middle_name"), customer["vault"].get("id.last_name"))
        if x
    )
    hold = hold_reason(signals, {h["uid"] for h in screen(name)})
    out = pipeline.run({"fp_id": fp_id, "onboarding_id": ob_id, "customer": customer}, review, footprint=fp, hold=hold)
    cleared = bool(out["hits"]) and all(h["final"]["call"] == "clear" for h in out["hits"]) and not out["held"]
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
    if out.get("held"):
        lines += [f"Every hit cleared, but no pass was posted: {out['held']}.", ""]
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
