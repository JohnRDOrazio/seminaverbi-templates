import sqlite3

from pipeline.press.fetch import PoliteClient
from pipeline.press.stages import Pages, run_filter
from pipeline.press.store import PressStore
from tests.test_press_fetch import UA, Clock, FakeSession, Resp
from tests.test_press_refusals import day  # noqa: F401 (fixture)

ROBOTS = "https://x.example/robots.txt"


def client(routes):
    c = Clock()
    return PoliteClient(FakeSession(routes), UA, 10, clock=c, sleep=c.sleep)


class Judge:
    def culture(self, url, text):
        return 0.9

    def angle(self, url, text):
        return 0.9


def test_robots_403_leaves_the_article_for_the_next_run():
    """A robots.txt that answers 401/403 one day stops the outlet for the run only: its articles stay `found`."""
    store = PressStore(sqlite3.connect(":memory:"))
    store.add_article("https://x.example/a", "o", "it")
    pages = Pages(client({ROBOTS: Resp(status=403)}))
    assert pages.get(store.article("https://x.example/a")) == (None, None)
    assert run_filter(store, pages, Judge(), {"max_articles_per_day": 10, "culture_drop": 0.5, "angle_drop": 0.4}) == {}
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
        client = client({})

        def holds(self, art):
            return False

        def get(self, art):
            return Page("T", None, "it", art.url, ["p"], []), None
    seen = []

    def link(page, outlet, store, judge, wikidata, sparql, pcfg, url, **kw):
        seen.append((url, kw.get("routes")))
        if url.endswith("/a"):
            raise SyncError("sparql: HTTP 429 after 5 retries")
        return []
    monkeypatch.setattr(stages, "link_article", link)
    counts = stages.run_link(store, FixedPages(), None, None, None, {"max_articles_per_day": 9})
    assert counts["errors"] == 1 and counts["articles"] == 1
    assert [a.url for a in store.articles("filtered")] == ["https://x.example/a"]
    assert seen[0][1] is seen[1][1] and isinstance(seen[0][1], dict)  # one class_route memo for the run
    assert "HTTP 429" in capsys.readouterr().err


def _found(store, *names):
    for n in names:
        store.add_article(f"https://x.example/{n}", "o", "it")


def test_failed_fetches_count_toward_the_limit_and_retry_oldest_attempt_last(day):  # noqa: F811
    """A backlog of failing articles cannot keep a run fetching, nor starve the articles never tried."""
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a", "b", "c")
    routes = {ROBOTS: Resp(""), **{f"https://x.example/{n}": Resp(status=503) for n in "abc"}}
    pc = client(routes)
    cfg = {"max_articles_per_day": 2, "culture_drop": 0.5, "angle_drop": 0.4}
    run_filter(store, Pages(pc), Judge(), cfg)
    asked = [u for u, _ in pc.session.calls if not u.endswith("robots.txt")]
    assert asked == ["https://x.example/a", "https://x.example/b"]
    day.set(2)
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
               j, {"max_articles_per_day": 2, "culture_drop": 0.5, "angle_drop": 0.4})
    assert j.n == 2


def test_a_refused_outlets_articles_use_none_of_the_budget():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a", "b")
    store.add_article("https://y.example/c", "p", "it")
    pc = client({ROBOTS: Resp(status=429), "https://y.example/robots.txt": Resp(""),
                 "https://y.example/c": Resp("<p>Testo</p>")})
    assert run_filter(store, Pages(pc), Judge(), {"max_articles_per_day": 2, "culture_drop": 0.5, "angle_drop": 0.4}) == {"keep": 1}


def test_the_pages_language_is_stored():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a")
    html = '<html lang="en"><head><title>T</title></head><body><p>Text</p></body></html>'
    run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(html)})), Judge(),
               {"max_articles_per_day": 2, "culture_drop": 0.5, "angle_drop": 0.4})
    assert store.article("https://x.example/a").lang == "en"


def test_an_old_press_store_gains_the_attempt_column():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE press_articles (url TEXT PRIMARY KEY, outlet TEXT, lang TEXT, title TEXT, "
               "published TEXT, found_at REAL, stage TEXT DEFAULT 'found', outcome TEXT, data_json TEXT)")
    db.execute("INSERT INTO press_articles (url, outlet, lang, found_at) VALUES ('https://x.example/a', 'o', 'it', 1)")
    store = PressStore(db)
    assert [a.url for a in store.articles("found")] == ["https://x.example/a"]
    assert "attempted_at" in {r[1] for r in db.execute("PRAGMA table_info(press_articles)")}


SECTION = ('<html><head><meta property="og:type" content="section">'
           '<script type="application/ld+json">{"@type": "BreadcrumbList"}</script></head>'
           '<body><p>Elenco</p></body></html>')


def test_a_section_page_is_skipped_for_good_at_filter(capsys):
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "musica")
    judge = Judge()
    counts = run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/musica": Resp(SECTION)})), judge,
                        {"max_articles_per_day": 2, "culture_drop": 0.5, "angle_drop": 0.4})
    assert counts == {"skip": 1}
    art = store.article("https://x.example/musica")
    assert (art.stage, art.outcome, art.data) == ("filtered", "skip", {"reason": "not an article"})


