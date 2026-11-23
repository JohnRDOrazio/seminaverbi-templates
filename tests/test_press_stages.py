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
