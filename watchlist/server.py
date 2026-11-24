"""Webhook receiver: point Footprint's webhook endpoint (dashboard, Webhooks) at /webhooks/footprint.

    FOOTPRINT_API_KEY=sk_test_... FOOTPRINT_WEBHOOK_SECRET=whsec_... ANTHROPIC_API_KEY=... \\
        python -m watchlist.server --port 8000 --out cases/

Each request is checked against the Svix signature, acknowledged with 202, then reviewed on a worker
thread. Case briefs for escalated customers are written to --out as <fp_id>.md.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import agent
from .footprint import BASE, FootprintClient
from .svix import InvalidSignature, verify


def make_app(secret: str, work: queue.Queue):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.path != "/webhooks/footprint":
                self.send_response(404)
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                verify(secret, dict(self.headers), body)
                event = json.loads(body)
            except (InvalidSignature, json.JSONDecodeError):
                self.send_response(401)
                self.end_headers()
                return
            work.put(event)
            self.send_response(202)
            self.end_headers()

    return Handler


def worker(work: queue.Queue, out: Path, review, api_key: str, base_url: str) -> None:
    while True:
        event = work.get()
        if event is None:
            return
        result = agent.handle(event, FootprintClient(api_key, base_url), review)
        print(f"{result['fp_id']}: {result['action']}", flush=True)
        if result.get("brief"):
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{result['fp_id']}.md").write_text(result["brief"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--out", default="cases")
    ap.add_argument("--footprint-url", default=os.environ.get("FOOTPRINT_BASE_URL", BASE))
    a = ap.parse_args()
    from .reviewer import ClaudeReviewer

    work: queue.Queue = queue.Queue()
    threading.Thread(
        target=worker, args=(work, Path(a.out), ClaudeReviewer(), os.environ["FOOTPRINT_API_KEY"], a.footprint_url), daemon=True
    ).start()
    ThreadingHTTPServer(("0.0.0.0", a.port), make_app(os.environ["FOOTPRINT_WEBHOOK_SECRET"], work)).serve_forever()


if __name__ == "__main__":
    main()
