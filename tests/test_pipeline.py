import copy
import json
from pathlib import Path

import pytest
import yaml
from golive_gate import FAIL
from oracle import review

from watchlist import pipeline
from watchlist.footprint import FakeFootprint, validate_decision

CASES = yaml.safe_load(Path("evals/cases.yml").read_text())["cases"]


def run(case, reviewer=review):
    return pipeline.run(case["input"], reviewer)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_oracle_matches_every_analyst_call(case):
    out = run(case)
    assert sorted(h["uid"] for h in out["hits"]) == case["expected"]["hits"]
    for h in out["hits"]:
        want = case["expected"]["calls"][h["uid"]]
        assert (h["final"]["call"], h["final"]["kind"]) == (want["call"], want["kind"]), h["uid"]
        assert h["guardrail"] is None


def test_guardrail_blocks_unsupported_clear():
    case = next(c for c in CASES if c["group"] == "need_info")

    def reckless(v):
        r = review(v)
        for x in r["reviews"]:
            x["call"], x["kind"] = "clear", "dob"
        return r

    out = run(case, reckless)
    assert all(h["final"]["call"] == "escalate" for h in out["hits"] if case["expected"]["calls"][h["uid"]]["call"] == "escalate")
    assert out["footprint_requests"] == []
    assert any(h["guardrail"] and h["guardrail"].startswith("clear blocked") for h in out["hits"])


def test_exact_dob_match_always_escalates():
    case = next(c for c in CASES if c["group"] == "true_match")

    def clears_all(v):
        r = review(v)
        for x in r["reviews"]:
            x["call"], x["kind"] = "clear", "gender"
        return r

    out = run(case, clears_all)
    target = [u for u, k in case["expected"]["calls"].items() if k["kind"] == "likely_match"]
    for h in out["hits"]:
        if h["uid"] in target:
            assert h["final"] == {"call": "escalate", "kind": "likely_match"}


def test_missing_review_is_escalated():
    case = next(c for c in CASES if c["group"] == "dob_clear")
    out = run(case, lambda v: {"reviews": [], "_meta": {}})
    assert all(h["final"]["call"] == "escalate" for h in out["hits"])
    assert out["footprint_requests"] == []


def test_pass_posted_only_when_all_clear():
    for case in CASES:
        out = run(case)
        all_clear = all(h["final"]["call"] == "clear" for h in out["hits"])
        assert len(out["footprint_requests"]) == (1 if all_clear else 0)
        for r in out["footprint_requests"]:
            assert r["body"]["status"] == "pass"


def test_decision_body_validation():
    validate_decision({"status": "pass", "annotation": "x"})
    for bad in (
        {"status": "approve", "annotation": "x"},
        {"status": "pass"},
        {"status": "pass", "annotation": " "},
        {"status": "pass", "annotation": "x", "extra": 1},
    ):
        with pytest.raises(ValueError):
            validate_decision(bad)
    with pytest.raises(ValueError):
        FakeFootprint().post_decision("fp_id_test_1", "fail ", "x")


def test_reviewer_view_has_no_labels():
    v = pipeline.rv.view(CASES[0]["input"], pipeline.screen("Ayman al-Zawahiri"))
    assert "expected" not in json.dumps(v) and "analyst" not in json.dumps(v)


def test_replay_needs_no_api_and_matches(monkeypatch, tmp_path):
    from watchlist.recorded import Tape, recording_reviewer, replay_reviewer

    tape = Tape(tmp_path / "r.jsonl")
    rec = recording_reviewer(review, tape, "fp")
    first = [run(c, rec) for c in CASES[:5]]
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)
    rep = replay_reviewer(Tape(tmp_path / "r.jsonl"), "fp")
    again = [run(c, rep) for c in CASES[:5]]
    assert [[h["final"] for h in o["hits"]] for o in first] == [[h["final"] for h in o["hits"]] for o in again]


def test_rule_checks_pass_for_oracle_and_catch_a_false_clear():
    from checks import checks
    from golive_gate.cases import Case

    for c in CASES:
        case = Case(id=c["id"], group=c["group"], title=c["title"], input=c["input"], expected=c["expected"])
        out = run(c)
        for chk in checks:
            verdict = chk.run(case, out)
            assert verdict.result != FAIL, (c["id"], chk.id, verdict)
    c = next(c for c in CASES if c["group"] == "true_match")
    case = Case(id=c["id"], group=c["group"], title=c["title"], input=c["input"], expected=c["expected"])
    out = copy.deepcopy(run(c))
    for h in out["hits"]:
        if c["expected"]["calls"][h["uid"]]["call"] == "escalate":
            h["final"] = {"call": "clear", "kind": "dob"}
    assert checks["no_false_clear"].run(case, out).result == FAIL