def test_a_section_page_already_kept_is_skipped_at_link():
    from pipeline.press.stages import run_link
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "musica")
    store.set_article("https://x.example/musica", "filtered", "keep", {"culture": 0.8})
    counts = run_link(store, Pages(client({ROBOTS: Resp(""), "https://x.example/musica": Resp(SECTION)})), None,
                      None, None, {"max_articles_per_day": 9})
    assert counts == {"articles": 0, "mentions": 0}
    art = store.article("https://x.example/musica")
    assert (art.stage, art.outcome, art.data) == ("linked", "skip", {"culture": 0.8, "reason": "not an article"})


class Angles(Judge):
    """angle scores by a word of the text (0.1 when none is there); records what was asked."""

    def __init__(self, **scores):
        self.scores, self.texts = scores, []

    def angle(self, url, text):
        self.texts.append(text)
        return next((p for word, p in self.scores.items() if word in text), 0.1)


ANGLE_CFG = {"max_articles_per_day": 5, "culture_drop": 0.5, "angle_drop": 0.4}


def _long(marker):
    filler = " ".join(f"parola{i}" for i in range(200))
    return (f"<html><head><title>Titolo</title></head><body><p>Apertura {filler}</p><p><em>Ordet</em> {filler}</p>"
            f"<p>{filler}</p><p><em>Ordet</em> {marker} {filler}</p></body></html>")


def test_an_article_without_the_angle_is_dropped_and_reported_apart():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a")
    judge = Angles()
    counts = run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(_long("x"))})), judge,
                        ANGLE_CFG)
    assert counts == {"drop (angle)": 1}
    art = store.article("https://x.example/a")
    assert (art.stage, art.outcome) == ("filtered", "drop")
    assert art.data == {"culture": 0.9, "angle": 0.1, "angle_opening": 0.1, "reason": "angle"}
    assert len(judge.texts) == 3  # the opening, then the two mentions' windows
    assert all(len(t.split()) <= 150 and t.startswith("Titolo") for t in judge.texts)


def test_an_angle_seen_only_further_on_keeps_the_article_with_its_best_score():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a")
    run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(_long("misericordia"))})),
               Angles(misericordia=0.8), ANGLE_CFG)
    art = store.article("https://x.example/a")
    assert art.outcome == "keep" and art.data == {"culture": 0.9, "angle": 0.8, "angle_opening": 0.1}


def test_an_angle_seen_in_the_opening_asks_no_further():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a")
    judge = Angles(Apertura=0.4)
    run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(_long("x"))})), judge, ANGLE_CFG)
    assert len(judge.texts) == 1 and store.article("https://x.example/a").outcome == "keep"


def test_the_angle_is_not_asked_below_culture_drop():
    store = PressStore(sqlite3.connect(":memory:"))
    _found(store, "a")
    judge = Angles()
    assert run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(_long("x"))})), judge,
                      {**ANGLE_CFG, "culture_drop": 0.95}) == {"drop": 1}
    assert judge.texts == [] and store.article("https://x.example/a").data == {"culture": 0.9}


CAP_CFG = {"max_articles_per_day": 5, "culture_drop": 0.5, "angle_drop": 0.4,
           "outlets": [{"id": "blog", "max_per_day": 2}, {"id": "o"}]}


def _cap_store():
    store = PressStore(sqlite3.connect(":memory:"))
    for i in range(6):
        store.add_article(f"https://x.example/b{i}", "blog", "it")
    for i in range(6):
        store.add_article(f"https://x.example/n{i}", "o", "it")
    return store


def _run(store, status=200):
    routes = {ROBOTS: Resp(""), **{a.url: Resp("<p>Testo</p>", status=status) for a in store.articles("found")}}
    pc = client(routes)
    run_filter(store, Pages(pc), Judge(), CAP_CFG)
    for a in store.articles("filtered"):
        store.set_article(a.url, "linked", a.outcome, a.data)
    return [u.rsplit("/", 1)[1] for u, _ in pc.session.calls if not u.endswith("robots.txt")]


def test_an_outlets_cap_leaves_the_rest_of_the_run_to_the_others_and_takes_its_turn_each_day(day):  # noqa: F811
    store = _cap_store()
    assert _run(store) == ["b0", "b1", "n0", "n1", "n2"]
    day.set(2)
    assert _run(store) == ["b2", "b3", "n3", "n4", "n5"]
    day.set(3)
    assert _run(store) == ["b4", "b5"]


def test_failures_count_toward_an_outlets_cap_and_do_not_starve_its_other_articles(day):  # noqa: F811
    store = _cap_store()
    assert _run(store, 503) == ["b0", "b1", "n0", "n1", "n2"]
    day.set(2)
    assert _run(store, 503) == ["b2", "b3", "n3", "n4", "n5"]


def test_a_backlog_of_other_outlets_does_not_starve_a_capped_outlet():
    """Its articles found after a large backlog still get their share of the first run."""
    store = PressStore(sqlite3.connect(":memory:"))
    for i in range(20):
        store.add_article(f"https://x.example/n{i}", "o", "it")
    for i in range(6):
        store.add_article(f"https://x.example/b{i}", "blog", "it")
    assert _run(store) == ["b0", "b1", "n0", "n1", "n2"]
