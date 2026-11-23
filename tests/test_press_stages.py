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


def _found(store, *names):
    for n in names:
        store.add_article(f"https://x.example/{n}", "o", "it")


def test_failed_fetches_count_toward_the_limit_and_retry_oldest_attempt_last():
    """A backlog of failing articles cannot keep a run fetching, nor starve the articles never tried."""
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a", "b", "c")
    routes = {ROBOTS: Resp(""), **{f"https://x.example/{n}": Resp(status=503) for n in "abc"}}
    pc = client(routes)
    cfg = {"max_articles_per_run": 2, "culture_drop": 0.5}
    run_filter(store, Pages(pc), Judge(), cfg)
    asked = [u for u, _ in pc.session.calls if not u.endswith("robots.txt")]
    assert asked == ["https://x.example/a", "https://x.example/b"]
    pc = client(routes)
    run_filter(store, Pages(pc), Judge(), cfg)
    asked = [u for u, _ in pc.session.calls if not u.endswith("robots.txt")]
    assert asked == ["https://x.example/c", "https://x.example/a"]


def test_judge_failures_count_toward_the_limit():
    from typesafe_sdk import TypeSafeError

    class Failing:
        def __init__(self):
            self.n = 0

        def culture(self, url, text):
            self.n += 1
            raise TypeSafeError("down")
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a", "b", "c")
    page = "<html><body><p>Testo</p></body></html>"
    j = Failing()
    run_filter(store, Pages(client({ROBOTS: Resp(""), **{f"https://x.example/{n}": Resp(page) for n in "abc"}})),
               j, {"max_articles_per_run": 2, "culture_drop": 0.5})
    assert j.n == 2


def test_a_refused_outlets_articles_use_none_of_the_budget():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a", "b")
    store.add_article("https://y.example/c", "p", "it")
    pc = client({ROBOTS: Resp(status=429), "https://y.example/robots.txt": Resp(""),
                 "https://y.example/c": Resp("<p>Testo</p>")})
    assert run_filter(store, Pages(pc), Judge(), {"max_articles_per_run": 2, "culture_drop": 0.5}) == {"keep": 1}


def test_the_pages_language_is_stored():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a")
    html = '<html lang="en"><head><title>T</title></head><body><p>Text</p></body></html>'
    run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(html)})), Judge(),
               {"max_articles_per_run": 2, "culture_drop": 0.5})
    assert store.article("https://x.example/a").lang == "en"


def test_an_old_press_store_gains_the_attempt_column():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE press_articles (url TEXT PRIMARY KEY, outlet TEXT, lang TEXT, title TEXT, "
               "published TEXT, found_at REAL, stage TEXT DEFAULT 'found', outcome TEXT, data_json TEXT)")
    db.execute("INSERT INTO press_articles (url, outlet, lang, found_at) VALUES ('https://x.example/a', 'o', 'it', 1)")
    store = PressStore(db)
    assert [a.url for a in store.articles("found")] == ["https://x.example/a"]
    assert "attempted_at" in {r[1] for r in db.execute("PRAGMA table_info(press_articles)")}
