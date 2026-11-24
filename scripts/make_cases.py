"""Build the 40 historical review cases in evals/cases.yml from the real OFAC SDN snapshot.

Every customer is constructed. Each one is screened against the full SDN list by
watchlist/screen.py, and every hit gets the call an analyst following
procedures/sanctions_review_sop.md made on it. Customers are of six kinds:

  true_match  the customer is the listed person (name or alias, listed DOB, nationality)
  dob_clear   same name as a listed person, born years apart
  gender      same name, DOB inside the listed year, the other gender on the ID document
  need_info   the record cannot rule the listed person out (year-only DOB, no DOB on file)
  multi_dob   the listed person has several DOBs; the customer's matches one, or none
  many_hits   a common name that hits several listed people at once

Run: python scripts/make_cases.py   (deterministic, seed below)
"""

from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from watchlist.names import display, normalize  # noqa: E402
from watchlist.screen import screen  # noqa: E402
from watchlist.sdn import load  # noqa: E402

random.seed(20261124)
E = load()
STREETS = ["Maple Ave", "Oak St", "Cedar Ln", "Lakeview Dr", "Park Pl", "Willow Way", "Main St", "Hillcrest Rd", "Elm St", "Sunset Blvd"]
CITIES = [
    ("Kansas City", "MO", "64105"),
    ("Overland Park", "KS", "66210"),
    ("Austin", "TX", "78701"),
    ("Miami", "FL", "33130"),
    ("Chicago", "IL", "60607"),
    ("Houston", "TX", "77002"),
    ("Phoenix", "AZ", "85004"),
    ("Brooklyn", "NY", "11201"),
    ("Denver", "CO", "80202"),
    ("San Jose", "CA", "95113"),
]
MAX_HITS = 5
# Well-known listed people: a constructed customer sharing their name would distract on the demo page.
SKIP = ("SHAIKH MOHAMMED", "KISLYAK", "SAFIEDDINE", "NASRALLAH", "ZAWAHIRI", "BIN LADIN", "QADHAFI", "AL-ADL", "VULIN")
used: set[str] = set()


def iso(d: dt.date) -> str:
    return d.isoformat()


def listed_date(entry) -> dt.date:
    d = next(x for x in entry.dobs if x.exact)
    return dt.date(d.year_from, d.month, d.day)


def shifted(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year + years, month=random.randint(1, 12))
    except ValueError:
        return d.replace(year=d.year + years, month=random.randint(1, 12), day=28)


def first_last(name: str) -> tuple[str, str, str]:
    """OFAC 'LAST, First Middle' -> (first, middle, last) in title case."""
    if "," not in name:
        parts = name.split()
        return parts[0].title(), "", " ".join(parts[1:]).title()
    last, rest = (x.strip() for x in name.split(",", 1))
    rest_parts = rest.split()
    return rest_parts[0].title(), " ".join(rest_parts[1:]).title(), last.title()


def customer(first, middle, last, dob, nationality, *, doc=None, drop_vault_dob=False) -> dict:
    street = f"{random.randint(100, 9899)} {random.choice(STREETS)}"
    city, state, zip_ = random.choice(CITIES)
    vault = {
        "id.first_name": first,
        "id.middle_name": middle or None,
        "id.last_name": last,
        "id.dob": None if (drop_vault_dob or dob is None) else dob,
        "id.nationality": nationality,
        "id.address_line1": street,
        "id.city": city,
        "id.state": state,
        "id.zip": zip_,
        "id.country": "US",
    }
    return {"vault": vault, "document": doc or {}}


def full_name(c: dict) -> str:
    v = c["vault"]
    return " ".join(x for x in (v["id.first_name"], v["id.middle_name"], v["id.last_name"]) if x)


def sop_call(cust: dict, hit: dict, target: str | None) -> dict:
    """The analyst's call on one hit, following procedures/sanctions_review_sop.md."""
    e = E[hit["uid"]]
    dob = cust["vault"]["id.dob"] or cust["document"].get("dob")
    gender = cust["document"].get("gender")
    if hit["uid"] == target:
        return {"call": "escalate", "kind": "likely_match", "basis": "customer is the listed person"}
    results = [d.compare(dob) for d in e.dobs] if dob else []
    if "match" in results:
        return {"call": "escalate", "kind": "likely_match", "basis": "full DOB matches a listed DOB"}
    if dob and e.dobs and all(r == "mismatch" for r in results):
        return {"call": "clear", "kind": "dob", "basis": "DOB differs from every listed DOB"}
    if gender and e.gender and gender[0].upper() != e.gender[0].upper():
        return {"call": "clear", "kind": "gender", "basis": "gender on the ID document differs from the listed gender"}
    return {"call": "escalate", "kind": "need_info", "basis": "nothing on file rules the listed person out"}


