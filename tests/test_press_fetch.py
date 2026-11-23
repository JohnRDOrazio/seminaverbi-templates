import sqlite3

import pytest

from pipeline.press.fetch import PoliteClient, Refused, load_press_config
from pipeline.press.store import PressStore


class Resp:
    def __init__(self, text="", status=200, headers=None):
        self.text, self.status_code, self.headers = text, status, headers or {}


class FakeSession:
    """Answers by URL; records every request."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


ROBOTS = "https://x.example/robots.txt"
UA = "SeminaVerbi-press/1.0 (+https://example/bot)"


def client(routes, delay=10, store=None):
    c = Clock()
    s = FakeSession(routes)
    return PoliteClient(s, UA, delay, store=store, clock=c, sleep=c.sleep), s, c


def test_sends_our_agent_and_waits_between_requests():
    pc, s, c = client({ROBOTS: Resp("User-agent: *\nDisallow:\n"),
                       "https://x.example/a": Resp("A"), "https://x.example/b": Resp("B")})
    pc.get("o", "https://x.example/a")
    pc.get("o", "https://x.example/b")
    assert all(kw["headers"]["User-Agent"] == UA for _, kw in s.calls)
    assert len(s.calls) == 3 and c.sleeps and all(x >= 10 for x in c.sleeps)
    assert sum(c.sleeps) >= 20


def test_crawl_delay_wins_when_larger():
    pc, s, c = client({ROBOTS: Resp("User-agent: *\nCrawl-delay: 30\n"),
                       "https://x.example/a": Resp("A")})
    pc.get("o", "https://x.example/a")
    assert max(c.sleeps) == 30


def test_disallowed_path_is_not_fetched():
    pc, s, c = client({ROBOTS: Resp("User-agent: *\nDisallow: /private\n")})
    assert pc.get("o", "https://x.example/private/x") is None
    assert [u for u, _ in s.calls] == [ROBOTS]


def test_403_refuses_the_outlet_for_the_run():
    pc, s, c = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=403),
                       "https://x.example/b": Resp("B")})
    with pytest.raises(Refused) as e:
        pc.get("vaticannews", "https://x.example/a")
    assert (e.value.outlet, e.value.reason) == ("vaticannews", "403")
    n = len(s.calls)
    with pytest.raises(Refused):
        pc.get("vaticannews", "https://x.example/b")
    assert len(s.calls) == n and pc.refused == {"vaticannews": "403"}


@pytest.mark.parametrize("status", [401, 429])
def test_401_and_429_refuse(status):
    pc, _, _ = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=status)})
    with pytest.raises(Refused):
        pc.get("o", "https://x.example/a")
    assert pc.refused["o"] == str(status)


@pytest.mark.parametrize("robots", [Resp(status=503), TimeoutError("t")])
def test_unreachable_robots_skips_the_outlet(robots):
    pc, s, _ = client({ROBOTS: robots, "https://x.example/a": Resp("A")})
    with pytest.raises(Refused) as e:
        pc.get("o", "https://x.example/a")
    assert e.value.reason == "robots unreachable"
    assert [u for u, _ in s.calls] == [ROBOTS]
    assert pc.refused["o"] == "robots unreachable"


def test_robots_403_disallows_all_and_404_allows_all():
    pc, s, _ = client({ROBOTS: Resp(status=403)})
    assert pc.get("o", "https://x.example/a") is None
    pc, s, _ = client({ROBOTS: Resp(status=404), "https://x.example/a": Resp("A")})
    assert pc.get("o", "https://x.example/a").text == "A"


def test_conditional_get_uses_the_cache():
    store = PressStore(sqlite3.connect(":memory:"))
    url = "https://x.example/feed"
    store.set_feed_cache(url, '"abc"', "Mon, 01 Jan 2026 00:00:00 GMT")
    pc, s, _ = client({ROBOTS: Resp(""), url: Resp(status=304)}, store=store)
    assert pc.get("o", url, conditional=True) is None
    h = s.calls[-1][1]["headers"]
    assert h["If-None-Match"] == '"abc"' and h["If-Modified-Since"].startswith("Mon")


def test_conditional_get_stores_new_validators():
    store = PressStore(sqlite3.connect(":memory:"))
    url = "https://x.example/feed"
    pc, _, _ = client({ROBOTS: Resp(""), url: Resp("f", headers={"ETag": '"n"', "Last-Modified": "L"})},
                      store=store)
    r = pc.get("o", url, conditional=True)
    assert r.text == "f" and store.feed_cache(url) == (None, None)  # stored only once its entries are read
    pc.remember(url, r)
    assert store.feed_cache(url) == ('"n"', "L")


def test_config_agent_has_no_email():
    cfg = load_press_config()
    assert "@" not in cfg["user_agent"] and cfg["user_agent"].startswith("SeminaVerbi-press/1.0")
    assert cfg["delay_seconds"] == 10 and len(cfg["outlets"]) == 12


def test_robots_429_refuses_instead_of_allowing_all():
    pc, s, _ = client({ROBOTS: Resp(status=429), "https://x.example/a": Resp("A")})
    with pytest.raises(Refused) as e:
        pc.get("o", "https://x.example/a")
    assert e.value.reason == "robots 429" and pc.refused == {"o": "robots 429"}
    assert [u for u, _ in s.calls] == [ROBOTS]


def test_robots_403_is_recorded_without_raising():
    pc, s, _ = client({ROBOTS: Resp(status=403)})
    assert pc.get("o", "https://x.example/a") is None
    assert pc.get("o", "https://x.example/b") is None
    assert pc.refused == {"o": "robots 403"}
    assert [u for u, _ in s.calls] == [ROBOTS]


BLOB = "https://blob.example"


def test_a_cross_host_redirect_reads_the_new_hosts_robots_and_waits_for_it():
    pc, s, c = client({ROBOTS: Resp(""), "https://x.example/sitemap.xml": Resp(
                           status=301, headers={"Location": f"{BLOB}/s/sitemap.xml"}),
                       f"{BLOB}/robots.txt": Resp("User-agent: *\nCrawl-delay: 30\n"),
                       f"{BLOB}/s/sitemap.xml": Resp("S"), f"{BLOB}/s/two.xml": Resp("T")})
    assert pc.get("o", "https://x.example/sitemap.xml").text == "S"
    assert [u for u, _ in s.calls] == [ROBOTS, "https://x.example/sitemap.xml", f"{BLOB}/robots.txt",
                                       f"{BLOB}/s/sitemap.xml"]
    assert all(kw.get("allow_redirects") is False for u, kw in s.calls if not u.endswith("robots.txt"))
    assert all(kw["headers"]["User-Agent"] == UA for _, kw in s.calls)
    c.sleeps.clear()
    pc.get("o", f"{BLOB}/s/two.xml")
    assert c.sleeps and max(c.sleeps) == 30  # the new host's own crawl delay


def test_a_redirect_to_a_host_whose_robots_forbids_it_is_not_followed():
    pc, s, _ = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=302, headers={"Location": f"{BLOB}/p"}),
                       f"{BLOB}/robots.txt": Resp("User-agent: *\nDisallow: /\n")})
    assert pc.get("o", "https://x.example/a") is None
    assert f"{BLOB}/p" not in [u for u, _ in s.calls]
    pc, s, _ = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=302, headers={"Location": f"{BLOB}/p"}),
                       f"{BLOB}/robots.txt": Resp(status=429)})
    import requests
    with pytest.raises(requests.RequestException):  # the target host's trouble fails this fetch only
        pc.get("o", "https://x.example/a")
    assert pc.refused == {}


def test_redirects_stop_after_five_hops():
    import requests
    routes = {ROBOTS: Resp("")}
    for i in range(7):
        routes[f"https://x.example/{i}"] = Resp(status=302, headers={"Location": f"/{i + 1}"})
    pc, s, _ = client(routes)
    with pytest.raises(requests.TooManyRedirects):
        pc.get("o", "https://x.example/0")
    assert len(s.calls) == 1 + 6  # robots, the first request and five hops


@pytest.mark.parametrize("robots", [Resp(status=403), Resp(status=401), Resp(status=429), Resp(status=503),
                                    TimeoutError("t")])
def test_robots_trouble_on_a_redirect_target_fails_that_fetch_only(robots):
    """The redirect target's host is not the outlet's: its robots trouble neither refuses nor blocks the outlet."""
    import requests
    pc, s, _ = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=302, headers={"Location": f"{BLOB}/p"}),
                       f"{BLOB}/robots.txt": robots, "https://x.example/b": Resp("B")})
    with pytest.raises(requests.RequestException):
        pc.get("o", "https://x.example/a")
    assert pc.refused == {} and pc._blocked == set()
    assert pc.get("o", "https://x.example/b").text == "B"
    with pytest.raises(requests.RequestException):  # the host is not asked again this run
        pc.get("o", "https://x.example/a")
    assert [u for u, _ in s.calls].count(f"{BLOB}/robots.txt") == 1


def test_a_redirect_targets_trouble_does_not_spoil_that_host_for_its_own_outlet():
    pc, s, _ = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=302, headers={"Location": f"{BLOB}/p"}),
                       f"{BLOB}/robots.txt": Resp(status=403)})
    import requests
    with pytest.raises(requests.RequestException):
        pc.get("o", "https://x.example/a")
    assert pc.get("blob", f"{BLOB}/q") is None  # its own robots 403: blocked for the run, as before
    assert pc.refused == {"blob": "robots 403"} and pc._blocked == {"blob"}


def test_robots_redirects_are_followed_by_hand_and_paced():
    routes = {ROBOTS: Resp(status=301, headers={"Location": f"{BLOB}/robots.txt"}),
              f"{BLOB}/robots.txt": Resp("User-agent: *\nDisallow: /private\n"), "https://x.example/a": Resp("A")}
    pc, s, c = client(routes)
    assert pc.get("o", "https://x.example/a").text == "A"
    assert pc.get("o", "https://x.example/private/b") is None
    assert [u for u, _ in s.calls] == [ROBOTS, f"{BLOB}/robots.txt", "https://x.example/a"]
    assert all(kw.get("allow_redirects") is False for _, kw in s.calls)
    assert len(c.sleeps) == 2 and all(x >= 10 for x in c.sleeps)  # before the hop and before the article


def test_robots_redirects_stop_after_five_hops():
    routes = {ROBOTS: Resp(status=302, headers={"Location": "/r0"}), "https://x.example/a": Resp("A")}
    for i in range(7):
        routes[f"https://x.example/r{i}"] = Resp(status=302, headers={"Location": f"/r{i + 1}"})
    pc, s, _ = client(routes)
    with pytest.raises(Refused) as e:
        pc.get("o", "https://x.example/a")
    assert e.value.reason == "robots unreachable"
    assert len(s.calls) == 1 + 5  # robots.txt and five hops, never the article


def test_a_redirect_to_a_new_host_waits_the_outlets_delay():
    pc, s, c = client({ROBOTS: Resp(""), "https://x.example/a": Resp(status=302, headers={"Location": f"{BLOB}/p"}),
                       f"{BLOB}/robots.txt": Resp(""), f"{BLOB}/p": Resp("P")})
    assert pc.get("o", "https://x.example/a").text == "P"
    assert len(c.sleeps) == 3 and all(x >= 10 for x in c.sleeps)  # before x/a, blob's robots and blob/p


def test_a_robots_refusal_on_one_of_the_outlets_hosts_stops_its_other_hosts():
    it = "https://it.x.example"
    pc, s, _ = client({ROBOTS: Resp(status=403), f"{it}/robots.txt": Resp(""), f"{it}/a": Resp("A")})
    assert pc.get("o", "https://x.example/a") is None
    assert pc.get("o", f"{it}/a") is None
    assert [u for u, _ in s.calls] == [ROBOTS]
    assert pc.refused == {"o": "robots 403"}
