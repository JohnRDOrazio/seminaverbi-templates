"""An outlet that refuses us: not read again that day (UTC), removed after a week of refusals, put back by hand."""
import sqlite3
from datetime import datetime

import pytest

from pipeline import __main__ as cli
from pipeline.press import stages as press_stages
from pipeline.press import store as press_store
from pipeline.press.fetch import PoliteClient, Refused
from pipeline.press.outlets import discover
from pipeline.press.store import PressStore
from pipeline.queue import Queue
from tests.test_press_cli import A1, FEED, RSS, press  # noqa: F401 (fixture)
from tests.test_press_fetch import BLOB, ROBOTS, UA, Clock, FakeSession, Resp

A = "https://x.example/a"


class Day:
    """The store's UTC day, set by the test."""

    def __init__(self, monkeypatch, day="2026-12-01"):
        self.day = day
        monkeypatch.setattr(press_store, "utc_now", lambda: datetime.fromisoformat(f"{self.day}T04:30:00+00:00"))

    def set(self, n):
        self.day = f"2026-12-{n:02d}"


@pytest.fixture
def day(monkeypatch):
    return Day(monkeypatch)


def memory():
    return PressStore(sqlite3.connect(":memory:"))


def client(store, routes):
    c, s = Clock(), FakeSession(routes)
    return PoliteClient(s, UA, 10, store=store, clock=c, sleep=c.sleep), s


def visit(store, routes, *urls, outlet="o"):
    """One run: a new client reading the URLs; the requests made."""
    pc, s = client(store, routes)
    for url in urls or (A,):
        try:
            pc.get(outlet, url)
        except Exception:
            pass
    return [u for u, _ in s.calls]


OK = {ROBOTS: Resp(""), A: Resp("A")}
NO = {ROBOTS: Resp(""), A: Resp(status=403)}


# ---- the same day ----

REFUSALS = [({ROBOTS: Resp(""), A: Resp(status=n)}, str(n)) for n in (401, 403, 429)] + [
    ({ROBOTS: Resp(status=n)}, f"robots {n}") for n in (401, 403, 429)] + [
    ({ROBOTS: Resp(""), A: Resp(status=429, headers={"Retry-After": "5"})}, "429")]


@pytest.mark.parametrize("routes,reason", REFUSALS)
def test_a_refusal_stops_every_later_run_of_the_day_and_no_longer(day, tmp_path, routes, reason):
    db = tmp_path / "q.sqlite"
    visit(PressStore(sqlite3.connect(db)), routes)
    store = PressStore(sqlite3.connect(db))
    assert store.refused_today() == {"o": reason}
    pc, s = client(store, OK)
    assert pc.refused == {"o": reason}  # known before anything is asked
    with pytest.raises(Refused):
        pc.get("o", A)
    assert s.calls == []  # robots.txt included
    assert visit(store, OK, outlet="other") == [ROBOTS, A]
    day.set(2)
    assert visit(store, OK) == [ROBOTS, A]


def test_a_refusal_by_another_process_during_the_run_is_honoured(day, tmp_path):
    db = tmp_path / "q.sqlite"
    pc, s = client(PressStore(sqlite3.connect(db)), OK)
    visit(PressStore(sqlite3.connect(db)), NO)
    with pytest.raises(Refused):
        pc.get("o", A)
    assert s.calls == []


NOT_REFUSALS = [
    {ROBOTS: Resp(status=503)},
    {ROBOTS: TimeoutError("t")},
    {ROBOTS: Resp("User-agent: *\nDisallow: /a\n")},
    {ROBOTS: Resp(""), A: Resp(status=500)},
    {ROBOTS: Resp(""), A: TimeoutError("t")},
    {ROBOTS: Resp(""), A: Resp(status=404)},
    {ROBOTS: Resp(""), A: Resp(status=302, headers={"Location": f"{BLOB}/p"}), f"{BLOB}/robots.txt": Resp(status=403)},
]


