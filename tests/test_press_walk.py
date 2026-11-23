"""Walking a blog's label feeds (Atom, newest change first, cut by date) over successive runs."""
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from pipeline.press import outlets as mod
from pipeline.press import store as press_store
from pipeline.press.fetch import Refused
from pipeline.press.outlets import discover
from pipeline.press.store import PressStore

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
days = []  # one per run: each run is on a day of its own


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class Blog:
    """Serves its posts {n: updated minute} like Blogger: newest change first, updated-max inclusive, rel=next when
    there are more; `unchanged` answers the top page's conditional requests with 304."""

    def __init__(self, posts):
        self.labels = {"A": dict(posts)}
        self.calls, self.remembered, self.fail, self.unchanged, self.boundary = [], [], None, False, True

    @property
    def posts(self):
        return self.labels["A"]

    def get(self, outlet, url, conditional=False):
        self.calls.append((url, conditional))
        if self.fail and len(self.calls) in self.fail:
            raise self.fail[len(self.calls)]
        if conditional and self.unchanged:
            return None
        q = parse_qs(urlsplit(url).query)
        assert q["orderby"] == ["updated"] and "start-index" not in q
        size = int(q["max-results"][0])
        items = sorted(self.labels[urlsplit(url).path[1:]].items(), key=lambda kv: -kv[1])
        if "updated-max" in q:
            top = datetime.fromisoformat(q["updated-max"][0])
            items = [(n, t) for n, t in items
                     if T0 + timedelta(minutes=t) < top or (self.boundary and T0 + timedelta(minutes=t) == top)]
        more = "<link rel='next' href='x'/>" if len(items) > size else ""
        body = "".join(
            f"<entry><published>2020-01-01T00:00:00Z</published>"
            f"<updated>{(T0 + timedelta(minutes=t)).isoformat()}</updated>"
            f"<link rel='alternate' href='https://b.example/{n}.html'/></entry>" for n, t in items[:size])
        return Resp(f"<feed xmlns='http://www.w3.org/2005/Atom'>{more}{body}</feed>")

    def remember(self, url, response):
        self.remembered.append(url)


@pytest.fixture(autouse=True)
def small_pages(monkeypatch):
    monkeypatch.setattr(mod, "WALK_PAGE", 4)
    days.clear()
    monkeypatch.setattr(press_store, "utc_now", lambda: T0 + timedelta(days=len(days)))


def store():
    return PressStore(sqlite3.connect(":memory:"))


def outlet(labels="A", **kw):
    return {"id": "b", "name": "b", "langs": ["it"], "culture_paths": [], "since": None,
            "entries": [{"url": f"https://b.example/{x}", "lang": "it", "walk": True} for x in labels], **kw}


def found(s):
    return [int(a.url.rsplit("/", 1)[1][:-5]) for a in s.articles("found")]


def run(s, blog, limit=100, **kw):
    days.append(1)
    blog.calls.clear()
    return discover(s, blog, [outlet(**kw)], limit)["b"]


def test_the_archive_is_walked_over_successive_runs_then_only_the_top_is_read():
    s, blog = store(), Blog({n: n for n in range(30)})
    for expected in (7, 14, 21, 28, 30):
        run(s, blog, 7)
        assert len(found(s)) == expected
    assert sorted(found(s)) == list(range(30))
    run(s, blog)  # whatever is left of the walk
    run(s, blog)
    assert [u for u, _ in blog.calls] == ["https://b.example/A?orderby=updated&max-results=4"]
    assert blog.calls[0][1] is True  # conditional
    assert [a.published for a in s.articles("found")][:1] == ["2020-01-01"]


def test_a_long_walk_starts_again_where_it_stopped():
    s, blog = store(), Blog({n: n for n in range(100)})
    run(s, blog, 50)
    run(s, blog, 4)
    assert len(blog.calls) <= 3  # the top page, the page it stopped in, perhaps one more


def test_new_changed_and_removed_posts_during_the_walk_skip_nothing():
    s, blog = store(), Blog({n: n for n in range(30)})
    run(s, blog, 10)
    for n in (29, 27, 25, 24, 22):  # removed: offsets would shift
        del blog.posts[n]
    for n in range(100, 112):  # more new posts than a page holds
        blog.posts[n] = n
    blog.posts[3] = 200  # an old post not reached yet, changed: now on top
    blog.posts[300] = 5.5  # dated back among those not reached
    run(s, blog, 6)
    blog.posts[400] = 400
    for _ in range(8):
        run(s, blog, 6)
    assert set(blog.posts) <= set(found(s))
    assert len(found(s)) == len(set(found(s)))


