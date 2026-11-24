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

  GET  /users/{fp_id}/onboardings  -> {data: [{id, playbook_key, status, timestamp}], meta}
  POST /users/{fp_id}/vault/decrypt  {"fields": [...], "reason": str} -> {field: value}

FakeFootprint validates writes and records them, for the backtest. FootprintClient sends the
real requests over HTTP; the tests and demo run it against watchlist/sandbox.py, a stand-in that
answers in Footprint's shapes. Against Footprint itself it needs a key from an approved account.
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


# Footprint stores document data per document kind; these are the identifiers the agent decrypts for
# DOB and gender off the ID document (document.<kind>.<field>; confirm exact names on an account).
DOC_KINDS = ("passport", "drivers_license", "id_card")
VAULT_FIELDS = [
    "id.first_name",
    "id.middle_name",
    "id.last_name",
    "id.dob",
    "id.nationality",
    "id.address_line1",
    "id.city",
    "id.state",
    "id.zip",
    "id.country",
] + [f"document.{k}.{f}" for k in DOC_KINDS for f in ("dob", "gender")]


def customer_record(decrypted: dict) -> dict:
    """Turn a decrypt response into the record the reviewer reads: vault fields plus the ID document."""
    vault = {k: v for k, v in decrypted.items() if k.startswith("id.")}  # null stays null: the field is empty
    document: dict = {}
    for kind in DOC_KINDS:
        fields = {f: decrypted.get(f"document.{kind}.{f}") for f in ("dob", "gender")}
        fields = {f: v for f, v in fields.items() if v not in (None, "")}
        if fields:
            document = {"type": kind, **fields}
            break
    return {"vault": vault, "document": document}


class FootprintClient:
    """Footprint's API over HTTP: the four requests the agent makes, each logged to trace.

    Auth is the secret key in the X-Footprint-Secret-Key header. base_url defaults to Footprint's API;
    the demo and tests point it at the stand-in in watchlist/sandbox.py, which answers in the same shapes.
    """

    def __init__(self, api_key: str | None = None, base_url: str = BASE) -> None:
        self.api_key = api_key or os.environ["FOOTPRINT_API_KEY"]
        self.base_url = base_url.rstrip("/")
        self.requests: list[dict[str, Any]] = []  # writes, in the shape FakeFootprint records
        self.trace: list[dict[str, Any]] = []  # every request, for the walkthrough

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        import requests

        r = requests.request(method, self.base_url + path, json=body, headers={"X-Footprint-Secret-Key": self.api_key}, timeout=30)
        self.trace.append({"method": method, "path": path, "body": body, "status": r.status_code})
        r.raise_for_status()
        return r.json()

    def list_onboardings(self, fp_id: str) -> list[dict]:
        return self._call("GET", f"/users/{fp_id}/onboardings")["data"]

    def get_risk_signals(self, fp_id: str, onboarding_id: str) -> list[dict]:
        signals = self._call("GET", f"/users/{fp_id}/onboardings/{onboarding_id}/risk_signals")
        for s in signals:
            validate_signal(s)
        return signals

    def decrypt(self, fp_id: str, fields: list[str], reason: str) -> dict:
        return self._call("POST", f"/users/{fp_id}/vault/decrypt", {"fields": fields, "reason": reason})

    def post_decision(self, fp_id: str, status: str, annotation: str) -> dict:
        body = {"status": status, "annotation": annotation}
        validate_decision(body)
        out = self._call("POST", f"/users/{fp_id}/decisions", body)
        self.requests.append({"method": "POST", "path": f"/users/{fp_id}/decisions", "body": body})
        return out
