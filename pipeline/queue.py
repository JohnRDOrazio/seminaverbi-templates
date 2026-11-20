"""SQLite candidate queue: candidates, per-stage outcomes, cached Jev answers."""
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

OUTCOMES = ("pass", "drop", "translation")
STATUSES = ("open", "approved", "created", "declined")
STATUS_FIELDS = ("page", "reason")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    qid TEXT PRIMARY KEY, domain TEXT NOT NULL, sources TEXT NOT NULL,
    labels TEXT NOT NULL, sitelinks TEXT NOT NULL, calibration INTEGER NOT NULL,
    status TEXT, page TEXT, reason TEXT, votes INTEGER, added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outcomes (
    qid TEXT NOT NULL, stage INTEGER NOT NULL, outcome TEXT NOT NULL,
    data_json TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (qid, stage));
CREATE TABLE IF NOT EXISTS jev (
    qid TEXT NOT NULL, qhash TEXT NOT NULL, question TEXT NOT NULL,
    answer_json TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (qid, qhash));
"""


@dataclass
class Row:
    qid: str
    domain: str
    sources: list
    labels: dict
    sitelinks: dict
    calibration: bool
    outcomes: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    status: str | None = None
    page: str | None = None
    votes: int | None = None


class Queue:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if "votes" not in {c["name"] for c in self.db.execute("PRAGMA table_info(candidates)")}:
            self.db.execute("ALTER TABLE candidates ADD COLUMN votes INTEGER")  # queue made before votes existed
        self.db.commit()

    def add(self, qid, domain, source, labels, sitelinks, calibration=False, data: dict | None = None) -> bool:
        """Add a candidate or a source to an existing one; `data` is kept as the stage-0 data (merged on a repeat)."""
        added = self._add(qid, domain, source, labels, sitelinks, calibration)
        if data is not None:
            old = self.db.execute("SELECT data_json FROM outcomes WHERE qid=? AND stage=0", (qid,)).fetchone()
            merged = {**(json.loads(old["data_json"]) if old else {}), **data}
            self.set_outcome(qid, 0, "pass", merged)
        return added

    def _add(self, qid, domain, source, labels, sitelinks, calibration) -> bool:
        row = self.db.execute("SELECT sources FROM candidates WHERE qid=?", (qid,)).fetchone()
        if row:
            sources = json.loads(row["sources"])
            if source not in sources:
                sources.append(source)
                self.db.execute("UPDATE candidates SET sources=? WHERE qid=?",
                                (json.dumps(sources), qid))
                self.db.commit()
            if calibration:  # a film article first found by a facet must still be a calibration row
                self.db.execute("UPDATE candidates SET calibration=1 WHERE qid=?", (qid,))
                self.db.commit()
            return False
        self.db.execute(
            "INSERT INTO candidates (qid, domain, sources, labels, sitelinks, calibration, added_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (qid, domain, json.dumps([source]), json.dumps(labels), json.dumps(sitelinks),
             int(calibration), time.time()))
        self.db.commit()
        return True

    def _rows(self, where: str, args: tuple, limit: int | None = None) -> list[Row]:
        sql = f"SELECT * FROM candidates WHERE {where} ORDER BY added_at, qid"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        out = []
        for c in self.db.execute(sql, args).fetchall():
            row = Row(c["qid"], c["domain"], json.loads(c["sources"]), json.loads(c["labels"]),
                      json.loads(c["sitelinks"]), bool(c["calibration"]),
                      status=c["status"], page=c["page"], votes=c["votes"])
            for o in self.db.execute("SELECT stage, outcome, data_json FROM outcomes WHERE qid=?",
                                     (row.qid,)):
                row.outcomes[o["stage"]] = o["outcome"]
                row.data[o["stage"]] = json.loads(o["data_json"])
            out.append(row)
        return out

    def pending(self, stage: int, domain: str | None = None, limit: int | None = None) -> list[Row]:
        where = ("(status IS NULL OR status != 'declined')"
                 " AND NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.qid=candidates.qid AND o.stage=?)")
        args: list = [stage]
        if stage > 1:
            where += (" AND EXISTS (SELECT 1 FROM outcomes o WHERE o.qid=candidates.qid"
                      " AND o.stage=? AND o.outcome='pass')")
            args.append(stage - 1)
        if domain is not None:
            where += " AND domain=?"
            args.append(domain)
        return self._rows(where, tuple(args), limit)

    def set_outcome(self, qid, stage: int, outcome: str, data: dict | None = None):
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self.db.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?)",
                        (qid, stage, outcome, json.dumps(data or {}), time.time()))
        self.db.commit()

    def redo(self, stage: int, qid: str | None = None):
        if qid is None:
            self.db.execute("DELETE FROM outcomes WHERE stage>=?", (stage,))
        else:
            self.db.execute("DELETE FROM outcomes WHERE stage>=? AND qid=?", (stage, qid))
        self.db.commit()

    def jev_get(self, qid, qhash) -> dict | None:
        r = self.db.execute("SELECT answer_json FROM jev WHERE qid=? AND qhash=?",
                            (qid, qhash)).fetchone()
        return json.loads(r["answer_json"]) if r else None

    def jev_put(self, qid, qhash, question: str, answer: dict):
        self.db.execute("INSERT OR REPLACE INTO jev VALUES (?,?,?,?,?)",
                        (qid, qhash, question, json.dumps(answer), time.time()))
        self.db.commit()

    def set_status(self, qid, status: str, **fields):
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        unknown = set(fields) - set(STATUS_FIELDS)
        if unknown:
            raise ValueError(f"unknown fields {sorted(unknown)}")
        sets = ["status=?"] + [f"{k}=?" for k in fields]
        self.db.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE qid=?",
                        (status, *fields.values(), qid))
        self.db.commit()

    def by_status(self, status) -> list[Row]:
        return self._rows("status=?", (status,))

    def set_votes(self, qid, n: int):
        self.db.execute("UPDATE candidates SET votes=? WHERE qid=?", (n, qid))
        self.db.commit()

    def set_page(self, qid, title: str):
        self.db.execute("UPDATE candidates SET page=? WHERE qid=?", (title, qid))
        self.db.commit()

    def with_outcome(self, stage: int, outcome: str) -> list[Row]:
        return self._rows("EXISTS (SELECT 1 FROM outcomes o WHERE o.qid=candidates.qid"
                          " AND o.stage=? AND o.outcome=?)", (stage, outcome))

    def by_status_in(self, statuses) -> list[Row]:
        marks = ",".join("?" * len(statuses))
        return self._rows(f"status IN ({marks})", tuple(statuses))

    def page_owner(self, title: str) -> str | None:
        r = self.db.execute("SELECT qid FROM candidates WHERE page=?", (title,)).fetchone()
        return r["qid"] if r else None
