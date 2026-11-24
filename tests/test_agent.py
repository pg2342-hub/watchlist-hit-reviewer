"""The agent against the Footprint stand-in, over real HTTP, with the scripted SOP reviewer."""

import base64
import json
import queue
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
import yaml
from oracle import review

from watchlist import agent
from watchlist.footprint import FootprintClient
from watchlist.sandbox import Sandbox
from watchlist.server import make_app
from watchlist.svix import InvalidSignature, sign, verify

CASES = yaml.safe_load((Path(__file__).resolve().parent.parent / "evals" / "cases.yml").read_text())["cases"]
SECRET = "whsec_" + base64.b64encode(b"test-secret-0123456789").decode()


def event(c, **kw):
    i = c["input"]
    return {
        "event_kind": "footprint.onboarding.completed",
        "fp_id": i["fp_id"],
        "onboarding_id": i["onboarding_id"],
        "status": "fail",
        "requires_manual_review": True,
        **kw,
    }


@pytest.fixture(scope="module")
def sandbox():
    with Sandbox(CASES) as sb:
        yield sb


def test_every_customer_round_trips_and_only_clears_post(sandbox):
    for c in CASES:
        fp = FootprintClient("sk_test_standin", sandbox.url)
        r = agent.handle(event(c), fp, review)
        assert r["customer"] == c["input"]["customer"], c["id"]  # decrypt rebuilds the same record
        posts = [t for t in fp.trace if t["path"].endswith("/decisions")]
        if r["action"] == "posted_pass":
            assert [p["body"]["status"] for p in posts] == ["pass"]
            assert all(h["final"]["call"] == "clear" for h in r["review"]["hits"])
        else:
            assert posts == [] and r["brief"]
    assert {d["status"] for d in sandbox.store.decisions} == {"pass"}


def test_decrypt_is_scoped_and_logged(sandbox):
    fp = FootprintClient("sk_test_standin", sandbox.url)
    agent.handle(event(CASES[0]), fp, review)
    dec = next(t for t in fp.trace if t["path"].endswith("/vault/decrypt"))
    assert dec["body"]["reason"] and not any("ssn" in f for f in dec["body"]["fields"])


def test_ignores_events_that_need_no_review(sandbox):
    fp = FootprintClient("sk_test_standin", sandbox.url)
    assert agent.handle(event(CASES[0], requires_manual_review=False), fp, review)["action"] == "ignored"
    assert fp.trace == []
    assert agent.handle({"event_kind": "footprint.vault.updated"}, fp, review)["action"] == "ignored"


def test_standin_rejects_what_footprint_would(sandbox):
    fp_id = CASES[0]["input"]["fp_id"]
    bad_key = FootprintClient("sk_test_wrong", sandbox.url)
    with pytest.raises(requests.HTTPError):
        bad_key.list_onboardings(fp_id)
    fp = FootprintClient("sk_test_standin", sandbox.url)
    with pytest.raises(requests.HTTPError):
        fp.decrypt(fp_id, ["id.first_name"], reason="")
    with pytest.raises(requests.HTTPError):
        fp.decrypt(fp_id, ["id.favorite_color"], reason="test")
    assert fp.list_onboardings(fp_id)[0]["id"] == CASES[0]["input"]["onboarding_id"]


def test_svix_signature():
    body = b'{"a":1}'
    now = int(time.time())
    headers = {"svix-id": "msg_1", "svix-timestamp": str(now), "svix-signature": sign(SECRET, "msg_1", now, body)}
    verify(SECRET, headers, body)
    with pytest.raises(InvalidSignature):
        verify(SECRET, headers, b'{"a":2}')
    with pytest.raises(InvalidSignature):
        verify(SECRET, headers, body, now=now + 3600)


def test_webhook_endpoint_checks_signature():
    work = queue.Queue()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_app(SECRET, work))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/webhooks/footprint"
    body = json.dumps(event(CASES[0])).encode()
    now = int(time.time())
    ok = {"svix-id": "msg_2", "svix-timestamp": str(now), "svix-signature": sign(SECRET, "msg_2", now, body)}
    assert requests.post(url, data=body, headers=ok, timeout=5).status_code == 202
    assert requests.post(url, data=body, headers={**ok, "svix-signature": "v1,AAAA"}, timeout=5).status_code == 401
    srv.shutdown()
    assert work.get_nowait()["fp_id"] == CASES[0]["input"]["fp_id"] and work.empty()
