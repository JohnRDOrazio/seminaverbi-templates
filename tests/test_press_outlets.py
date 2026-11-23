import sqlite3
from pathlib import Path

from pipeline.press.fetch import Refused
from pipeline.press.outlets import canonical, discover, entry_urls
from pipeline.press.store import PressStore

FIX = Path(__file__).parent / "fixtures" / "press"


def fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_rss_atom_and_sitemaps_parse():
    assert entry_urls(fx("feed.rss")) == [
        ("https://x.example/cultura/one/?utm_source=f", "2026-02-03"),
        ("https://x.example/politica/two", "2025-12-01"),
        ("https://x.example/cultura/three", None)]
    assert entry_urls(fx("feed.atom")) == [
        ("https://x.example/cultura/a", "2026-03-05"),
        ("https://x.example/b", "2025-06-01")]
    assert entry_urls(fx("sitemap-index.xml")) == [
        ("https://x.example/sitemap-it.xml", "2026-02-01"),
        ("https://x.example/sitemap-en.xml", None)]
    assert entry_urls(fx("urlset.xml")) == [
        ("https://x.example/cultura/p1", "2026-02-10"),
        ("https://x.example/cultura/p2", None)]
    assert entry_urls(fx("news-sitemap.xml")) == [("https://x.example/news/n1", "2026-04-01")]


class Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


class FakeClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, outlet, url, conditional=False):
        self.calls.append((outlet, url, conditional))
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r

    def remember(self, url, response):
        pass


def store():
    return PressStore(sqlite3.connect(":memory:"))


def outlet(id="o", entries=None, **kw):
    return {"id": id, "name": id, "langs": ["it"], "entries": entries or [f"https://{id}.example/feed"],
            "culture_paths": [], "since": None, **kw}


def urlset(host, n):
    body = "".join(f"<url><loc>https://{host}/a{i}</loc></url>" for i in range(n))
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def test_only_culture_paths_and_since():
    s, c = store(), FakeClient({"https://o.example/feed": Resp(fx("feed.rss"))})
    n = discover(s, c, [outlet(culture_paths=["/cultura/"], since="2026-01-01")], 10)
    assert n == {"o": 2}  # one: dated, in; two: wrong path and old; three: path ok, undated is kept
    assert sorted(a.url for a in s.articles("found")) == [
        "https://x.example/cultura/one", "https://x.example/cultura/three"]
    s2 = store()
    assert discover(s2, c, [outlet(since="2026-03-01")], 10) == {"o": 1}  # dated ones are older; the undated one stays


def test_round_robin_respects_the_limit():
    s = store()
    c = FakeClient({"https://a.example/feed": Resp(urlset("a.example", 5)),
                    "https://b.example/feed": Resp(urlset("b.example", 5))})
    n = discover(s, c, [outlet("a"), outlet("b")], 4)
    assert n == {"a": 2, "b": 2}


def test_discover_canonicalises_urls():
    assert canonical("https://x.va/a/?utm_source=f&id=3#top") == "https://x.va/a?id=3"
    assert canonical("https://x.va/a?fbclid=1&gclid=2") == "https://x.va/a"


def test_sitemap_index_followed_for_outlet_langs():
    s = store()
    c = FakeClient({"https://o.example/feed": Resp(fx("sitemap-index.xml")),
                    "https://x.example/sitemap-it.xml": Resp(fx("urlset.xml"))})
    n = discover(s, c, [outlet(langs=["it", "de"])], 10)
    assert n == {"o": 2}
    assert all(call[2] for call in c.calls)  # conditional
    assert not any("sitemap-en" in call[1] for call in c.calls)
    assert {a.lang for a in s.articles("found")} == {"it"}


def test_discover_skips_a_broken_entry(capsys):
    s = store()
    c = FakeClient({"https://a.example/feed": Resp("<html>oops"),
                    "https://b.example/feed": Resp(urlset("b.example", 3))})
    n = discover(s, c, [outlet("a"), outlet("b")], 10)
    assert n == {"a": 0, "b": 3}
    assert "press: a https://a.example/feed: not a feed or sitemap" in capsys.readouterr().err


def test_errors_and_non_2xx_are_skipped(capsys):
    s = store()
    c = FakeClient({"https://a.example/feed": TimeoutError("slow"),
                    "https://b.example/feed": Resp("", 500),
                    "https://c.example/feed": None})
    n = discover(s, c, [outlet("a"), outlet("b"), outlet("c")], 10)
    assert n == {"a": 0, "b": 0, "c": 0}
    assert "press: a https://a.example/feed: slow" in capsys.readouterr().err


def test_refused_outlet_is_reported_not_retried():
    s = store()
    c = FakeClient({"https://a.example/f1": Refused("a", "403"), "https://a.example/f2": Resp(urlset("a.example", 2)),
                    "https://b.example/feed": Resp(urlset("b.example", 2))})
    n = discover(s, c, [outlet("a", entries=["https://a.example/f1", "https://a.example/f2"]), outlet("b")], 10)
    assert n == {"a": 0, "b": 2}
    assert [x[1] for x in c.calls if x[0] == "a"] == ["https://a.example/f1"]


def test_doctype_and_entity_bodies_are_skipped_unparsed(monkeypatch, capsys):
    import xml.etree.ElementTree as ET

    def boom(*a, **k):
        raise AssertionError("parsed")
    monkeypatch.setattr(ET, "fromstring", boom)
    laughs = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss>&b;</rss>'
    for body in (laughs, laughs.replace("DOCTYPE", "doctype"), "<!eNTITY x 'y'><rss/>"):
        s = store()
        c = FakeClient({"https://o.example/feed": Resp(body)})
        assert discover(s, c, [outlet()], 10) == {"o": 0}
    assert "press: o https://o.example/feed: not a feed or sitemap" in capsys.readouterr().err


