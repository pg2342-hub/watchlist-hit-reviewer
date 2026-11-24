"""A scripted reviewer that follows the procedure with no model: the reference the checks must pass."""

from watchlist.screen import entry


def review(v):
    cust = v["customer"]
    dob = cust["vault"].get("id.dob") or cust["document"].get("dob")
    gender = cust["document"].get("gender")
    out = []
    for h in v["hits"]:
        e = entry(h["uid"])
        res = [d.compare(dob) for d in e.dobs] if dob else []
        comps = [{"attribute": "dob", "customer_value": dob, "list_value": d.text, "result": r} for d, r in zip(e.dobs, res, strict=False)]
        if "match" in res:
            call, kind = "escalate", "likely_match"
        elif dob and e.dobs and all(r == "mismatch" for r in res):
            call, kind = "clear", "dob"
        elif gender and e.gender and gender[0].upper() != e.gender[0].upper():
            call, kind = "clear", "gender"
        else:
            call, kind = "escalate", "need_info"
        out.append(
            {
                "uid": h["uid"],
                "call": call,
                "kind": kind,
                "comparisons": comps,
                "deciding_fact": f"{kind}",
                "narrative": f"Hit {h['uid']}: {call} ({kind}).",
                "next_step": None if call == "clear" else "Obtain a government ID showing full date of birth.",
            }
        )
    return {"reviews": out, "_meta": {"cost_usd": 0.0, "latency_s": 0.0}}
