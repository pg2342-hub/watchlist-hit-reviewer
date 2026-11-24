from watchlist.names import display, normalize, score
from watchlist.screen import screen
from watchlist.sdn import load, parse_dob


def test_snapshot_loads_individuals():
    entries = load()
    assert len(entries) > 5000
    assert all(e.type == "individual" for e in entries.values())


def test_dob_forms():
    assert parse_dob("10 Dec 1948").exact
    d = parse_dob("circa 1960")
    assert (d.year_from, d.year_to) == (1959, 1961)
    r = parse_dob("1989 to 1994")
    assert (r.year_from, r.year_to) == (1989, 1994)
    assert parse_dob("10 Dec 1948").compare("1948-12-10") == "match"
    assert parse_dob("10 Dec 1948").compare("1948-12-11") == "mismatch"
    assert parse_dob("1960").compare("1960-05-01") == "possible"
    assert parse_dob("Mar 1965").compare("1965-04-01") == "mismatch"


def test_name_order_and_accents():
    assert score("Ayman al-Zawahiri", normalize(display("AL ZAWAHIRI, Dr. Ayman"))) == 1.0
    assert score("José Pérez", "Jose Perez") == 1.0
    assert score("John Smith", "Ayman Zawahiri") < 0.6


def test_screen_finds_listed_person():
    assert any(h["uid"] == "2676" for h in screen("Ayman al-Zawahiri"))
    assert screen("Zzyzx Qwerty") == []
