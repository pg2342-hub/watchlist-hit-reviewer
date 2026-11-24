"""The Footprint side of a review: read a user's risk signals, write a manual decision.

Footprint's sandbox needs approval, so this repo does not call Footprint. The two
requests below follow Footprint's public API reference (docs.onefootprint.com,
checked 2026-11-24):

  GET  /users/{fp_id}/onboardings/{onboarding_id}/risk_signals
       -> [{reason_code, note, description, severity, scopes, timestamp}]
       reason_code includes watchlist_hit_ofac / watchlist_clear_ofac;
       severity is info, low, medium or high
  POST /users/{fp_id}/decisions  {"status": "pass" | "fail", "annotation": str}
       creates a manual decision and clears the outstanding manual review

FakeFootprint validates every request against those shapes and records it, so the
pipeline's writes can be checked. FootprintClient sends the same requests to the
real API when FOOTPRINT_API_KEY is set (untested here: no sandbox access).
"""

from __future__ import annotations

import os
from typing import Any

BASE = "https://api.onefootprint.com"
SEVERITIES = {"info", "low", "medium", "high"}
REASON_CODES = {"watchlist_hit_ofac", "watchlist_clear_ofac"}
SCOPES = {"name", "dob", "address", "document", "country"}


def risk_signals(hits: list[dict], when: str = "2026-11-01T09:00:00Z") -> list[dict]:
    """The risk signals a screening run would leave on the onboarding, one per hit."""
    if not hits:
        return [
            {
                "reason_code": "watchlist_clear_ofac",
                "note": "No OFAC hit",
                "description": "No OFAC SDN hit.",
                "severity": "info",
                "scopes": ["name"],
                "timestamp": when,
            }
        ]
    return [
        {
            "reason_code": "watchlist_hit_ofac",
            "note": "OFAC SDN name hit",
            "description": f"Name matched OFAC SDN entry {h['uid']} ({h['matched_name']}), score {h['name_score']:.2f}.",
            "severity": "high",
            "scopes": ["name"],
            "timestamp": when,
        }
        for h in hits
    ]


def validate_signal(s: dict) -> None:
    missing = {"reason_code", "note", "description", "severity", "scopes", "timestamp"} - s.keys()
    if missing:
        raise ValueError(f"risk signal missing {sorted(missing)}")
    if s["reason_code"] not in REASON_CODES or s["severity"] not in SEVERITIES or not set(s["scopes"]) <= SCOPES:
        raise ValueError(f"risk signal has a value Footprint does not list: {s}")


def validate_decision(body: dict) -> None:
    if set(body) != {"status", "annotation"}:
        raise ValueError(f"decision body must have exactly status and annotation, got {sorted(body)}")
    if body["status"] not in {"pass", "fail"}:
        raise ValueError(f"status must be pass or fail, got {body['status']!r}")
    if not isinstance(body["annotation"], str) or not body["annotation"].strip():
        raise ValueError("annotation must be a non-empty string")


class FakeFootprint:
    """Records validated requests instead of sending them."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post_decision(self, fp_id: str, status: str, annotation: str) -> dict:
        body = {"status": status, "annotation": annotation}
        validate_decision(body)
        self.requests.append({"method": "POST", "path": f"/users/{fp_id}/decisions", "body": body})
        return {}


class FootprintClient:
    """The same requests against the real API (needs a key from an approved Footprint account)."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ["FOOTPRINT_API_KEY"]

    def get_risk_signals(self, fp_id: str, onboarding_id: str) -> list[dict]:
        import requests

        r = requests.get(f"{BASE}/users/{fp_id}/onboardings/{onboarding_id}/risk_signals", auth=(self.api_key, ""), timeout=30)
        r.raise_for_status()
        return r.json()

    def post_decision(self, fp_id: str, status: str, annotation: str) -> dict:
        import requests

        body = {"status": status, "annotation": annotation}
        validate_decision(body)
        r = requests.post(f"{BASE}/users/{fp_id}/decisions", auth=(self.api_key, ""), json=body, timeout=30)
        r.raise_for_status()
        return r.json()
