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
    n = discover(s, c, [outlet()], 10)
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