@pytest.mark.parametrize("routes", NOT_REFUSALS)
def test_trouble_that_is_not_a_refusal_is_not_kept(day, routes):
    store = memory()
    visit(store, routes)
    assert store.refused_today() == {}
    assert visit(store, OK) == [ROBOTS, A]


def test_the_day_is_utc():
    import time
    before = time.strftime("%Y-%m-%d", time.gmtime())
    assert press_store.utc_today() in (before, time.strftime("%Y-%m-%d", time.gmtime()))


def test_the_days_articles_are_discovered_and_read_the_day_after(day):
    store = memory()
    outlet = {"id": "o", "langs": ["it"], "entries": [FEED]}
    robots = "https://news.example/robots.txt"
    pc, _ = client(store, {robots: Resp(""), FEED: Resp(status=403)})
    assert discover(store, pc, [outlet], 10) == {"o": 0}
    pc, s = client(store, {robots: Resp(""), FEED: Resp(RSS)})
    assert discover(store, pc, [outlet], 10) == {"o": 0} and s.calls == []
    day.set(2)
    pc, _ = client(store, {robots: Resp(""), FEED: Resp(RSS)})
    assert discover(store, pc, [outlet], 10) == {"o": 3}


def test_articles_of_a_stopped_outlet_stay_found_unattempted(day):
    store = memory()
    store.add_article(A, "o", "it")
    store.refuse("o", "403")
    pc, s = client(store, OK)
    pcfg = {"outlets": [{"id": "o"}], "max_articles_per_day": 5}
    assert press_stages.run_filter(store, press_stages.Pages(pc), None, pcfg) == {}
    assert s.calls == [] and store.article(A).stage == "found"
    assert store.db.execute("SELECT attempted_at FROM press_articles").fetchone()[0] is None


# ---- the week ----

def refuse_days(store, day, days):
    for n in days:
        day.set(n)
        visit(store, NO)


def test_seven_refused_days_remove_the_outlet_for_good(day):
    store = memory()
    refuse_days(store, day, range(1, 7))
    assert store.removed() == {}
    refuse_days(store, day, [7])
    assert store.removed()["o"] == {"on": "2026-12-07", "since": "2026-12-01", "reason": "403"}
    for n in (7, 8, 30):
        day.set(n)
        assert visit(store, OK) == []
    pc, _ = client(store, OK)
    assert pc.refused == {"o": "removed"}


def test_a_removal_keeps_its_first_date(day):
    store = memory()
    refuse_days(store, day, range(1, 8))
    day.set(9)
    store.refuse("o", "429")  # a run already reading it when it was removed
    assert store.removed()["o"] == {"on": "2026-12-07", "since": "2026-12-01", "reason": "403"}


def test_several_refusals_in_a_day_count_once(day):
    store = memory()
    for _ in range(9):
        store.refuse("o", "403")
    assert store.removed() == {}


def test_days_without_a_run_neither_count_nor_break(day):
    store = memory()
    refuse_days(store, day, [1, 2, 3, 10, 11, 12])
    assert store.removed() == {}
    refuse_days(store, day, [20])
    assert store.removed()["o"]["since"] == "2026-12-01"


@pytest.mark.parametrize("trouble", NOT_REFUSALS[:6])
def test_days_neither_refused_nor_successful_neither_count_nor_break(day, trouble):
    store = memory()
    refuse_days(store, day, range(1, 7))
    day.set(7)
    visit(store, trouble)
    assert store.removed() == {}
    refuse_days(store, day, [8])
    assert "o" in store.removed()


@pytest.mark.parametrize("status", [200, 304])
def test_a_successful_day_resets(day, status):
    store = memory()
    refuse_days(store, day, range(1, 7))
    day.set(7)
    visit(store, {ROBOTS: Resp(""), A: Resp("A", status=status)})
    refuse_days(store, day, range(8, 14))
    assert store.removed() == {}
    refuse_days(store, day, [14])
    assert store.removed()["o"]["since"] == "2026-12-08"


