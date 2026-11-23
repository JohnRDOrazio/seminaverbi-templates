import sqlite3
from dataclasses import replace

import pytest

from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue


def test_tables_live_in_the_queue_db(tmp_path):
    q = Queue(tmp_path / "q.sqlite"); s = PressStore.of(q)
    assert s.add_article("https://x/a", "sir", "it") is True
    assert s.add_article("https://x/a", "sir", "it") is False
    names = {r[0] for r in q.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"press_articles", "press_mentions", "press_feeds"} <= names


def test_no_body_column():
    s = PressStore(sqlite3.connect(":memory:"))
    cols = {c[1] for c in s.db.execute("PRAGMA table_info(press_articles)")}
    assert not cols & {"text", "body", "html", "content"}


def test_mention_upsert_and_route():
    s = PressStore(sqlite3.connect(":memory:")); s.add_article("u", "sir", "it")
    m = Mention("u", "Q1", "Silence", "work", "film", 0.7, ["a b c"], "candidate")
    s.add_mention(m); s.add_mention(replace(m, p=0.9))
    assert [x.p for x in s.mentions(qid="Q1")] == [0.9]
    s.set_route("u", "Q1", "enrichment"); assert s.mentions(route="enrichment")[0].qid == "Q1"
    assert s.unrouted() == []


def test_unrouted_and_passage_limit():
    s = PressStore(sqlite3.connect(":memory:")); s.add_article("u", "sir", "it")
    s.add_mention(Mention("u", "Q1", "L", "person", "saints", 0.8, [], None))
    assert [m.qid for m in s.unrouted()] == ["Q1"]
    with pytest.raises(ValueError):
        s.add_mention(Mention("u", "Q2", "L", "work", "film", 0.8, [" ".join(["w"] * 26)], None))
    s.add_mention(Mention("u", "Q3", "L", "work", "film", 0.8, [" ".join(["w"] * 25)], None))


def test_articles_stage_and_set_article():
    s = PressStore(sqlite3.connect(":memory:"))
    s.add_article("a", "sir", "it", "T", "2026-01-01"); s.add_article("b", "avvenire", "it")
    s.add_article("c", "sir", "it")
    assert [a.url for a in s.articles("found", outlet="sir")] == ["a", "c"]
    assert len(s.articles("found", limit=1)) == 1
    s.set_article("a", "filtered", "keep", {"culture": 0.7})
    a = s.articles("filtered")[0]
    assert (a.url, a.outcome, a.data, a.title, a.published) == ("a", "keep", {"culture": 0.7}, "T", "2026-01-01")
    assert [x.url for x in s.articles("found")] == ["b", "c"]


def test_feed_cache():
    s = PressStore(sqlite3.connect(":memory:"))
    assert s.feed_cache("f") == (None, None)
    s.set_feed_cache("f", "e1", "lm1"); s.set_feed_cache("f", "e2", None)
    assert s.feed_cache("f") == ("e2", None)
