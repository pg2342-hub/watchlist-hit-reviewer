"""One customer, end to end: screen, read the risk signals, review each hit, apply guardrails, write to Footprint.

Guardrails (code, not the model):
- A clear stands only when code confirms its deciding fact: for a DOB clear, the customer's DOB is on
  file and differs from every listed DOB; for a gender clear, both genders are on file and differ and no
  listed DOB matches. Otherwise the hit is escalated as need_info, with the reason kept.
- A hit whose listed full DOB equals the customer's is always escalated as a likely match.
- A hit the model did not review, or reviewed without a deciding fact and note, is escalated.
- Footprint gets a pass decision only when every hit on the customer is cleared, with the narratives as
  the annotation. Escalations leave the manual review open. Nothing here ever posts a fail.
"""

from __future__ import annotations

from collections.abc import Callable

from . import reviewer as rv
from .footprint import FakeFootprint, risk_signals, validate_signal
from .screen import entry, screen


def customer_dob(customer: dict) -> str | None:
    return customer["vault"].get("id.dob") or customer["document"].get("dob")


def confirm(customer: dict, uid: str) -> dict:
    """What code can establish about one hit on its own: DOB results per listed DOB, and gender."""
    e = entry(uid)
    dob = customer_dob(customer)
    results = [d.compare(dob) for d in e.dobs] if dob else []
    gender = customer["document"].get("gender")
    return {
        "dob_on_file": dob is not None,
        "listed_dobs": [d.text for d in e.dobs],
        "dob_results": results,
        "dob_rules_out": bool(dob and e.dobs and all(r == "mismatch" for r in results)),
        "dob_exact_match": "match" in results,
        "gender_differs": bool(gender and e.gender and gender[0].upper() != e.gender[0].upper()),
    }


def guard(proposed: dict, facts: dict) -> tuple[str, str, str | None]:
    """Final (call, kind, guardrail note) for one hit."""
    if facts["dob_exact_match"]:
        if proposed["call"] == "clear" or proposed["kind"] != "likely_match":
            return "escalate", "likely_match", "a listed full DOB equals the customer's DOB"
        return "escalate", "likely_match", None
    if proposed["call"] == "clear":
        if proposed["kind"] == "dob" and facts["dob_rules_out"]:
            return "clear", "dob", None
        if proposed["kind"] == "gender" and facts["gender_differs"]:
            return "clear", "gender", None
        why = (
            "no DOB on file"
            if not facts["dob_on_file"]
            else ("a listed DOB does not rule the customer out" if proposed["kind"] == "dob" else "genders do not differ on file")
        )
        return "escalate", "need_info", f"clear blocked: {why}"
    kind = proposed["kind"] if proposed["kind"] in ("likely_match", "need_info") else "need_info"
    return "escalate", kind, None


def run(case_input: dict, review: Callable[[dict], dict], footprint: FakeFootprint | None = None) -> dict:
    customer = case_input["customer"]
    name = " ".join(
        x for x in (customer["vault"]["id.first_name"], customer["vault"].get("id.middle_name"), customer["vault"]["id.last_name"]) if x
    )
    hits = screen(name)
    signals = risk_signals(hits)
    for s in signals:
        validate_signal(s)
    footprint = footprint or FakeFootprint()
    reply = review(rv.view(case_input, hits)) if hits else {"reviews": [], "_meta": {"cost_usd": 0.0, "latency_s": 0.0}}
    by_uid = {r["uid"]: r for r in reply["reviews"]}
    out_hits = []
    for h in hits:
        r = by_uid.get(h["uid"])
        facts = confirm(customer, h["uid"])
        if r is None:
            proposed = {"call": "escalate", "kind": "need_info"}
            final = ("escalate", "need_info", "the reviewer returned no review for this hit")
            r = {"comparisons": [], "deciding_fact": "", "narrative": "", "next_step": None}
        elif not str(r.get("narrative") or "").strip() or not str(r.get("deciding_fact") or "").strip():
            proposed = {"call": r["call"], "kind": r["kind"]}
            final = ("escalate", "need_info", "the reviewer gave no deciding fact or note, so nothing can be cleared on it")
            r = {**r, "narrative": "Escalated by code: the review came back without a deciding fact or note.", "deciding_fact": ""}
            r["next_step"] = r.get("next_step") or "Review this hit by hand against the list entry."
        else:
            proposed = {"call": r["call"], "kind": r["kind"]}
            final = guard(proposed, facts)
        out_hits.append(
            {
                "uid": h["uid"],
                "matched_name": h["matched_name"],
                "name_score": h["name_score"],
                "list_entry": h["list_entry"],
                "proposed": proposed,
                "final": {"call": final[0], "kind": final[1]},
                "guardrail": final[2],
                "facts": facts,
                "comparisons": r["comparisons"],
                "deciding_fact": r["deciding_fact"],
                "narrative": r["narrative"],
                "next_step": r["next_step"],
            }
        )
    unknown = sorted(set(by_uid) - {h["uid"] for h in hits})
    if out_hits and all(h["final"]["call"] == "clear" for h in out_hits):
        note = " ".join(f"[{h['uid']}] {h['narrative']}" for h in out_hits)
        footprint.post_decision(case_input["fp_id"], "pass", note)
    return {
        "fp_id": case_input["fp_id"],
        "screened_name": name,
        "risk_signals": signals,
        "hits": out_hits,
        "unknown_reviews": unknown,
        "footprint_requests": footprint.requests,
        "_meta": reply.get("_meta", {}),
    }