def test_a_day_with_a_success_and_a_refusal_is_not_a_refused_day_but_stops_it(day):
    store = memory()
    refuse_days(store, day, range(1, 7))
    day.set(7)
    b = "https://x.example/b"
    visit(store, {**OK, b: Resp(status=403)}, A, b)
    assert store.removed() == {} and store.refused_today() == {"o": "403"}
    assert store.refusals() == {"o": {"on": "2026-12-07", "since": None, "days": 0, "reason": "403"}}
    refuse_days(store, day, range(8, 14))
    assert store.removed() == {}


def test_robots_txt_alone_is_not_a_success(day):
    store = memory()
    refuse_days(store, day, range(1, 8))  # robots.txt answered 200 each day
    assert "o" in store.removed()


def test_a_404_is_not_a_success(day):
    store = memory()
    b = "https://x.example/b"
    for n in range(1, 8):
        day.set(n)
        visit(store, {ROBOTS: Resp(""), A: Resp(status=404), b: Resp(status=403)}, A, b)
    assert "o" in store.removed()


# ---- putting back ----

def test_reinstate_reads_the_outlet_again_and_counts_anew(day):
    store = memory()
    refuse_days(store, day, range(1, 8))
    day.set(9)
    assert store.reinstate("o") is True and store.reinstate("o") is False
    assert store.removed() == {} and visit(store, OK) == [ROBOTS, A]


def test_reinstate_on_the_day_it_refused_waits_for_the_day_after(day):
    store = memory()
    refuse_days(store, day, range(1, 8))
    store.reinstate("o")
    assert visit(store, OK) == []
    refuse_days(store, day, range(8, 14))
    assert store.removed() == {}  # the refusals before it was put back count no more
    refuse_days(store, day, [14])
    assert store.removed()["o"]["since"] == "2026-12-08"


# ---- stores made before ----

