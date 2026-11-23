"""At most max_articles_per_day articles a UTC day (and an outlet's max_per_day of its own), whatever the runs."""
import sqlite3

import pytest

from pipeline.press import stages as press_stages
from pipeline.press import store as press_store
from pipeline.press.outlets import discover
from pipeline.press.store import PressStore
from tests.test_press_fetch import Resp
from tests.test_press_refusals import day  # noqa: F401 (fixture)
from tests.test_press_stages import ROBOTS, Judge, client

CFG = {"max_articles_per_day": 5, "culture_drop": 0.5, "angle_drop": 0.4,
       "outlets": [{"id": "o"}, {"id": "blog", "max_per_day": 2}]}


def add(store, name, outlet="o", keep=False):
    url = f"https://x.example/{name}"
    store.add_article(url, outlet, "it")
    if keep:
        store.set_article(url, "filtered", "keep", {})
    return url


class Run:
    """One run: its own client and pages; the article names requested."""

    def __init__(self, store, status=200, cfg=CFG):
        urls = [r[0] for r in store.db.execute("SELECT url FROM press_articles")]
        self.store, self.cfg = store, cfg
        self.client = client({ROBOTS: Resp(""), **{u: Resp("<p>Testo</p>", status=status) for u in urls}})
        self.pages = press_stages.Pages(self.client)

    def names(self):
        return [u.rsplit("/", 1)[1] for u, _ in self.client.session.calls if not u.endswith("robots.txt")]

    def filter(self):
        press_stages.run_filter(self.store, self.pages, Judge(), self.cfg)
        return self.names()

    def link(self):
        press_stages.run_link(self.store, self.pages, Judge(), None, None, self.cfg)
        return self.names()


@pytest.fixture
def store(day, monkeypatch):  # noqa: F811
    monkeypatch.setattr(press_stages, "link_article", lambda *a, **k: [])
    return PressStore(sqlite3.connect(":memory:"))


def test_two_runs_of_a_day_share_the_articles_they_read(store, day):  # noqa: F811
    for i in range(12):
        add(store, f"n{i}")
    run = Run(store)
    assert run.filter() == ["n0", "n1", "n2", "n3", "n4"]
    run.link()
    assert Run(store).filter() == []
    day.set(2)
    assert Run(store).filter() == ["n5", "n6", "n7", "n8", "n9"]


def test_a_second_run_reads_what_the_first_left(store):
    for i in range(3):
        add(store, f"n{i}")
    Run(store).filter()
    for i in range(3, 9):
        add(store, f"n{i}")
    assert Run(store).filter() == ["n3", "n4"]


def test_failures_count_and_reading_an_article_again_the_same_day_does_not(store):
    for i in range(7):
        add(store, f"n{i}")
    assert len(Run(store, 503).filter()) == 5
    again = Run(store, 503).filter()
    assert sorted(again) == ["n0", "n1", "n2", "n3", "n4"]  # no article beyond the day's five


def test_link_refetching_an_article_filtered_the_same_day_counts_it_once(store):
    for i in range(5):
        add(store, f"n{i}")
    assert len(Run(store).filter()) == 5
    run = Run(store)
    assert len(run.link()) == 5 and [a.stage for a in store.articles("linked")] == ["linked"] * 5
    add(store, "n5")
    assert Run(store).filter() == []


def test_articles_kept_on_earlier_days_count_when_link_fetches_them(store):
    for i in range(8):
        add(store, f"k{i}", keep=True)
    assert len(Run(store).link()) == 5
    assert Run(store).link() == []
    add(store, "n")
    assert Run(store).filter() == []


def test_filter_and_link_together_stay_within_the_day_and_link_has_its_share(store):
    for i in range(9):
        add(store, f"n{i}")
    for i in range(9):
        add(store, f"k{i}", keep=True)
    run = Run(store)
    assert run.filter() == ["n0", "n1", "n2"]  # at most half is left to articles kept on earlier days
    assert run.link()[3:] == ["k0", "k1"]
    assert store.read_today() == 5


