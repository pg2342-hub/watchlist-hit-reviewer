"""Screen one customer name against every listed individual and return the hits.

This is the alert generator: it plays the part of the screening vendor whose
hits land in the review queue. Names and every alias are scored; the best
score per list entry is kept, and entries at or above THRESHOLD become hits.
"""

from __future__ import annotations

from functools import lru_cache

from .names import display, normalize, score
from .sdn import Entry, load

THRESHOLD = 0.90


@lru_cache(maxsize=1)
def _index() -> list[tuple[str, str, list[str], str]]:
    out = []
    for e in load().values():
        for n in [e.name, *e.aliases]:
            out.append((e.uid, n, normalize(display(n)), n[:1]))
    return out


def screen(customer_name: str, threshold: float = THRESHOLD) -> list[dict]:
    tokens = normalize(customer_name)
    initials = {t[:1] for t in tokens}
    best: dict[str, tuple[float, str]] = {}
    for uid, listed, ltoks, _ in _index():
        if not initials & {t[:1] for t in ltoks}:  # blocking: share at least one first letter
            continue
        s = score(tokens, ltoks)
        if s >= threshold and s > best.get(uid, (0.0, ""))[0]:
            best[uid] = (s, listed)
    entries = load()
    hits = [{"uid": uid, "matched_name": listed, "name_score": s, "list_entry": entries[uid].facts()} for uid, (s, listed) in best.items()]
    return sorted(hits, key=lambda h: (-h["name_score"], h["uid"]))


def entry(uid: str) -> Entry:
    return load()[uid]
