"""Rule checks plus two Claude judge checks for what a rule cannot state exactly.

The judge runs live (and records each reply) when GOLIVE_LIVE=1, and replays the recorded
replies otherwise, so CI and the demo page need no API key.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks import checks as rule_checks
from golive_gate import NA, CheckSet, judge_check

from watchlist.recorded import ROOT, Tape, recording_judge, replay_judge

JUDGE_TAPE = Tape(ROOT / "judge.jsonl")
_judge = None


def judge(prompt):
    global _judge
    if _judge is None:
        if os.environ.get("GOLIVE_LIVE") == "1":
            from golive_gate import ClaudeJudge

            _judge = recording_judge(ClaudeJudge(model="claude-opus-5-5", effort="low"), JUDGE_TAPE)
        else:
            _judge = replay_judge(JUDGE_TAPE)
    return _judge(prompt)


def _evidence(case):
    return "Customer record:\n" + json.dumps(case.input["customer"], indent=1, sort_keys=True)


def _narratives(out):
    rows = []
    for h in out["hits"]:
        entry = {k: h["list_entry"][k] for k in ("uid", "name", "aliases", "dob", "gender", "nationality", "citizenship", "place_of_birth")}
        rows.append(
            f"Hit {h['uid']} on name {h['matched_name']!r}. List entry: {json.dumps(entry, sort_keys=True)}\n"
            f"Final call: {h['final']['call']} ({h['final']['kind']})\n"
            f"Narrative: {h['narrative']}\nNext step: {h['next_step']}"
        )
    return "\n\n".join(rows)


checks = CheckSet(rule_checks)

_grounded = judge_check(
    "narratives_grounded",
    "Every narrative uses only facts that appear in the customer record or that hit's list entry (no invented dates, "
    "places, documents or conclusions), states the final call correctly, and names the fact the call rests on. A "
    "narrative that describes a cleared hit as escalated or the reverse, or quotes a value that is not in the evidence, fails.",
    judge=judge,
    render_case=_evidence,
    render_output=_narratives,
    critical=True,
)
_actionable = judge_check(
    "escalations_actionable",
    "For each escalated hit, the next step tells the next analyst exactly what to obtain or check to resolve it (for "
    "example a government ID showing full date of birth, or place of birth), and it fits why the hit was escalated. "
    "Generic text such as 'review further' fails. Ignore cleared hits.",
    judge=judge,
    render_case=_evidence,
    render_output=_narratives,
)


@checks.check("narratives_grounded", text=_grounded.text.split(".")[0] + ".", critical=True)
def narratives_grounded(case, out):
    if not out["hits"]:
        return NA
    return _grounded.run(case, out)


@checks.check("escalations_actionable", text=_actionable.text.split(".")[0] + ".")
def escalations_actionable(case, out):
    if not any(h["final"]["call"] == "escalate" for h in out["hits"]):
        return NA
    return _actionable.run(case, out)
