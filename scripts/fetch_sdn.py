"""Download the OFAC SDN list (public, U.S. Treasury) into data/ofac/.

Three files from OFAC's Sanctions List Service, in the legacy CSV layout:
SDN.CSV (one row per listed person or entity), ALT.CSV (aliases) and ADD.CSV (addresses).
The screener in watchlist/screen.py reads these. Run it again to refresh the snapshot.
"""

import datetime as dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/"
FILES = ["SDN.CSV", "ALT.CSV", "ADD.CSV"]
OUT = Path(__file__).resolve().parent.parent / "data" / "ofac"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"source": BASE, "fetched_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": {}}
    for name in FILES:
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "watchlist-hit-reviewer"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
        if len(body) < 10_000:
            print(f"{name}: only {len(body)} bytes, refusing to overwrite", file=sys.stderr)
            return 1
        (OUT / name.lower()).write_bytes(body)
        manifest["files"][name.lower()] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        print(f"{name}: {len(body):,} bytes")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
