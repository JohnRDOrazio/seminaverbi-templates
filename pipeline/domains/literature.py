"""Literature: candidate finding (stage 0), triage (stage 2) and the dossier (stage 4).

Sources, tagged on each queue row: ``author`` (works of the configured authors), ``facet`` (works about Jesus,
the Bible, a saint or a biblical character, or in a Christian genre), ``illustrissimi`` and
``illustrissimi:author`` (the works the Illustrissimi letters name, and other works of their authors),
``francis`` (the works Pope Francis's 2024 letter names) and ``calibration`` (the works and series
SeminaVerbi already has articles on).

Stage-0 data on a row: ``count`` (sitelinks), ``instance_of`` (P31 QIDs), ``series`` (bool), ``volumes``
(``{volume_qid: {"sitelinks": {...}, "count": int}}``), ``lead_sitelinks`` and ``folded_into`` (series QID).
Only the stub of stages 0 and 4 exists so far.
"""
from __future__ import annotations

from pathlib import Path

from sync import categories
from sync.wikidata import WikidataClient

from . import common
from .common import CHUNK, LIMIT  # noqa: F401

WORK, EDITION = "Q7725634", "Q3331189"  # literary work; version, edition or translation
DIRECT = {"illustrissimi", "francis"}  # sources that name a work directly: no sitelink floor

# Jev's wording for the quick judgment, the connection choice and its options (pipeline.judge asks with these).
# Verbatim: the cached answers are keyed by a hash of this text.
QUESTIONS = {
    "plausible": ("Does the literary work described in `text` plausibly show the influence of the Gospel, or a seed "
                  "of the Word (a moral or spiritual truth that the Gospel brings to fulfilment)? The work need not "
                  "be religious: an implicit seed in one storyline, character, poem or volume is enough."),
    "connection": ("`text` is a dossier on one literary work (for a series, possibly several volumes). How does the "
                   "work relate to the Gospel? A work need not be religious to carry seeds of the Word: a moral or "
                   "spiritual truth that the Gospel brings to fulfilment (mercy, forgiveness, sacrifice, conversion, "
                   "hope...) is enough, even when it is implicit and found in a single storyline, character, poem or "
                   "volume. Judge the text itself, as its scenes show it, not the work's reputation."),
    "connections": {
        "direct": "direct Christian or biblical influence",
        "seeds": "implicit seeds of the Word: a truth the Gospel fulfils, in the whole work or in one storyline, "
                 "character, poem or volume",
        "entertainment": "biblical or Christian imagery used for entertainment",
        "none": "no connection, not even an implicit one",
    },
}


def candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int:
    raise NotImplementedError("Task 4")


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    raise NotImplementedError("Task 5")


def dossier_stage(row, cfg: dict, wiki_for, wikidata) -> tuple[str, dict]:
    raise NotImplementedError("Task 5")


def _series_classes() -> list[str]:
    from pipeline import load_config  # read when needed: add_calibration has no cfg argument
    return load_config()["literature"]["series_classes"]


def add_calibration(queue, sparql, qids: list[str]) -> int:
    """Add the works and series SeminaVerbi already covers, flagged as calibration; returns the rows added.
    A QID that is neither a work nor a series (a person, a place) is reported and skipped."""
    classes = [WORK, *_series_classes()]
    found: set[str] = set()
    added = 0
    for i in range(0, len(qids), CHUNK):
        values = f"  VALUES ?item {{ {common.values(qids[i:i + CHUNK])} }}\n"
        for cls in classes:
            rows = common.run_query(sparql, "literature", f"calibration ({cls})",
                                    common.query(values + f"  ?item wdt:P31/wdt:P279* wd:{cls} ."), limit=LIMIT)
            for row in rows:
                labels, sitelinks, data = common.row_data(row)
                if cls != WORK:
                    data["series"] = True  # never written False: a work query on the same item must not clear it
                found.add(row["item"])
                added += queue.add(row["item"], "literature", "calibration", labels, sitelinks, True, data)
    for qid in qids:
        if qid not in found:
            print(f"literature calibration: {qid} skipped (not a work)")
    return added


def calibration_qids(wikis: dict, wikidata=None, tree: dict | None = None) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the literature categories of every wiki, and the 'lang:Title' of those
    with no local item or no Wikipedia sitelink (reported, never guessed)."""
    wikidata = wikidata or WikidataClient()
    tree = tree or categories.load(Path(__file__).resolve().parents[2])
    return common.calibration_qids(wikis, wikidata, tree, {"literature"})


def triage(row, cfg: dict) -> tuple[str, str]:
    """Stage 2: ('drop', reason) for a volume folded into its series, an edition, or a row under the sitelink
    floor unless a source names the work directly. A calibration row always passes."""
    if row.calibration:
        return "pass", "calibration"
    data = row.data.get(0, {})
    if data.get("folded_into"):
        return "drop", f"folded into {data['folded_into']}"
    if EDITION in data.get("instance_of", []):
        return "drop", "an edition or translation, not a work of its own"
    if not set(row.sources) & DIRECT:
        count, floor = data.get("count", 0), cfg["sitelink_floor"]
        if count < floor:
            return "drop", f"{count} sitelinks, under the floor of {floor}"
    return "pass", ""
