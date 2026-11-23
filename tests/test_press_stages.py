import sqlite3

from pipeline.press.fetch import PoliteClient
from pipeline.press.stages import Pages, run_filter
from pipeline.press.store import PressStore
from tests.test_press_fetch import UA, Clock, FakeSession, Resp

ROBOTS = "https://x.example/robots.txt"


def client(routes):
    c = Clock()
    return PoliteClient(FakeSession(routes), UA, 10, clock=c, sleep=c.sleep)


class Judge:
    def culture(self, url, text):
        return 0.9


def test_robots_403_leaves_the_article_for_the_next_run():
    """A robots.txt that answers 401/403 one day stops the outlet for the run only: its articles stay `found`."""
    store = PressStore(sqlite3.connect(":memory:"))
    store.add_article("https://x.example/a", "o", "it")
    pages = Pages(client({ROBOTS: Resp(status=403)}))
    assert pages.get(store.article("https://x.example/a")) == (None, None)
    assert run_filter(store, pages, Judge(), {"max_articles_per_run": 10, "culture_drop": 0.5}) == {}
    assert [a.url for a in store.articles("found")] == ["https://x.example/a"]


def test_a_path_robots_disallows_is_still_skipped_for_good():
    store = PressStore(sqlite3.connect(":memory:"))
    store.add_article("https://x.example/private/a", "o", "it")
    pages = Pages(client({ROBOTS: Resp("User-agent: *\nDisallow: /private\n")}))
    assert pages.get(store.article("https://x.example/private/a")) == (None, "robots.txt")


def test_a_wikidata_failure_after_retries_skips_only_that_article(monkeypatch, capsys):
    """A WDQS 429/503 after the retries raises SyncError: that article stays `filtered`, the others are linked."""
    from pipeline.press import stages
    from pipeline.press.extract import Page
    from sync.wiki import SyncError
    store = PressStore(sqlite3.connect(":memory:"))
    for u in ("https://x.example/a", "https://x.example/b"):
        store.add_article(u, "o", "it")
        store.set_article(u, "filtered", "keep", {})

    class FixedPages:
        def get(self, art):
            return Page("T", None, "it", art.url, ["p"], []), None
    seen = []

    def link(page, outlet, store, judge, wikidata, sparql, pcfg, url, **kw):
        seen.append((url, kw.get("routes")))
        if url.endswith("/a"):
            raise SyncError("sparql: HTTP 429 after 5 retries")
        return []
    monkeypatch.setattr(stages, "link_article", link)
    counts = stages.run_link(store, FixedPages(), None, None, None, {})
    assert counts["errors"] == 1 and counts["articles"] == 1
    assert [a.url for a in store.articles("filtered")] == ["https://x.example/a"]
    assert seen[0][1] is seen[1][1] and isinstance(seen[0][1], dict)  # one class_route memo for the run
    assert "HTTP 429" in capsys.readouterr().err
