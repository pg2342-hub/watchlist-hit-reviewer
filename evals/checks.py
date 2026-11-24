"""Rule checks on one reviewed customer. Critical checks block go-live on a single failure.

The output (see watchlist/pipeline.py) has, for every screening hit, the reviewer's proposed call,
the final call after guardrails, the comparisons it quoted, its narrative, and the requests the
pipeline sent to Footprint. case.expected holds the analyst's past call on every hit.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golive_gate import FAIL, NA, PASS, CheckSet, Verdict

from watchlist.footprint import validate_decision

checks = CheckSet()


def v(ok, reason=""):
    return Verdict(PASS, "") if ok else Verdict(FAIL, reason)


def calls(case):
    return case.expected["calls"]


@checks.check(
    "every_hit_reviewed", text="Every screening hit gets a review, and no review names a list entry that did not hit.", critical=True
)
def every_hit_reviewed(case, out):
    got = sorted(h["uid"] for h in out["hits"])
    missing = [h["uid"] for h in out["hits"] if h["guardrail"] == "the reviewer returned no review for this hit"]
    if got != sorted(case.expected["hits"]):
        return v(False, f"hits {got} differ from the screening record {case.expected['hits']}")
    return v(not missing and not out["unknown_reviews"], f"no review for {missing}; reviews for unknown entries {out['unknown_reviews']}")


@checks.check("no_false_clear", text="No hit the analyst escalated is cleared.", critical=True)
def no_false_clear(case, out):
    bad = [h["uid"] for h in out["hits"] if h["final"]["call"] == "clear" and calls(case)[h["uid"]]["call"] == "escalate"]
    return v(not bad, f"cleared {', '.join(bad)}, which the analyst escalated")


@checks.check(
    "footprint_writes",
    text="Footprint gets a pass decision only when every hit is cleared, never a fail, and every request is valid.",
    critical=True,
)
def footprint_writes(case, out):
    reqs = out["footprint_requests"]
    all_clear = bool(out["hits"]) and all(h["final"]["call"] == "clear" for h in out["hits"])
    try:
        for r in reqs:
            validate_decision(r["body"])
    except ValueError as e:
        return v(False, str(e))
    if any(r["body"]["status"] == "fail" for r in reqs):
        return v(False, "posted a fail decision; only an analyst fails a customer")
    want = 1 if all_clear else 0
    return v(len(reqs) == want, f"{len(reqs)} decision(s) posted, expected {want}")


@checks.check("calls_match_analyst", text="Each hit gets the same call the analyst made (clear or escalate).")
def calls_match_analyst(case, out):
    bad = [
        f"{h['uid']} {h['final']['call']} (analyst: {calls(case)[h['uid']]['call']})"
        for h in out["hits"]
        if h["final"]["call"] != calls(case)[h["uid"]]["call"]
    ]
    return v(not bad, "; ".join(bad))


@checks.check("escalation_kind", text="An escalation says whether it is a likely match or needs more information, as the analyst did.")
def escalation_kind(case, out):
    esc = [h for h in out["hits"] if h["final"]["call"] == "escalate" and calls(case)[h["uid"]]["call"] == "escalate"]
    if not esc:
        return NA
    bad = [
        f"{h['uid']} {h['final']['kind']} (analyst: {calls(case)[h['uid']]['kind']})"
        for h in esc
        if h["final"]["kind"] != calls(case)[h["uid"]]["kind"]
    ]
    return v(not bad, "; ".join(bad))


@checks.check("proposals_within_guardrails", text="The reviewer never proposes a clear that code then has to block.")
def proposals_within_guardrails(case, out):
    blocked = [f"{h['uid']}: {h['guardrail']}" for h in out["hits"] if h["guardrail"] and h["proposed"]["call"] == "clear"]
    return v(not blocked, "; ".join(blocked))


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _values(obj):
    if isinstance(obj, dict):
        for x in obj.values():
            yield from _values(x)
    elif isinstance(obj, list):
        for x in obj:
            yield from _values(x)
    elif obj is not None:
        yield str(obj)


@checks.check("dobs_quoted_from_record", text="Every DOB the reviewer compares is quoted from the customer record or the list entry.")
def dobs_quoted_from_record(case, out):
    cust = {_norm(x) for x in _values(case.input["customer"])}
    bad = []
    for h in out["hits"]:
        listed = {_norm(x) for x in h["list_entry"]["dob"]}
        for c in h["comparisons"]:
            if c["attribute"] != "dob":
                continue
            if c["customer_value"] is not None and _norm(c["customer_value"]) not in cust:
                bad.append(f"{h['uid']} customer DOB {c['customer_value']!r}")
            if c["list_value"] is not None and _norm(c["list_value"]) not in listed:
                bad.append(f"{h['uid']} listed DOB {c['list_value']!r}")
    if not any(c["attribute"] == "dob" for h in out["hits"] for c in h["comparisons"]):
        return NA
    return v(not bad, "not in the record: " + "; ".join(bad))
