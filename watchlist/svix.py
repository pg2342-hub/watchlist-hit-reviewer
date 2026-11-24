"""Verify Footprint webhooks. Footprint sends webhooks through Svix, so each request carries
svix-id, svix-timestamp and svix-signature headers, signed with the endpoint's whsec_ secret
(Svix's documented scheme: HMAC-SHA256 over "id.timestamp.body", base64, "v1," prefix)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE_S = 300


class InvalidSignature(Exception):
    pass


def _key(secret: str) -> bytes:
    return base64.b64decode(secret.removeprefix("whsec_"))


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(_key(secret), f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify(secret: str, headers: dict, body: bytes, now: float | None = None) -> None:
    h = {k.lower(): v for k, v in headers.items()}
    try:
        msg_id, ts, sigs = h["svix-id"], int(h["svix-timestamp"]), h["svix-signature"]
    except (KeyError, ValueError) as exc:
        raise InvalidSignature("missing or malformed svix headers") from exc
    if abs((now if now is not None else time.time()) - ts) > TOLERANCE_S:
        raise InvalidSignature("timestamp outside the 5 minute window")
    expected = sign(secret, msg_id, ts, body).split(",", 1)[1]
    for s in sigs.split():
        version, _, value = s.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return
    raise InvalidSignature("no signature matched")
