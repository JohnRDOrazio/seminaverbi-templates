"""Tables for the Catholic-media source: articles, mentions, feed caches.

Keeps link, title, date, outlet, language and short passages only, and for outlets that republish one another a short
fingerprint of hashes (see duplicates); never article text.
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
  passages_json TEXT, route TEXT, stance REAL, stance_outcome TEXT, PRIMARY KEY (url, qid));
CREATE TABLE IF NOT EXISTS press_feeds (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT);
CREATE TABLE IF NOT EXISTS press_walks (url TEXT PRIMARY KEY, data_json TEXT);
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
    attribution: str | None = None  # who it is cited as, when not its outlet (a republished article)
    counts_as: str | None = None  # the outlet that published it first, when not its own
    same_as: str | None = None  # the article this one is a copy of (its first publication)

    @property
    def evidence_outlet(self) -> str:
        """The outlet its evidence counts for."""
        return self.counts_as or self.outlet


@dataclass
class Mention:
    url: str
    qid: str
    label: str
    kind: str  # "work" | "person"
    domain: str
    p: float
    passages: list[str] = field(default_factory=list)
    route: str | None = None  # "candidate" | "enrichment" | "waiting" | "dropped" | None until routed
    stance: float | None = None  # probability that the article finds something in the item (None: not asked)
    stance_outcome: str | None = None  # "bearing" | "mixed" | "neutral" | "critical"; None: not asked (or an old score)

    @property
    def critical(self) -> bool:
        return self.stance_outcome == "critical"

    @property
    def supports(self) -> bool:
        """The mention may make a candidate and be evidence for one."""
        return self.stance_outcome not in ("neutral", "critical")


ARTICLE = ("SELECT url, outlet, lang, title, published, stage, outcome, data_json, attribution, counts_as, "
           "same_as FROM press_articles")


def _article(r) -> Article:
    return Article(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7]) if r[7] else {}, *r[8:])


class PressStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.executescript(SCHEMA)
        if "attempted_at" not in {r[1] for r in db.execute("PRAGMA table_info(press_articles)")}:
            db.execute("ALTER TABLE press_articles ADD COLUMN attempted_at REAL")  # a store made before it
        columns = {r[1] for r in db.execute("PRAGMA table_info(press_articles)")}
        for name in ("attribution", "counts_as", "same_as", "fingerprint"):
            if name not in columns:
                db.execute(f"ALTER TABLE press_articles ADD COLUMN {name} TEXT")
        columns = {r[1] for r in db.execute("PRAGMA table_info(press_mentions)")}
        for name, kind in (("stance", "REAL"), ("stance_outcome", "TEXT")):
            if name not in columns:
                db.execute(f"ALTER TABLE press_mentions ADD COLUMN {name} {kind}")
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
        sql, args = ARTICLE + " WHERE stage=?", [stage]
        if outlet is not None:
            sql += " AND outlet=?"; args.append(outlet)
        sql += " ORDER BY " + ("attempted_at IS NOT NULL, attempted_at, " if tried_last else "") + "found_at, rowid"
        if limit is not None:
            sql += " LIMIT ?"; args.append(limit)
        return [_article(r) for r in self.db.execute(sql, args).fetchall()]

    def article(self, url: str) -> Article | None:
        r = self.db.execute(ARTICLE + " WHERE url=?", (url,)).fetchone()
        return _article(r) if r else None

    def set_credit(self, url, attribution: str, published: str, counts_as: str) -> None:
        """The article first appeared in `counts_as`, on `published`."""
        self.db.execute("UPDATE press_articles SET attribution=?, published=?, counts_as=? WHERE url=?",
                        (attribution, published, counts_as, url))
        self.db.commit()

    def set_fingerprint(self, url, fingerprint: str) -> None:
        self.db.execute("UPDATE press_articles SET fingerprint=? WHERE url=?", (fingerprint, url))
        self.db.commit()

    def fingerprints(self, outlet) -> list[tuple[str, str]]:
        """(url, fingerprint) of the outlet's articles that have one."""
        return [tuple(r) for r in self.db.execute(
            "SELECT url, fingerprint FROM press_articles WHERE outlet=? AND fingerprint IS NOT NULL "
            "ORDER BY found_at, rowid", (outlet,)).fetchall()]

    def set_same(self, url, first: str, outlet: str) -> None:
        """The article is a copy of `first`, an article of `outlet`: its evidence counts for that outlet."""
        self.db.execute("UPDATE press_articles SET same_as=?, counts_as=? WHERE url=?", (first, outlet, url))
        self.db.commit()

    def copies(self, url) -> list[Article]:
        """The other copies of the article, its first publication first."""
        art = self.article(url)
        first = (art.same_as if art else None) or url
        rows = self.db.execute(ARTICLE + " WHERE (url=? OR same_as=?) AND url!=? ORDER BY url!=?, url",
                               (first, first, url, first)).fetchall()
        return [_article(r) for r in rows]

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
            "INSERT INTO press_mentions (url, qid, label, kind, domain, p, passages_json, route, "
            "stance, stance_outcome) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url, qid) DO UPDATE SET "
            "label=excluded.label, kind=excluded.kind, domain=excluded.domain, p=excluded.p, "
            "passages_json=excluded.passages_json, route=COALESCE(excluded.route, route), "
            "stance=COALESCE(excluded.stance, stance), "
            "stance_outcome=COALESCE(excluded.stance_outcome, stance_outcome)",
            (m.url, m.qid, m.label, m.kind, m.domain, m.p, json.dumps(m.passages), m.route, m.stance,
             m.stance_outcome))
        self.db.commit()

    def _mentions(self, where: str, args: list) -> list[Mention]:
        rows = self.db.execute(
            "SELECT url, qid, label, kind, domain, p, passages_json, route, stance, stance_outcome FROM press_mentions"
            + where + " ORDER BY url, qid", args).fetchall()
        return [Mention(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "[]"), r[7], r[8], r[9])
                for r in rows]

    def mentions(self, qid=None, route=None) -> list[Mention]:
        conds, args = [], []
        if qid is not None:
            conds.append("qid=?"); args.append(qid)
        if route is not None:
            conds.append("route=?"); args.append(route)
        return self._mentions(" WHERE " + " AND ".join(conds) if conds else "", args)

    def unrouted(self) -> list[Mention]:
        return self._mentions(" WHERE route IS NULL", [])

    def set_route(self, url, qid, route, domain: str | None = None) -> None:
        """The mention's route, and its domain when given, in one committed update."""
        self.db.execute("UPDATE press_mentions SET route=?, domain=COALESCE(?, domain) WHERE url=? AND qid=?",
                        (route, domain, url, qid))
        self.db.commit()

    def feed_cache(self, url) -> tuple[str | None, str | None]:
        r = self.db.execute("SELECT etag, last_modified FROM press_feeds WHERE url=?", (url,)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def set_feed_cache(self, url, etag, last_modified) -> None:
        self.db.execute("INSERT OR REPLACE INTO press_feeds VALUES (?,?,?)", (url, etag, last_modified))
        self.db.commit()

    def walk(self, url) -> dict:
        """How far the walk of a feed's archive has come (see outlets._walk); {} before it begins."""
        r = self.db.execute("SELECT data_json FROM press_walks WHERE url=?", (url,)).fetchone()
        return json.loads(r[0]) if r else {}

    def set_walk(self, url, data: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO press_walks VALUES (?,?)", (url, json.dumps(data)))
        self.db.commit()
