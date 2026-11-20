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
    status TEXT, page TEXT, reason TEXT, added_at REAL NOT NULL);
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


class Queue:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def add(self, qid, domain, source, labels, sitelinks, calibration=False) -> bool:
        row = self.db.execute("SELECT sources FROM candidates WHERE qid=?", (qid,)).fetchone()
        if row:
            sources = json.loads(row["sources"])
            if source not in sources:
                sources.append(source)
                self.db.execute("UPDATE candidates SET sources=? WHERE qid=?",
                                (json.dumps(sources), qid))
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
                      status=c["status"], page=c["page"])
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
