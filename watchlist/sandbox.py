"""A stand-in for Footprint's API, for tests and the demo (Footprint's own sandbox needs company approval).

It serves the four endpoints the agent uses, in the shapes Footprint's public API reference gives, and
rejects requests Footprint would reject: a missing secret key, a decrypt without a reason or for an
unknown field, a decision body with anything but status and annotation. Users come from the backtest
customers; the OFAC check that leaves the risk signals runs the same screen against the SDN snapshot.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .footprint import DOC_KINDS, VAULT_FIELDS, risk_signals, validate_decision
from .screen import screen

KNOWN_FIELDS = set(VAULT_FIELDS) | {"id.email", "id.phone_number", "id.ssn4", "id.ssn9", "id.citizenships"}


def vault_from_customer(customer: dict) -> dict:
    """Flatten a customer record into Footprint data identifiers."""
    vault = dict(customer["vault"])
    doc = customer.get("document") or {}
    if doc:
        assert doc["type"] in DOC_KINDS
        for f in ("dob", "gender"):
            if f in doc:
                vault[f"document.{doc['type']}.{f}"] = doc[f]
    return vault


class Store:
    def __init__(self, cases: list[dict], api_key: str) -> None:
        self.api_key = api_key
        self.users: dict[str, dict[str, Any]] = {}
        self.decisions: list[dict] = []
        for c in cases:
            inp = c.get("input", c)
            v = inp["customer"]["vault"]
            name = " ".join(x for x in (v["id.first_name"], v.get("id.middle_name"), v["id.last_name"]) if x)
            self.users[inp["fp_id"]] = {
                "vault": vault_from_customer(inp["customer"]),
                "onboarding": {
                    "id": inp["onboarding_id"],
                    "playbook_key": "pb_test_kyc",
                    "status": "fail",
                    "timestamp": "2026-11-01T09:00:00Z",
                },
                "risk_signals": risk_signals(screen(name)),
            }


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method: str) -> None:
            if self.headers.get("X-Footprint-Secret-Key") != store.api_key:
                return self._send(401, {"error": {"message": "invalid secret key"}})
            m = re.fullmatch(r"/users/([^/]+)(/.*)", self.path)
            user = store.users.get(m.group(1)) if m else None
            if user is None:
                return self._send(404, {"error": {"message": "user not found"}})
            fp_id, rest = m.group(1), m.group(2)
            body = None
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"null")
                except json.JSONDecodeError:
                    return self._send(400, {"error": {"message": "body is not JSON"}})
            ob = user["onboarding"]
            if method == "GET" and rest == "/onboardings":
                return self._send(200, {"data": [ob], "meta": {}})
            if method == "GET" and rest == f"/onboardings/{ob['id']}/risk_signals":
                return self._send(200, user["risk_signals"])
            if method == "POST" and rest == "/vault/decrypt":
                if not isinstance(body, dict) or not isinstance(body.get("fields"), list) or not str(body.get("reason", "")).strip():
                    return self._send(400, {"error": {"message": "fields and reason are required"}})
                unknown = [f for f in body["fields"] if f not in KNOWN_FIELDS]
                if unknown:
                    return self._send(400, {"error": {"message": f"unknown data identifiers {unknown}"}})
                return self._send(200, {f: user["vault"].get(f) for f in body["fields"]})
            if method == "POST" and rest == "/decisions":
                try:
                    validate_decision(body)
                except (ValueError, TypeError) as exc:
                    return self._send(400, {"error": {"message": str(exc)}})
                store.decisions.append({"fp_id": fp_id, **body})
                ob["status"] = body["status"]
                return self._send(200, {})
            return self._send(404, {"error": {"message": f"no route {method} {rest}"}})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return Handler


class Sandbox:
    """Run the stand-in on a free local port: `with Sandbox(cases, key) as sb: sb.url`."""

    def __init__(self, cases: list[dict], api_key: str = "sk_test_standin") -> None:
        self.store = Store(cases, api_key)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> Sandbox:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