cases: list[dict] = []


def add(group: str, title: str, cust: dict, target: str | None, *, require: str | None = None) -> bool:
    hits = screen(full_name(cust))
    if not hits or len(hits) > MAX_HITS:
        return False
    if require and require not in {h["uid"] for h in hits}:
        return False
    calls = {h["uid"]: sop_call(cust, h, target) for h in hits}
    n = len(cases) + 1
    cases.append(
        {
            "id": f"c{n:02d}",
            "group": group,
            "title": title,
            "input": {
                "fp_id": f"fp_id_test_{random.getrandbits(40):010x}",
                "onboarding_id": f"ob_test_{random.getrandbits(32):08x}",
                "customer": cust,
            },
            "expected": {"hits": sorted(calls), "calls": calls},
        }
    )
    return True


def pick(pool, k, build) -> None:
    random.shuffle(pool)
    made = 0
    for e in pool:
        if made == k:
            break
        if e.uid in used or any(w in e.name.upper() for w in SKIP):
            continue
        if build(e):
            used.add(e.uid)
            made += 1
    assert made == k, f"only built {made} of {k}"


def _short(e) -> bool:
    return 2 <= len(normalize(display(e.name))) <= 4


exact = [e for e in E.values() if len(e.dobs) == 1 and e.dobs[0].exact and e.gender and e.nationality and _short(e)]
yearly = [e for e in E.values() if e.dobs and all(not d.exact for d in e.dobs) and _short(e)]
multi = [
    e for e in E.values() if len(e.dobs) >= 2 and all(d.exact for d in e.dobs) and len({d.year_from for d in e.dobs}) >= 2 and _short(e)
]
aliased = [e for e in exact if any(len(normalize(a)) >= 2 for a in e.aliases)]


SPELLINGS = [
    ("mohammad", "muhammad"),
    ("muhammad", "mohamed"),
    ("mohammed", "muhammad"),
    ("yuri", "yury"),
    ("aleksandr", "alexander"),
    ("aleksey", "alexei"),
    ("alexey", "aleksei"),
    ("sergey", "sergei"),
    ("dmitriy", "dmitry"),
    ("ks", "x"),
    ("iy", "y"),
    ("ou", "u"),
    ("ph", "f"),
    ("kh", "h"),
    ("ee", "i"),
    ("oo", "u"),
    ("yev", "yeyev"),
    ("v", "w"),
]


def respell(name: str) -> str:
    """A transliteration variant: the first spelling rule that changes the name, applied once."""
    low = name.lower()
    for a, b in SPELLINGS:
        if a in low:
            i = low.index(a)
            out = name[:i] + (b.title() if name[i].isupper() else b) + name[i + len(a) :]
            return out
    return name


def true_match(e, via_alias=False, variant=False):
    name = random.choice([a for a in e.aliases if len(normalize(a)) >= 2]) if via_alias else e.name
    f, m, last = first_last(name)
    if variant:
        f, m, last = respell(f), "", respell(last)
        if f"{f} {last}".lower() == f"{first_last(name)[0]} {first_last(name)[2]}".lower():
            return False
    c = customer(
        f, m, last, iso(listed_date(e)), e.nationality[0], doc={"type": "passport", "dob": iso(listed_date(e)), "gender": e.gender}
    )
    how = "an alias on the list" if via_alias else ("another spelling of the listed name" if variant else "the listed name")
    return add("true_match", f"Customer uses {how}, listed DOB and nationality", c, e.uid, require=e.uid)


pick(list(exact), 2, true_match)
pick(list(exact), 4, lambda e: true_match(e, variant=True))
pick(list(aliased), 2, lambda e: true_match(e, via_alias=True))


