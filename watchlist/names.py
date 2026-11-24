"""Name normalisation and fuzzy matching, standard library only.

Screening vendors compare names token by token so that "Ali, Mohammed" matches
"Mohamed Ali" and a dropped middle name still hits. The score here is the mean
Jaro-Winkler similarity of the best one-to-one token pairing, taken over the
shorter name's tokens, with a small penalty when the longer name has tokens
that pair with nothing.
"""

from __future__ import annotations

import re
import unicodedata

_PARTICLES = {"al", "el", "bin", "ibn", "abu", "abd", "de", "del", "la", "van", "von", "da", "dos", "der"}
_TITLES = {"dr", "mr", "mrs", "ms", "sheikh", "shaykh", "haji", "hajji", "general", "colonel", "col", "gen"}


def normalize(name: str) -> list[str]:
    """Lowercase, strip accents and punctuation, split into tokens, drop titles."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9\s-]", " ", text).replace("-", " ")
    return [t for t in text.split() if t and t not in _TITLES]


def display(sdn_name: str) -> str:
    """'LAST, First Middle' as listed by OFAC -> 'First Middle LAST'."""
    if "," in sdn_name:
        last, first = sdn_name.split(",", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return sdn_name.strip()


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(max(la, lb) // 2 - 1, 0)
    used_b = [False] * lb
    matches_a = []
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not used_b[j] and b[j] == ch:
                used_b[j] = True
                matches_a.append(ch)
                break
    m = len(matches_a)
    if not m:
        return 0.0
    matches_b = [b[j] for j in range(lb) if used_b[j]]
    transpositions = sum(x != y for x, y in zip(matches_a, matches_b, strict=True)) / 2
    jaro = (m / la + m / lb + (m - transpositions) / m) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def _significant(tokens: list[str]) -> list[str]:
    kept = [t for t in tokens if t not in _PARTICLES]
    return kept or tokens


def score(a: str | list[str], b: str | list[str]) -> float:
    """Similarity of two names in [0, 1], independent of token order."""
    ta = _significant(normalize(a) if isinstance(a, str) else a)
    tb = _significant(normalize(b) if isinstance(b, str) else b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # greedy best pairing: take the highest-scoring pair, remove both, repeat
    pairs = sorted(((jaro_winkler(s, t), i, j) for i, s in enumerate(short) for j, t in enumerate(long_)), reverse=True)
    used_i, used_j, total = set(), set(), 0.0
    for sim, i, j in pairs:
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        total += sim
        if len(used_i) == len(short):
            break
    base = total / len(short)
    unpaired = len(long_) - len(short)
    return round(base * (1 - 0.07 * unpaired), 4)