def test_oversize_body_is_skipped(capsys):
    body = urlset("o.example", 1) + " " * (10 * 1024 * 1024)
    s, c = store(), FakeClient({"https://o.example/feed": Resp(body)})
    assert discover(s, c, [outlet()], 10) == {"o": 0}
    assert "not a feed or sitemap" in capsys.readouterr().err


INDEX = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
         '<sitemap><loc>https://o.example/sitemap-1.xml</loc></sitemap>'
         '<sitemap><loc>https://o.example/sitemap-2.xml</loc></sitemap></sitemapindex>')


def test_single_language_outlet_follows_unmarked_children():
    s = store()
    c = FakeClient({"https://o.example/feed": Resp(INDEX),
                    "https://o.example/sitemap-1.xml": Resp(urlset("o.example", 1)),
                    "https://o.example/sitemap-2.xml": Resp(urlset("o.example", 2).replace("/a0", "/b0"))})
    assert discover(s, c, [outlet()], 10) == {"o": 3}
    assert {a.lang for a in s.articles("found")} == {"it"}


def test_multi_language_outlet_still_filters_children_by_token():
    s = store()
    c = FakeClient({"https://o.example/feed": Resp(INDEX)})
    assert discover(s, c, [outlet(langs=["it", "en"])], 10) == {"o": 0}
    assert [x[1] for x in c.calls] == ["https://o.example/feed"]


def polite(routes, s):
    from pipeline.press.fetch import PoliteClient
    from tests.test_press_fetch import UA, Clock, FakeSession
    clock = Clock()
    session = FakeSession({"https://o.example/robots.txt": FResp(""), **routes})
    return PoliteClient(session, UA, 0, store=s, clock=clock, sleep=clock.sleep), session


class FResp(Resp):
    def __init__(self, text="", status=200, etag=None):
        super().__init__(text, status)
        self.headers = {"ETag": etag} if etag else {}


def test_validators_are_kept_only_once_a_feed_is_read_to_the_end():
    """A stream stopped at the run's limit keeps no ETag: the next run's conditional GET must not answer 304 and
    hide the entries not yet read."""
    feed = "https://o.example/feed"
    s = store()
    c, session = polite({feed: FResp(urlset("o.example", 5), etag='"v1"')}, s)
    assert discover(s, c, [outlet()], 2) == {"o": 2}
    assert s.feed_cache(feed) == (None, None)
    assert discover(s, c, [outlet()], 10) == {"o": 3}
    assert s.feed_cache(feed) == ('"v1"', None)


def test_an_index_keeps_no_validators_while_a_child_fails():
    s = store()
    c, _ = polite({"https://o.example/feed": FResp(INDEX, etag='"i"'),
                   "https://o.example/sitemap-1.xml": TimeoutError("slow"),
                   "https://o.example/sitemap-2.xml": FResp(urlset("o.example", 1), etag='"c2"')}, s)
    assert discover(s, c, [outlet()], 10) == {"o": 1}
    assert s.feed_cache("https://o.example/feed") == (None, None)  # the failed child is read again next run
    assert s.feed_cache("https://o.example/sitemap-2.xml") == ('"c2"', None)


def test_an_index_is_fetched_unconditionally_and_keeps_no_validators():
    """A child may hold new articles while its index is unchanged: a 304 on the index must never hide them."""
    s = store()
    c, session = polite({"https://o.example/feed": FResp(INDEX, etag='"i"'),
                         "https://o.example/sitemap-1.xml": FResp(urlset("o.example", 1), etag='"c1"'),
                         "https://o.example/sitemap-2.xml": FResp(urlset("o.example", 1), etag='"c2"')}, s)
    discover(s, c, [outlet()], 10)
    assert s.feed_cache("https://o.example/feed") == (None, None)
    assert s.feed_cache("https://o.example/sitemap-1.xml") == ('"c1"', None)
    session.calls.clear()
    discover(s, c, [outlet()], 10)
    sent = {u: kw["headers"] for u, kw in session.calls}
    assert "If-None-Match" not in sent["https://o.example/feed"]
    assert sent["https://o.example/sitemap-1.xml"]["If-None-Match"] == '"c1"'


ATOM_REL = ('<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><link rel="alternate" href="/cultura/a"/></entry>'
            '<entry xml:base="https://y.example/base/"><link href="b"/></entry>'
            '<entry><link href="https://z.example/c"/></entry></feed>')


def test_relative_links_resolve_against_the_response_and_xml_base():
    assert entry_urls(ATOM_REL, "https://x.example/feed") == [
        ("https://x.example/cultura/a", None), ("https://y.example/base/b", None), ("https://z.example/c", None)]
    rss = '<rss><channel><item><link>/cultura/r</link></item></channel></rss>'
    assert entry_urls(rss, "https://x.example/rss") == [("https://x.example/cultura/r", None)]


def test_discover_stores_relative_links_as_absolute_urls():
    s = store()
    r = Resp(ATOM_REL)
    r.url = "https://moved.example/feed"  # the response's effective URL
    c = FakeClient({"https://o.example/feed": r})
    discover(s, c, [outlet()], 10)
    assert {a.url for a in s.articles("found")} == {
        "https://moved.example/cultura/a", "https://y.example/base/b", "https://z.example/c"}
