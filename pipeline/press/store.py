"""Tables for the Catholic-media source: articles, mentions, feed caches.

Keeps link, title, date, outlet, language and short passages only; never article text.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field

MAX_PASSAGE_WORDS = 25

SCHEMA = """
CREATE TABLE IF NOT EXISTS press_articles (
  url TEXT PRIMARY KEY, outlet TEXT, lang TEXT, title TEXT, published TEXT,
  found_at REAL, stage TEXT DEFAULT 'found', outcome TEXT, data_json TEXT, attempted_at REAL);
CREATE TABLE IF NOT EXISTS press_mentions (
  url TEXT, qid TEXT, label TEXT, kind TEXT, domain TEXT, p REAL,
  passages_json TEXT, route TEXT, PRIMARY KEY (url, qid));
CREATE TABLE IF NOT EXISTS press_feeds (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT);
"""


@dataclass
class Article:
    url: str
    outlet: str
    lang: str
    title: str | None = None
    published: str | None = None
    stage: str = "found"
    outcome: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class Mention:
    url: str
    qid: str
    label: str
    kind: str  # "work" | "person"
    domain: str
    p: float
    passages: list[str] = field(default_factory=list)
    route: str | None = None  # "candidate" | "enrichment" | "waiting" | None until routed


class PressStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.executescript(SCHEMA)
        if "attempted_at" not in {r[1] for r in db.execute("PRAGMA table_info(press_articles)")}:
            db.execute("ALTER TABLE press_articles ADD COLUMN attempted_at REAL")  # a store made before it
        db.commit()

    @classmethod
    def of(cls, queue) -> "PressStore":
        return cls(queue.db)

    def add_article(self, url, outlet, lang, title=None, published=None) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO press_articles (url, outlet, lang, title, published, found_at) "
            "VALUES (?,?,?,?,?,?)", (url, outlet, lang, title, published, time.time()))
        self.db.commit()
        return cur.rowcount == 1

    def articles(self, stage: str, outlet: str | None = None, limit: int | None = None,
                 tried_last: bool = False) -> list[Article]:
        """In discovery order; `tried_last`: the articles never attempted first, then the oldest attempt first."""
        sql, args = "SELECT url, outlet, lang, title, published, stage, outcome, data_json " \
                    "FROM press_articles WHERE stage=?", [stage]
        if outlet is not None:
            sql += " AND outlet=?"; args.append(outlet)
        sql += " ORDER BY " + ("attempted_at IS NOT NULL, attempted_at, " if tried_last else "") + "found_at, rowid"
        if limit is not None:
            sql += " LIMIT ?"; args.append(limit)
        return [Article(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7]) if r[7] else {})
                for r in self.db.execute(sql, args).fetchall()]

    def article(self, url: str) -> Article | None:
        r = self.db.execute("SELECT url, outlet, lang, title, published, stage, outcome, data_json "
                            "FROM press_articles WHERE url=?", (url,)).fetchone()
        return Article(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7]) if r[7] else {}) if r else None

    def attempted(self, url: str) -> None:
        self.db.execute("UPDATE press_articles SET attempted_at=? WHERE url=?", (time.time(), url))
        self.db.commit()

    def set_article(self, url, stage, outcome, data: dict) -> None:
        self.db.execute("UPDATE press_articles SET stage=?, outcome=?, data_json=? WHERE url=?",
                        (stage, outcome, json.dumps(data), url))
        self.db.commit()

    def add_mention(self, m: Mention) -> None:
        for p in m.passages:
            if len(p.split()) > MAX_PASSAGE_WORDS:
                raise ValueError(f"passage over {MAX_PASSAGE_WORDS} words")
        # Upsert: evidence fields are refreshed, an existing route survives a re-find with route=None.
        self.db.execute(
            "INSERT INTO press_mentions VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(url, qid) DO UPDATE SET "
            "label=excluded.label, kind=excluded.kind, domain=excluded.domain, p=excluded.p, "
            "passages_json=excluded.passages_json, route=COALESCE(excluded.route, route)",
            (m.url, m.qid, m.label, m.kind, m.domain, m.p, json.dumps(m.passages), m.route))
        self.db.commit()

    def _mentions(self, where: str, args: list) -> list[Mention]:
        rows = self.db.execute(
            "SELECT url, qid, label, kind, domain, p, passages_json, route FROM press_mentions"
            + where + " ORDER BY url, qid", args).fetchall()
        return [Mention(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "[]"), r[7]) for r in rows]

    def mentions(self, qid=None, route=None) -> list[Mention]:
        conds, args = [], []
        if qid is not None:
            conds.append("qid=?"); args.append(qid)
        if route is not None:
            conds.append("route=?"); args.append(route)
        return self._mentions(" WHERE " + " AND ".join(conds) if conds else "", args)

    def unrouted(self) -> list[Mention]:
        return self._mentions(" WHERE route IS NULL", [])

    def set_mention_domain(self, url, qid, domain) -> None:
        self.db.execute("UPDATE press_mentions SET domain=? WHERE url=? AND qid=?", (domain, url, qid))
        self.db.commit()

    def set_route(self, url, qid, route) -> None:
        self.db.execute("UPDATE press_mentions SET route=? WHERE url=? AND qid=?", (route, url, qid))
        self.db.commit()

    def feed_cache(self, url) -> tuple[str | None, str | None]:
        r = self.db.execute("SELECT etag, last_modified FROM press_feeds WHERE url=?", (url,)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def set_feed_cache(self, url, etag, last_modified) -> None:
        self.db.execute("INSERT OR REPLACE INTO press_feeds VALUES (?,?,?)", (url, etag, last_modified))
        self.db.commit()
