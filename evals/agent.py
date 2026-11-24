"""Agents for golive-gate. golive calls each with case.input (one customer) and grades the output.

- recorded_agent: the full pipeline, with the Claude review step replayed from recordings/ (no key)
- live_agent:     the full pipeline, calling Claude now and recording each reply (needs ANTHROPIC_API_KEY)
- name_cutoff:    the name-score cutoff rule the gate must block
Only the model's replies are recorded; screening against the SDN snapshot, the guardrails and the
Footprint writes always run as code, so CI tests the current code against the recorded model calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golive_gate import Response

from watchlist import pipeline
from watchlist.baseline import baseline
from watchlist.recorded import ROOT, Tape, recording_reviewer, replay_reviewer

REVIEW_TAPE = Tape(ROOT / "reviewer.jsonl")


def fingerprint():
    """Model, prompt and schema of the review step: a recording made with any other version does not replay."""
    from watchlist import reviewer

    return {"model": reviewer.MODEL, "effort": reviewer.EFFORT, "system": reviewer.SYSTEM, "schema": reviewer.SCHEMA}


_live = None


def _respond(out):
    return Response(out, cost_usd=out.get("_meta", {}).get("cost_usd", 0.0))


def recorded_agent(case_input):
    return _respond(pipeline.run(case_input, replay_reviewer(REVIEW_TAPE, fingerprint())))


def live_agent(case_input):
    global _live
    if _live is None:  # created on first use, so importing this file needs no key
        from watchlist.reviewer import ClaudeReviewer

        _live = recording_reviewer(ClaudeReviewer(), REVIEW_TAPE, fingerprint())
    return _respond(pipeline.run(case_input, _live))


def name_cutoff(case_input):
    return baseline(case_input)