def test_a_post_labelled_later_is_found():
    """Giving an old post the label changes it, so it comes to the top of the label's feed."""
    s, blog = store(), Blog({n: n for n in range(6)})
    run(s, blog), run(s, blog)
    blog.posts[50] = 60
    assert run(s, blog) == 1 and 50 in found(s)


def test_new_posts_come_before_the_rest_of_the_archive():
    s, blog = store(), Blog({n: n for n in range(30)})
    run(s, blog, 5)
    blog.posts[99] = 99
    assert run(s, blog, 1) == 1 and 99 in found(s)


def test_a_post_under_several_labels_is_one_article():
    s, blog = store(), Blog({n: n for n in range(6)})
    blog.labels["B"] = {n: n for n in range(3, 9)}
    assert run(s, blog, labels="AB") == 9 and sorted(found(s)) == list(range(9))


def test_an_interrupted_top_pass_is_done_again_from_the_top():
    s, blog = store(), Blog({n: n for n in range(6)})
    run(s, blog), run(s, blog)
    for n in range(100, 120):
        blog.posts[n] = n
    blog.remembered.clear()
    run(s, blog, 6)  # stopped inside the second page
    assert blog.remembered == []  # or the unread pages would hide behind a 304
    for _ in range(3):
        run(s, blog, 6)
    assert set(blog.posts) <= set(found(s))
    run(s, blog, 6)
    assert len(blog.calls) == 1 and blog.remembered


@pytest.mark.parametrize("error", [Refused("b", "429"), TimeoutError("slow")])
def test_a_failure_part_way_leaves_the_walk_to_go_on(error):
    s, blog = store(), Blog({n: n for n in range(20)})
    blog.fail = {3: error}
    run(s, blog)
    assert 0 < len(found(s)) < 20
    blog.fail = None
    run(s, blog)
    assert len(found(s)) == 20


def test_a_304_at_the_top_still_walks_the_archive():
    s, blog = store(), Blog({n: n for n in range(20)})
    run(s, blog, 6)
    blog.unchanged = True
    run(s, blog)
    assert len(found(s)) == 20


@pytest.mark.parametrize("boundary", [True, False])
def test_posts_changed_in_the_same_second_are_all_read(boundary):
    """A mass relabelling leaves many posts with one date: the page is made larger rather than skipped."""
    s, blog = store(), Blog({**{n: 10 for n in range(11)}, 50: 50, 60: 1})
    blog.boundary = boundary
    for _ in range(6):
        run(s, blog, 5)
    assert set(found(s)) == set(blog.posts)


def test_more_posts_in_one_second_than_the_largest_page_are_reported_not_skipped(capsys, monkeypatch):
    monkeypatch.setattr(mod, "WALK_MAX_PAGE", 8)
    s, blog = store(), Blog({**{n: 10 for n in range(11)}, 60: 1})
    run(s, blog)
    assert 60 not in found(s) and len(blog.calls) < 6
    assert "same second" in capsys.readouterr().err


def test_an_empty_first_page_does_not_end_the_walk():
    s, blog = store(), Blog({})
    run(s, blog)
    blog.labels["A"] = {n: n for n in range(6)}
    run(s, blog)
    assert len(found(s)) == 6


def test_a_page_that_is_no_feed_or_has_an_undated_entry_is_reported(capsys):
    class Bad(Blog):
        text = "<html>oops"

        def get(self, outlet, url, conditional=False):
            return Resp(self.text)
    s, blog = store(), Bad({})
    run(s, blog)
    blog.text = ("<feed xmlns='http://www.w3.org/2005/Atom'><entry><link rel='alternate' href='https://b.example/1'/>"
                 "</entry></feed>")
    run(s, blog)
    assert capsys.readouterr().err.count("not a feed that can be walked") == 2


def test_the_outlets_cap_bounds_what_a_run_discovers():
    s, blog = store(), Blog({n: n for n in range(30)})
    assert run(s, blog, max_per_day=7) == 7
    assert run(s, blog, max_per_day=7) == 7