def test_filter_leaves_no_more_than_link_needs(store):
    for i in range(9):
        add(store, f"n{i}")
    add(store, "k", keep=True)
    run = Run(store)
    assert run.filter() == ["n0", "n1", "n2", "n3"] and run.link()[4:] == ["k"]


def test_nothing_is_held_for_kept_articles_that_cannot_be_read(store):
    for i in range(6):
        add(store, f"n{i}")
    add(store, "k", "refuser", keep=True)
    store.refuse("refuser", "403")
    run = Run(store)
    run.client.refused = store.stopped()
    assert len(run.filter()) == 5 and len(run.link()) == 5


def test_link_takes_turns_so_a_failing_backlog_starves_none(store, day):  # noqa: F811
    for i in range(7):
        add(store, f"k{i}", keep=True)
    Run(store, 503).link()
    day.set(2)
    assert set(Run(store, 503).link()) >= {"k5", "k6"}


def test_an_outlets_cap_is_its_day_for_filter_and_link_together(store, day):  # noqa: F811
    for i in range(3):
        add(store, f"b{i}", "blog")
    add(store, "k", "blog", keep=True)
    run = Run(store)
    assert run.filter() == ["b0"] and run.link()[1:] == ["k"]
    assert Run(store).filter() == [] and Run(store).link() == []
    day.set(2)
    assert Run(store).filter() == ["b1", "b2"]


def test_a_run_past_midnight_does_not_fetch_again_what_it_holds(store, day):  # noqa: F811
    for i in range(5):
        add(store, f"n{i}")
    run = Run(store)
    run.filter()
    day.set(2)
    assert len(run.link()) == 5 and store.read_today() == 0


def test_two_processes_share_the_day(day, tmp_path, monkeypatch):  # noqa: F811
    a, b = (PressStore(sqlite3.connect(tmp_path / "q.sqlite")) for _ in range(2))
    for i in range(9):
        add(a, f"n{i}")
    assert [s.take(f"u{i}", "o", 5, None) for i, s in enumerate((a, b, a, b, a, b))] == [True] * 5 + [False]
    assert b.take("u0", "o", 5, None) is True  # read already: not counted again


def test_only_the_days_reads_are_kept(store, day):  # noqa: F811
    store.take("u", "o", 5, None)
    day.set(2)
    store.take("v", "o", 5, None)
    assert [tuple(r) for r in store.db.execute("SELECT url FROM press_reads")] == [("v",)]


# ---- discovery ----

class Feed:
    def __init__(self, n):
        self.n, self.calls = n, 0

    def get(self, outlet, url, conditional=False):
        self.calls += 1
        return Resp("<rss><channel>" + "".join(f"<item><link>https://{outlet}.example/{i}</link></item>"
                                               for i in range(self.n)) + "</channel></rss>")

    def remember(self, url, response):
        pass


OUTLETS = [{"id": "o", "langs": ["it"], "entries": ["https://o.example/f"]},
           {"id": "blog", "langs": ["it"], "entries": ["https://blog.example/f"], "max_per_day": 2}]


def test_runs_of_a_day_share_what_they_discover(store, day):  # noqa: F811
    assert discover(store, Feed(3), OUTLETS, 7) == {"o": 3, "blog": 2}
    assert discover(store, Feed(9), OUTLETS, 7) == {"o": 2, "blog": 0}
    feed = Feed(20)
    assert discover(store, feed, OUTLETS, 7) == {"o": 0, "blog": 0} and feed.calls == 0
    day.set(2)
    assert discover(store, Feed(20), OUTLETS, 7) == {"o": 5, "blog": 2}


def test_a_full_outlet_leaves_the_rest_of_the_day_to_the_others(store):
    assert discover(store, Feed(9), OUTLETS[1:], 4) == {"blog": 2}
    feed = Feed(9)
    assert discover(store, feed, OUTLETS, 4) == {"o": 2, "blog": 0} and feed.calls == 1


def test_yesterdays_discoveries_do_not_count(store, day):  # noqa: F811
    for i in range(4):
        add(store, f"n{i}")
    assert store.found_today() == 4 and store.found_today("o") == 4 and store.found_today("blog") == 0
    day.set(2)
    assert store.found_today() == 0
