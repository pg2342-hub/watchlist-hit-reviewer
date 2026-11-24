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
    """The DOB the review quotes: the vault's, else the ID document's."""
    return customer["vault"].get("id.dob") or customer["document"].get("dob")


def dobs_on_file(customer: dict) -> list[str]:
    """Every distinct DOB on file: the typed-in vault DOB and the one read off the ID document."""
    out = []
    for d in (customer["vault"].get("id.dob"), customer["document"].get("dob")):
        if d and d not in out:
            out.append(d)
    return out


def confirm(customer: dict, uid: str) -> dict:
    """What code can establish about one hit on its own: DOB results per listed DOB, and gender.

    A DOB rules the listed person out only if every DOB on file (vault and ID document) misses every listed
    DOB, the vault and document agree, and the parser read every listed DOB.
    """
    e = entry(uid)
    on_file = dobs_on_file(customer)
    dob = customer_dob(customer)
    unread = list(e.dob_unread)
    try:
        results = [d.compare(dob) for d in e.dobs] if dob else []
        every = [d.compare(x) for x in on_file for d in e.dobs]
    except ValueError:  # a DOB on file in a format code cannot compare: nothing is ruled out on it
        results, every = [], []
        unread.append(f"customer DOB {dob!r} is not YYYY-MM-DD")
    gender = customer["document"].get("gender")
    return {
        "dob_on_file": bool(on_file),
        "dobs_disagree": len(on_file) > 1,
        "listed_dobs": [d.text for d in e.dobs],
        "listed_dobs_unread": unread,
        "dob_results": results,
        "dob_rules_out": bool(on_file and e.dobs and not unread and len(on_file) == 1 and all(r == "mismatch" for r in every)),
        "dob_exact_match": "match" in every,
        "gender_differs": bool(gender and e.gender and gender[0].upper() != e.gender[0].upper()),
    }


def code_note(customer: dict, hit: dict, facts: dict, proposed: dict) -> dict:
    """The note code writes when the review came back without one: what it can establish, and what to check."""
    dob = customer_dob(customer)
    listed = "; ".join(facts["listed_dobs"]) or "none"
    if not dob:
        dob_part = f"The customer has no DOB on file; the entry lists DOB {listed}."
    else:
        dob_part = f"The customer's DOB {dob} against listed DOB {listed}: {', '.join(facts['dob_results']) or 'no listed DOB'}."
    note = (
        f"Escalated by code. Claude proposed {proposed['call']} ({proposed['kind']}) on hit {hit['uid']} but returned no "
        f"deciding fact or note, so code did not act on it. {dob_part}"
    )
    step = (
        f"Confirm the customer's DOB {dob} on a government ID and compare it with each listed DOB ({listed}), then close the hit."
        if dob
        else f"Obtain a government ID showing full date of birth and compare it with each listed DOB ({listed})."
    )
    return {"narrative": note, "deciding_fact": dob_part, "next_step": step}


def guard(proposed: dict, facts: dict) -> tuple[str, str, str | None]:
    """Final (call, kind, guardrail note) for one hit."""
    if facts["dob_exact_match"]:
        if proposed["call"] == "clear" or proposed["kind"] != "likely_match":
            return "escalate", "likely_match", "a listed full DOB equals the customer's DOB"
        return "escalate", "likely_match", None
    if proposed["call"] == "clear":
        if proposed["kind"] == "dob" and facts["dob_rules_out"]:
            return "clear", "dob", None
        if proposed["kind"] == "gender" and facts["gender_differs"] and facts["dob_on_file"] and not facts["dobs_disagree"]:
            return "clear", "gender", None
        if not facts["dob_on_file"]:
            why = "no DOB on file"
        elif facts["dobs_disagree"]:
            why = "the vault DOB and the ID document DOB disagree"
        elif facts.get("listed_dobs_unread"):
            why = "a listed DOB could not be read: " + "; ".join(facts["listed_dobs_unread"])
        elif proposed["kind"] == "dob":
            why = "a listed DOB does not rule the customer out"
        else:
            why = "genders do not differ on file"
        return "escalate", "need_info", f"clear blocked: {why}"
    kind = proposed["kind"] if proposed["kind"] in ("likely_match", "need_info") else "need_info"
    return "escalate", kind, None


def run(
    case_input: dict,
    review: Callable[[dict], dict],
    footprint: FakeFootprint | None = None,
    hold: str | None = None,
) -> dict:
    """Review every hit and write a pass when all are cleared, unless `hold` says why nothing may be posted."""
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
            r = {**r, **code_note(customer, h, facts, proposed)}
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
    if out_hits and all(h["final"]["call"] == "clear" for h in out_hits) and not hold:
        note = " ".join(f"[{h['uid']}] {h['narrative']}" for h in out_hits)
        footprint.post_decision(case_input["fp_id"], "pass", note)
    return {
        "fp_id": case_input["fp_id"],
        "screened_name": name,
        "risk_signals": signals,
        "hits": out_hits,
        "unknown_reviews": unknown,
        "held": hold if out_hits and all(h["final"]["call"] == "clear" for h in out_hits) else None,
        "footprint_requests": footprint.requests,
        "_meta": reply.get("_meta", {}),
    }