def dob_clear(e, doc_only=False):
    f, m, last = first_last(e.name)
    d = shifted(listed_date(e), random.choice([-1, 1]) * random.randint(4, 22))
    c = customer(
        f,
        "" if random.random() < 0.5 else m,
        last,
        iso(d),
        random.choice(["US", "US", "MX", "GB", "IN"]),
        doc={"type": "drivers_license", "dob": iso(d), "gender": e.gender},
        drop_vault_dob=doc_only,
    )
    where = "the ID document only (no DOB in the vault)" if doc_only else "the vault"
    return add("dob_clear", f"Same name, born {abs(d.year - listed_date(e).year)} years apart; DOB from {where}", c, None, require=e.uid)


pick(list(exact), 8, dob_clear)
pick(list(exact), 4, lambda e: dob_clear(e, doc_only=True))


def gender_clear(e):
    if not e.gender:
        return False
    f, m, last = first_last(e.name)
    d = e.dobs[0]
    born = dt.date(random.randint(d.year_from, d.year_to), random.randint(1, 12), random.randint(1, 28))
    other = "Female" if e.gender.lower().startswith("m") else "Male"
    c = customer(f, m, last, iso(born), "US", doc={"type": "passport", "dob": iso(born), "gender": other})
    if sop_call(c, {"uid": e.uid}, None)["kind"] != "gender":
        return False
    return add("gender", f"Same name, born inside the listed year ({d.text}), {other.lower()} on the ID document", c, None, require=e.uid)


pick([e for e in yearly if e.gender], 4, gender_clear)


def need_info_year(e):
    f, m, last = first_last(e.name)
    d = e.dobs[0]
    born = dt.date(random.randint(d.year_from, d.year_to), random.randint(1, 12), random.randint(1, 28))
    c = customer(f, m, last, iso(born), random.choice(["US", "GB", "CA"]), doc={"type": "drivers_license", "dob": iso(born)})
    return add("need_info", f"Same name, born inside the listed range ({d.text}), no gender on file", c, None, require=e.uid)


def need_info_no_dob(e):
    f, m, last = first_last(e.name)
    c = customer(f, m, last, None, "US", doc={})
    return add("need_info", "Same name, no DOB anywhere on file (onboarding did not collect it)", c, None, require=e.uid)


pick([e for e in yearly if not e.gender], 6, need_info_year)
pick(list(exact), 2, need_info_no_dob)


def multi_dob(e, hit_alt):
    f, m, last = first_last(e.name)
    if hit_alt:
        d = e.dobs[1]
        born = dt.date(d.year_from, d.month, d.day)
        title = f"Listed person has {len(e.dobs)} DOBs; customer's DOB is the second one ({d.text})"
        target = e.uid
    else:
        born = shifted(dt.date(e.dobs[0].year_from, e.dobs[0].month, e.dobs[0].day), 9)
        if any(d.compare(iso(born)) != "mismatch" for d in e.dobs):
            return False
        title = f"Listed person has {len(e.dobs)} DOBs; the customer's matches none of them"
        target = None
    c = customer(f, m, last, iso(born), "US", doc={"type": "passport", "dob": iso(born)})
    return add("multi_dob", title, c, target, require=e.uid)


pick(list(multi), 2, lambda e: multi_dob(e, True))
pick(list(multi), 2, lambda e: multi_dob(e, False))


def many_hits(e):
    f, m, last = first_last(e.name)
    hits = screen(f"{f} {last}")
    if not 3 <= len(hits) <= MAX_HITS:
        return False
    d = dt.date(random.randint(1965, 1998), random.randint(1, 12), random.randint(1, 28))
    c = customer(f, "", last, iso(d), "US", doc={"type": "drivers_license", "dob": iso(d)})
    return add("many_hits", f"Common name: {len(hits)} listed people hit at once", c, None)


pick([e for e in exact + yearly if len(normalize(display(e.name))) == 2], 4, many_hits)

assert len(cases) == 40, len(cases)
header = (
    "# 40 historical sanctions-screening reviews. Generated by scripts/make_cases.py from the OFAC SDN snapshot in\n"
    "# data/ofac/; edit that script, not this file. Customers are constructed; list entries are real (public OFAC data).\n"
)
out = ROOT / "evals" / "cases.yml"
out.write_text(header + yaml.safe_dump({"cases": cases}, sort_keys=False, allow_unicode=True, width=120))
by = {}
for c in cases:
    by.setdefault(c["group"], [0, 0])
    by[c["group"]][0] += 1
    by[c["group"]][1] += len(c["expected"]["hits"])
print("cases", len(cases), "hits", sum(len(c["expected"]["hits"]) for c in cases), by)
