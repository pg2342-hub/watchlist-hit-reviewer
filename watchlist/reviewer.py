"""The Claude review step: one call per customer, a proposed call and a narrative for every hit.

The model reads the bank's procedure, the customer record and each list entry, and
returns structured output only: for each hit a proposed call (clear or escalate), the
kind of call, the attributes it compared with both values, the one fact the call
rests on, a narrative for the audit file, and for an escalation the next step.
Code then checks the deciding fact before any clear is posted (see pipeline.py).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MODEL = "claude-opus-5-5"
EFFORT = "low"
# USD per million tokens (input, output), first-party list prices.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-opus-4-8": (5.00, 25.00), "claude-sonnet-5-5": (2.00, 10.00)}

SOP = (Path(__file__).resolve().parent.parent / "procedures" / "sanctions_review_sop.md").read_text()

_nullable = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reviews"],
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["uid", "call", "kind", "comparisons", "deciding_fact", "narrative", "next_step"],
                "properties": {
                    "uid": {"type": "string"},
                    "call": {"type": "string", "enum": ["clear", "escalate"]},
                    "kind": {"type": "string", "enum": ["dob", "gender", "likely_match", "need_info"]},
                    "comparisons": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["attribute", "customer_value", "list_value", "result"],
                            "properties": {
                                "attribute": {
                                    "type": "string",
                                    "enum": ["name", "dob", "gender", "nationality", "place_of_birth", "address"],
                                },
                                "customer_value": _nullable,
                                "list_value": _nullable,
                                "result": {"type": "string", "enum": ["match", "mismatch", "possible", "unknown"]},
                            },
                        },
                    },
                    "deciding_fact": {"type": "string"},
                    "narrative": {"type": "string"},
                    "next_step": _nullable,
                },
            },
        }
    },
}

SYSTEM = f"""You are a sanctions screening reviewer at a bank. Screening has raised one or more OFAC SDN name hits on a customer at onboarding. Review every hit, following the bank's procedure exactly:

{SOP}

Rules for your output:
- One review per hit, with the hit's uid. kind is dob or gender for a clear, likely_match or need_info for an escalation.
- comparisons: the attributes you compared. Copy customer_value and list_value exactly as they appear in the record (dates as written there); use null when the value is not on file. List every listed DOB you compared, one comparison each.
- deciding_fact: the one fact the call rests on, in one sentence.
- narrative: three to five sentences for the audit file, using only facts in the record.
- next_step: for an escalation, what the next reviewer must obtain or check; null for a clear.
When the record does not let you rule the listed person out, escalate. A wrong clear lets a sanctioned person open an account; an unneeded escalation costs an analyst a few minutes."""


def view(case_input: dict, hits: list[dict]) -> dict:
    """What the reviewer sees: the customer record and each hit's list entry (no analyst labels)."""
    return {
        "fp_id": case_input["fp_id"],
        "customer": case_input["customer"],
        "hits": [
            {"uid": h["uid"], "matched_name": h["matched_name"], "name_score": h["name_score"], "list_entry": h["list_entry"]} for h in hits
        ],
    }


def prompt(v: dict) -> str:
    return "Customer record and screening hits:\n" + json.dumps(v, indent=1, sort_keys=True)


class ClaudeReviewer:
    """Calls Claude with structured output; returns the parsed reviews plus cost and latency."""

    def __init__(self, model: str = MODEL, effort: str = EFFORT, client: Any = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def __call__(self, v: dict) -> dict:
        start = time.perf_counter()
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt(v)}],
        )
        latency = time.perf_counter() - start
        if response.stop_reason == "refusal":
            raise RuntimeError("the model declined this review")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("the review was cut off at max_tokens")
        text = next(b.text for b in response.content if b.type == "text")
        out = json.loads(text)
        out["_meta"] = {
            "model": getattr(response, "model", self.model),
            "latency_s": round(latency, 3),
            "cost_usd": round(self.cost(response), 6),
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return out

    def cost(self, response: Any) -> float:
        price = PRICES.get(getattr(response, "model", self.model)) or PRICES.get(self.model, (0.0, 0.0))
        usage = response.usage
        written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        tokens_in = (usage.input_tokens or 0) + written * 1.25 + cached * 0.05
        return (tokens_in * price[0] + (usage.output_tokens or 0) * price[1]) / 1e6
