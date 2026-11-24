"""Tables for the Catholic-media source: articles, mentions, feed caches, the outlets that refuse us.

Keeps link, title, date, outlet, language and short passages only, and for outlets that republish one another a short
fingerprint of hashes (see duplicates); never article text.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

MAX_PASSAGE_WORDS = 25
REMOVE_AFTER = 7  # refused days in a row, and the calendar days they span at least, before an outlet is removed

SCHEMA = """
CREATE TABLE IF NOT EXISTS press_articles (
  url TEXT PRIMARY KEY, outlet TEXT, lang TEXT, title TEXT, published TEXT,
  found_at REAL, stage TEXT DEFAULT 'found', outcome TEXT, data_json TEXT, attempted_at REAL);
CREATE INDEX IF NOT EXISTS press_articles_found ON press_articles (found_at, outlet);
CREATE TABLE IF NOT EXISTS press_mentions (
  url TEXT, qid TEXT, label TEXT, kind TEXT, domain TEXT, p REAL,
  passages_json TEXT, route TEXT, stance REAL, stance_outcome TEXT, PRIMARY KEY (url, qid));
CREATE TABLE IF NOT EXISTS press_feeds (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT);
CREATE TABLE IF NOT EXISTS press_walks (url TEXT PRIMARY KEY, data_json TEXT);
CREATE TABLE IF NOT EXISTS press_refusals (outlet TEXT, day TEXT, reason TEXT, PRIMARY KEY (outlet, day));
CREATE TABLE IF NOT EXISTS press_reads (url TEXT PRIMARY KEY, outlet TEXT, day TEXT);
CREATE TABLE IF NOT EXISTS press_outlets (
  outlet TEXT PRIMARY KEY, ok_day TEXT, reinstated TEXT, removed TEXT, since TEXT, reason TEXT);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_today() -> str:
    """The day a refusal or a daily limit holds for: the UTC calendar day, whatever the machine's time zone."""
    return utc_now().date().isoformat()


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
        if "first_read" not in {r[1] for r in db.execute("PRAGMA table_info(press_outlets)")}:
            self._add_reads()
        db.commit()

    def _add_reads(self) -> None:
        """The first read and the days read of each outlet. A store made before them takes them from its articles (found
        in a feed that was read) and its last successful day: the days seen there are the least it was read."""
        days: dict[str, set[str]] = {}
        for outlet, at in self.db.execute("SELECT outlet, found_at FROM press_articles WHERE found_at IS NOT NULL"):
            days.setdefault(outlet, set()).add(datetime.fromtimestamp(at, timezone.utc).date().isoformat())
        for outlet, ok in self.db.execute("SELECT outlet, ok_day FROM press_outlets WHERE ok_day IS NOT NULL"):
            days.setdefault(outlet, set()).add(ok)
        # days_read first: an interrupted migration is done again as long as first_read is missing
        if "days_read" not in {r[1] for r in self.db.execute("PRAGMA table_info(press_outlets)")}:
            self.db.execute("ALTER TABLE press_outlets ADD COLUMN days_read INTEGER")
        self.db.execute("ALTER TABLE press_outlets ADD COLUMN first_read TEXT")
        for outlet, seen in days.items():
            self._ensure_outlet(outlet)
            self.db.execute("UPDATE press_outlets SET first_read=?, days_read=? WHERE outlet=?",
                            (min(seen), len(seen), outlet))

    @classmethod
    def of(cls, queue) -> "PressStore":
        return cls(queue.db)

    def add_article(self, url, outlet, lang, title=None, published=None) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO press_articles (url, outlet, lang, title, published, found_at) "
            "VALUES (?,?,?,?,?,?)", (url, outlet, lang, title, published, utc_now().timestamp()))
        self.db.commit()
        return cur.rowcount == 1

    def discovered(self, url, outlet, lang, published, limit: int, cap: int | None) -> str:
        """Add an article discovery found while the day's count is under `limit` and its outlet's under `cap` (None: no
        cap), in one statement, so processes cannot overrun them: "added"; else "known" (the URL was there), "day full" or
        "outlet full"."""
        if self.article(url) is not None:
            return "known"
        now = utc_now()
        start = datetime.combine(now.date(), datetime.min.time(), timezone.utc).timestamp()
        count = "(SELECT COUNT(*) FROM press_articles WHERE found_at >= ?"
        cur = self.db.execute(
            "INSERT OR IGNORE INTO press_articles (url, outlet, lang, published, found_at) SELECT ?,?,?,?,? "
            f"WHERE {count}) < ? AND (? IS NULL OR {count} AND outlet=?) < ?)",
            (url, outlet, lang, published, now.timestamp(), start, limit, cap, start, outlet, cap))
        self.db.commit()
        if cur.rowcount == 1:
            return "added"
        if self.article(url) is not None:
            return "known"
        return "day full" if self.found_today() >= limit else "outlet full"

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

    def join(self, post: Article, paper: Article) -> None:
        """The post is a copy of the newspaper's article: both, with the copies each already has, become one group under
        one article, the newspaper's first found (another of its URLs may be a copy too); the evidence of all counts for
        the newspaper."""
        roots = [post.same_as or post.url, paper.same_as or paper.url]
        with self.db:
            rows = self.db.execute("SELECT url, outlet, same_as FROM press_articles WHERE url IN (?,?) OR same_as IN (?,?) "
                                   "ORDER BY found_at, rowid", roots * 2).fetchall()
            first = next(r[0] for r in rows if r[1] == paper.outlet and r[2] is None)
            self.db.executemany("UPDATE press_articles SET same_as=?, counts_as=? WHERE url=?",
                                [(first, paper.outlet, r[0]) for r in rows if r[0] != first])
            self.db.execute("UPDATE press_articles SET same_as=NULL WHERE url=?", (first,))

    def copies(self, url) -> list[Article]:
        """The other copies of the article, its first publication first."""
        art = self.article(url)
        first = (art.same_as if art else None) or url
        rows = self.db.execute(ARTICLE + " WHERE (url=? OR same_as=?) AND url!=? ORDER BY url!=?, url",
                               (first, first, url, first)).fetchall()
        return [_article(r) for r in rows]

    def attempted(self, url: str) -> None:
        self.db.execute("UPDATE press_articles SET attempted_at=? WHERE url=?", (utc_now().timestamp(), url))
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

    # The day's budget (UTC), shared by every run. An article counts once a day, the first time a stage sets out to
    # read it, whatever comes of it; discovery counts the articles it added.

    def found_today(self, outlet: str | None = None) -> int:
        """The articles (of the outlet) added today."""
        start = datetime.combine(utc_now().date(), datetime.min.time(), timezone.utc).timestamp()
        sql, args = "SELECT COUNT(*) FROM press_articles WHERE found_at >= ?", [start]
        if outlet is not None:
            sql += " AND outlet=?"; args.append(outlet)
        return self.db.execute(sql, args).fetchone()[0]

    def read_today(self, outlet: str | None = None) -> int:
        """The articles (of the outlet) counted today."""
        sql, args = "SELECT COUNT(*) FROM press_reads WHERE day=?", [utc_today()]
        if outlet is not None:
            sql += " AND outlet=?"; args.append(outlet)
        return self.db.execute(sql, args).fetchone()[0]

    def was_read_today(self, url: str) -> bool:
        return self.db.execute("SELECT 1 FROM press_reads WHERE url=? AND day=?", (url, utc_today())).fetchone() is not None

    def take(self, url: str, outlet: str, limit: int, cap: int | None) -> bool:
        """Whether the article may be read: it was counted today already, or it is counted now, leaving the day's count
        within `limit` and its outlet's within `cap` (None: no cap). One statement, so processes cannot overrun it."""
        if self.was_read_today(url):
            return True
        today = utc_today()
        self.db.execute("DELETE FROM press_reads WHERE day < ?", (today,))
        cur = self.db.execute(
            "INSERT OR IGNORE INTO press_reads SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM press_reads WHERE day=?) < ? "
            "AND (? IS NULL OR (SELECT COUNT(*) FROM press_reads WHERE day=? AND outlet=?) < ?)",
            (url, outlet, today, today, limit, cap, today, outlet, cap))
        self.db.commit()
        return cur.rowcount == 1 or self.was_read_today(url)

    # The outlets that refuse us. A refusal stops the outlet for its UTC day. A day counts as refused toward removal
    # when no successful response came from the outlet that day; a successful day, or putting the outlet back, starts
    # the count again; a day with neither (no run, or timeouts and errors only) neither counts nor breaks it.

    def _ensure_outlet(self, outlet: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO press_outlets (outlet) VALUES (?)", (outlet,))

    def _counted(self, outlet: str) -> list[str]:
        """The refused days since the last successful day and the last reinstatement, oldest first."""
        return [r[0] for r in self.db.execute(
            "SELECT day FROM press_refusals r LEFT JOIN press_outlets o USING (outlet) WHERE r.outlet=? "
            "AND day > MAX(COALESCE(ok_day, ''), COALESCE(reinstated, '')) ORDER BY day", (outlet,)).fetchall()]

    def refuse(self, outlet: str, reason: str) -> None:
        """Record that the outlet refused us today; on the REMOVE_AFTERth refused day in a row it is removed."""
        today = utc_today()
        self._ensure_outlet(outlet)
        self.db.execute("INSERT OR IGNORE INTO press_refusals VALUES (?,?,?)", (outlet, today, reason))
        days = self._counted(outlet)
        if (len(days) >= REMOVE_AFTER
                and (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1 >= REMOVE_AFTER):
            self.db.execute("UPDATE press_outlets SET removed=?, since=?, reason=? WHERE outlet=? AND removed IS NULL",
                            (today, days[0], reason, outlet))
        self.db.commit()

    def succeeded(self, outlet: str) -> None:
        """Record that the outlet answered successfully (not its robots.txt) today."""
        today = utc_today()
        r = self.db.execute("SELECT ok_day FROM press_outlets WHERE outlet=?", (outlet,)).fetchone()
        if r and r[0] and r[0] >= today:
            return
        self._ensure_outlet(outlet)
        self.db.execute("UPDATE press_outlets SET ok_day=?, first_read=COALESCE(first_read, ?), "
                        "days_read=COALESCE(days_read, 0)+1 WHERE outlet=?", (today, today, outlet))
        self.db.commit()

    def reads(self) -> dict[str, tuple[str, int]]:
        """{outlet: (the first day it was read, on how many days)} of the outlets ever read (robots.txt aside)."""
        return {r[0]: (r[1], r[2]) for r in self.db.execute(
            "SELECT outlet, first_read, days_read FROM press_outlets WHERE first_read IS NOT NULL ORDER BY outlet")
            .fetchall()}

    def refused_today(self) -> dict[str, str]:
        """{outlet: reason} of the outlets that refused us today."""
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT outlet, reason FROM press_refusals WHERE day=? ORDER BY outlet", (utc_today(),)).fetchall()}

    def removed(self) -> dict[str, dict]:
        """{outlet: on (the day it was removed), since (the first refused day), reason} of the removed outlets."""
        return {r[0]: {"on": r[1], "since": r[2], "reason": r[3]} for r in self.db.execute(
            "SELECT outlet, removed, since, reason FROM press_outlets WHERE removed IS NOT NULL ORDER BY outlet")
            .fetchall()}

    def stopped(self) -> dict[str, str]:
        """{outlet: why} of the outlets to ask nothing of today: refused today, or removed."""
        return self.refused_today() | dict.fromkeys(self.removed(), "removed")

    def refusals(self) -> dict[str, dict]:
        """The outlets whose last word was a refusal, the removed ones aside: {outlet: on (its last refusal), reason,
        since and days (the refused days counted toward removal; None and 0 when that day also had a success)}."""
        out, removed = {}, self.removed()
        for outlet, day, reason, ok, back in self.db.execute(
                "SELECT r.outlet, MAX(day), r.reason, COALESCE(ok_day, ''), COALESCE(reinstated, '') "
                "FROM press_refusals r LEFT JOIN press_outlets o USING (outlet) GROUP BY r.outlet ORDER BY r.outlet"
                ).fetchall():
            if outlet in removed or day < max(ok, back):
                continue
            days = self._counted(outlet)
            out[outlet] = {"on": day, "since": days[0] if days else None, "days": len(days), "reason": reason}
        return out

    def reinstate(self, outlet: str) -> bool:
        """Put a removed outlet back (False: it is not removed). Its refusals so far count no more; one of today still
        stops it for today."""
        cur = self.db.execute("UPDATE press_outlets SET removed=NULL, since=NULL, reason=NULL, reinstated=? "
                              "WHERE outlet=? AND removed IS NOT NULL", (utc_today(), outlet))
        self.db.commit()
        return cur.rowcount == 1

    def copy_refusals(self, other: "PressStore") -> None:
        """Take over what `other` knows of the outlets that refuse us (for a throwaway store)."""
        for table, columns in (("press_refusals", "outlet, day, reason"),
                               ("press_outlets", "outlet, ok_day, reinstated, removed, since, reason")):
            rows = [tuple(r) for r in other.db.execute(f"SELECT {columns} FROM {table}").fetchall()]
            marks = ",".join("?" * (columns.count(",") + 1))
            self.db.executemany(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})", rows)
        self.db.commit()