def test_an_old_store_gains_the_tables_and_keeps_its_rows(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("CREATE TABLE press_articles (url TEXT PRIMARY KEY, outlet TEXT, lang TEXT, title TEXT, published TEXT, "
               "found_at REAL, stage TEXT DEFAULT 'found', outcome TEXT, data_json TEXT)")
    db.execute("INSERT INTO press_articles (url, outlet, lang) VALUES ('u', 'o', 'it')")
    db.execute("CREATE TABLE press_feeds (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT)")
    db.execute("INSERT INTO press_feeds VALUES ('f', 'e', 'm')")
    db.commit()
    store = PressStore(db)
    assert store.removed() == {} and store.refused_today() == {} and store.refusals() == {}
    store.refuse("o", "403")
    again = PressStore(sqlite3.connect(tmp_path / "old.sqlite"))  # the migration is guarded
    assert again.refused_today() == {"o": "403"}
    assert again.article("u").stage == "found" and again.feed_cache("f") == ("e", "m")


# ---- the command line ----

def with_client(monkeypatch, routes):
    sessions = []

    def make(pcfg, store):
        pc, s = client(store, routes)
        sessions.append(s)
        return pc

    monkeypatch.setattr(cli, "_press_client", make)
    return sessions


CIV = "https://civ.example/feed"
CIV_ROUTES = {"https://news.example/robots.txt": Resp(""), FEED: Resp(RSS), "https://civ.example/robots.txt": Resp(""),
              CIV: Resp(status=403)}


def test_a_run_reports_refused_and_removed_outlets_with_their_dates(press, day, monkeypatch, capsys):  # noqa: F811
    db, _ = press
    sessions = with_client(monkeypatch, CIV_ROUTES)
    for n in (1, 2):
        day.set(n)
        assert cli.main(["press", "discover", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "press refused: civilta (403; refused every day it was read since 2026-12-01, 2 days)" in out
    assert "press removed: none" in out
    assert cli.main(["press", "discover", "--db", str(db)]) == 0  # again the same day
    assert CIV not in [u for u, _ in sessions[-1].calls]
    assert "civilta (403; refused" in capsys.readouterr().out
    for n in range(3, 9):
        day.set(n)
        assert cli.main(["press", "discover", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    line = "press removed: civilta (on 2026-12-07; refused since 2026-12-01: 403; `press reinstate civilta` puts it back)"
    assert out.count(line) == 2  # the day it was removed, and the run after
    assert out.rstrip().endswith("press refused: none\n" + line)
    assert not any("civ.example" in u for u, _ in sessions[-1].calls)


def test_press_outlets_shows_them_and_asks_nothing(press, day, monkeypatch, capsys):  # noqa: F811
    db, _ = press
    store = PressStore.of(Queue(db))
    refuse_days(store, day, range(1, 8))
    store.refuse("sir", "429")
    day.set(8)
    sessions = with_client(monkeypatch, {})
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert cli.main(["press", "outlets", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "press refused today (2026-12-08 UTC): none" in out
    assert "press refused last time read: sir (on 2026-12-07: 429; refused every day it was read since 2026-12-07, 1 day)" in out
    assert "press removed: o (on 2026-12-07" in out
    assert sessions == []


def test_press_reinstate(press, day, monkeypatch, capsys):  # noqa: F811
    db, _ = press
    store = PressStore.of(Queue(db))
    refuse_days(store, day, range(1, 8))
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert cli.main(["press", "reinstate", "o", "--db", str(db)]) == 0
    assert "press reinstated: o" in capsys.readouterr().out
    assert store.removed() == {}
    assert cli.main(["press", "reinstate", "o", "--db", str(db)]) == 1
    assert "o is not a removed outlet, nothing changed" in capsys.readouterr().err
    for args in (["press", "reinstate"], ["press", "discover", "sir"]):
        with pytest.raises(SystemExit):
            cli.main([*args, "--db", str(db)])


def dump(db):
    return list(sqlite3.connect(db).iterdump())


def test_discover_dry_run_honours_the_stored_state_and_writes_none(press, day, monkeypatch, capsys):  # noqa: F811
    db, _ = press
    store = PressStore.of(Queue(db))
    refuse_days(store, day, range(1, 8))
    store.refuse("sir", "429")
    before = dump(db)
    sessions = with_client(monkeypatch, CIV_ROUTES)
    assert cli.main(["press", "discover", "--dry-run", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    asked = [u for u, _ in sessions[0].calls]
    assert FEED not in asked and CIV in asked  # sir refused today; civilta not yet
    assert "sir (429" in out and "civilta (403)" in out and "press removed: o (" in out
    assert dump(db) == before
    assert store.refused_today() == {"sir": "429", "o": "403"}  # civilta's is not kept


def test_run_dry_run_on_a_copy_changes_only_the_copy(press, day, monkeypatch, tmp_path):  # noqa: F811
    db, _ = press
    PressStore.of(Queue(db))
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(db.read_bytes())
    before = dump(db)
    with_client(monkeypatch, CIV_ROUTES | {"https://news.example/robots.txt": Resp(status=503)})
    assert cli.main(["press", "run", "--dry-run", "--db", str(copy)]) == 0
    assert PressStore.of(Queue(copy)).refused_today() == {"civilta": "403"}
    assert dump(db) == before


def test_the_page_says_it():
    from tests.test_press_cli import ROOT
    text = (ROOT / "docs/press-bot-page.wiki").read_text()
    assert ("* If a site answers that it refuses the request (HTTP 401, 403 or 429), the bot stops reading that site for "
            "the day and does not try again by other means. A site that refuses it every day for a week is no longer "
            "read.\n") in text
    assert "numeric fingerprint (hashes only)" in text and "https://typesafe.ai/legal/mca" in text
    assert "used to train any model" not in text
