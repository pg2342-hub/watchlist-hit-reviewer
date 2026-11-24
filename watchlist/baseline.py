"""The rule a queue is often tuned with, which the gate must block: clear any hit below a name-score cutoff.

It never looks at DOB or gender, so a listed person who onboards under a spelling or alias that
scores below the cutoff is cleared and passed straight through.
"""

from __future__ import annotations

from .footprint import FakeFootprint
from .pipeline import confirm
from .screen import screen

CUTOFF = 0.97


def baseline(case_input: dict) -> dict:
    customer = case_input["customer"]
    v = customer["vault"]
    name = " ".join(x for x in (v["id.first_name"], v.get("id.middle_name"), v["id.last_name"]) if x)
    fp = FakeFootprint()
    hits = []
    for h in screen(name):
        call = "escalate" if h["name_score"] >= CUTOFF else "clear"
        kind = "likely_match" if call == "escalate" else "dob"
        hits.append(
            {
                "uid": h["uid"],
                "matched_name": h["matched_name"],
                "name_score": h["name_score"],
                "list_entry": h["list_entry"],
                "proposed": {"call": call, "kind": kind},
                "final": {"call": call, "kind": kind},
                "guardrail": None,
                "facts": confirm(customer, h["uid"]),
                "comparisons": [
                    {
                        "attribute": "name",
                        "customer_value": name,
                        "list_value": h["matched_name"],
                        "result": "match" if call == "escalate" else "mismatch",
                    }
                ],
                "deciding_fact": f"Name score {h['name_score']:.2f} against a cutoff of {CUTOFF}.",
                "narrative": f"Auto-{'escalated' if call == 'escalate' else 'cleared'} on name score {h['name_score']:.2f}.",
                "next_step": "Analyst review." if call == "escalate" else None,
            }
        )
    if hits and all(h["final"]["call"] == "clear" for h in hits):
        fp.post_decision(case_input["fp_id"], "pass", " ".join(h["narrative"] for h in hits))
    return {
        "fp_id": case_input["fp_id"],
        "screened_name": name,
        "risk_signals": [],
        "hits": hits,
        "unknown_reviews": [],
        "footprint_requests": fp.requests,
        "_meta": {"cost_usd": 0.0},
    }
