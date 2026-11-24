"""Load the OFAC SDN list (legacy CSV layout) and parse the facts screening needs.

SDN.CSV columns: uid, name, type, program, title, call sign, vessel type, tonnage,
GRT, vessel flag, vessel owner, remarks. Dates of birth, places of birth,
nationality, citizenship and gender live in the free-text remarks, separated by
semicolons ("DOB 10 Dec 1948; POB Giza, Egypt; nationality Egypt; Gender Male").
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "ofac"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NULL = "-0-"


@dataclass(frozen=True)
class Dob:
    """A listed date of birth: an exact day, a month, a year, or a year range (circa = +/- 1 year)."""

    text: str
    year_from: int
    year_to: int
    month: int | None = None
    day: int | None = None

    @property
    def exact(self) -> bool:
        return self.day is not None and self.year_from == self.year_to

    def compare(self, iso: str) -> str:
        """'match', 'mismatch' or 'possible' against a full customer DOB (YYYY-MM-DD)."""
        y, m, d = (int(x) for x in iso.split("-"))
        if not self.year_from <= y <= self.year_to:
            return "mismatch"
        if self.month is not None and self.year_from == self.year_to and self.month != m:
            return "mismatch"
        if self.day is not None and self.year_from == self.year_to and self.day != d:
            return "mismatch"
        return "match" if self.exact else "possible"


def _clean(v: str) -> str:
    v = v.strip()
    return "" if v == NULL else v


def parse_dob(text: str) -> Dob | None:
    t = text.strip().rstrip(".")
    circa = t.lower().startswith("circa")
    t = re.sub(r"^circa\s+", "", t, flags=re.I)
    rng = re.match(r"^(.*?)\s+to\s+(.*)$", t)
    if rng:
        a, b = parse_dob(rng.group(1)), parse_dob(rng.group(2))
        if a and b:
            return Dob(text.strip().rstrip("."), a.year_from, b.year_to)
        return None
    m = re.match(r"^(\d{1,2}) ([A-Za-z]{3}) (\d{4})$", t)
    if m:
        y = int(m.group(3))
        if circa:
            return Dob(text.strip().rstrip("."), y - 1, y + 1)
        return Dob(text.strip().rstrip("."), y, y, MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.match(r"^([A-Za-z]{3}) (\d{4})$", t)
    if m:
        y = int(m.group(2))
        return Dob(text.strip().rstrip("."), y, y, MONTHS[m.group(1).lower()])
    m = re.match(r"^(\d{4})$", t)
    if m:
        y = int(m.group(1))
        return Dob(text.strip().rstrip("."), y - 1 if circa else y, y + 1 if circa else y)
    return None


@dataclass
class Entry:
    uid: str
    name: str
    type: str
    programs: list[str]
    aliases: list[str] = field(default_factory=list)
    dobs: list[Dob] = field(default_factory=list)
    pob: list[str] = field(default_factory=list)
    nationality: list[str] = field(default_factory=list)
    citizen: list[str] = field(default_factory=list)
    gender: str | None = None
    addresses: list[str] = field(default_factory=list)
    remarks: str = ""

    def facts(self) -> dict:
        """What a reviewer sees for this list entry, as plain values the agent can cite."""
        return {
            "uid": self.uid,
            "name": self.name,
            "aliases": self.aliases,
            "programs": self.programs,
            "dob": [d.text for d in self.dobs],
            "place_of_birth": self.pob,
            "nationality": self.nationality,
            "citizenship": self.citizen,
            "gender": self.gender,
            "addresses": self.addresses,
        }


def _parse_remarks(e: Entry) -> None:
    for seg in e.remarks.split(";"):
        s = seg.strip().rstrip(".")
        low = s.lower()
        if low.startswith(("dob ", "alt. dob ")):
            d = parse_dob(s.split("DOB", 1)[1])
            if d:
                e.dobs.append(d)
        elif low.startswith(("pob ", "alt. pob ")):
            e.pob.append(s.split("POB", 1)[1].strip())
        elif low.startswith(("nationality ", "alt. nationality ")):
            e.nationality.append(s.split("nationality", 1)[1].strip())
        elif low.startswith(("citizen ", "alt. citizen ")):
            e.citizen.append(s.split("citizen", 1)[1].strip())
        elif low.startswith("gender "):
            e.gender = s.split(" ", 1)[1].strip()


def _read(name: str) -> list[list[str]]:
    with (DATA / name).open(encoding="latin-1", newline="") as f:
        return [r for r in csv.reader(f) if len(r) > 3]


@lru_cache(maxsize=1)
def load() -> dict[str, Entry]:
    """Every listed individual, keyed by OFAC uid (entities and vessels are not screened here)."""
    entries: dict[str, Entry] = {}
    for r in _read("sdn.csv"):
        if _clean(r[2]) != "individual":
            continue
        e = Entry(r[0].strip(), _clean(r[1]), "individual", [p.strip("[] ") for p in _clean(r[3]).split("] [")])
        e.remarks = _clean(r[11]) if len(r) > 11 else ""
        _parse_remarks(e)
        entries[e.uid] = e
    for r in _read("alt.csv"):
        e = entries.get(r[0].strip())
        if e and _clean(r[3]):
            e.aliases.append(_clean(r[3]))
    for r in _read("add.csv"):
        e = entries.get(r[0].strip())
        if e:
            parts = [_clean(x) for x in r[2:5]]
            line = ", ".join(p for p in parts if p)
            if line:
                e.addresses.append(line)
    return entries
