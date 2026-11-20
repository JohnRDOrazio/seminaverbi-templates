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

from pipeline.sources import Wikipedia

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


def _run(sparql, label: str, query: str) -> list[dict]:
    return common.run_query(sparql, "literature", label, query)


def _add_rows(queue, rows: list[dict], source: str, series: bool = False) -> int:
    """Add the rows under `source`; `series`: they came from a query bounded to a series class (P31 may hold only
    a subclass of it), kept as data[0]["series"]. Never written False, so a work query on the same item does not
    clear it. Every source goes through here."""
    added = 0
    for row in rows:
        labels, sitelinks, data = common.row_data(row)
        if series:
            data["series"] = True
        added += queue.add(row["item"], "literature", source, labels, sitelinks, False, data)
    return added


def _bounded_rows(queue, sparql, pattern: str, label: str, source: str, classes: list[str]) -> tuple[int, int]:
    """Run `pattern` bounded to each class and add the rows: (rows added to the queue, rows found)."""
    added = found = 0
    for cls in classes:
        rows = _run(sparql, f"{label} ({cls})", common.bounded(pattern, cls))
        found += len(rows)
        added += _add_rows(queue, rows, source, series=cls != WORK)
    return added, found


def _works_of(queue, sparql, qids: list[str], source: str, label: str, classes: list[str]) -> int:
    """Works (P50) of the authors `qids`, one chunk of authors at a time."""
    added = 0
    for i in range(0, len(qids), CHUNK):
        pattern = f"  VALUES ?author {{ {common.values(qids[i:i + CHUNK])} }}\n  ?item wdt:P50 ?author ."
        added += _bounded_rows(queue, sparql, pattern, label, source, classes)[0]
    return added


def _direct_pattern(qids: list[str]) -> str:
    """The entries themselves, or the work of each one that is a fictional character (P1441, present in work)."""
    return (f"  VALUES ?entry {{ {common.values(qids)} }}\n"
            "  { ?entry wdt:P1441 ?item } UNION { BIND(?entry AS ?item) }")


def _direct(queue, sparql, qids: list[str], source: str, label: str, classes: list[str]) -> int:
    added = 0
    for i in range(0, len(qids), CHUNK):
        added += _bounded_rows(queue, sparql, _direct_pattern(qids[i:i + CHUNK]), label, source, classes)[0]
    return added


def _illustrissimi(queue, sparql, cfg: dict, wikidata, classes: list[str]) -> int:
    """The works of the people and characters the Illustrissimi letters are addressed to. One entry at a time,
    so that an addressee yielding nothing can be reported; the direct query runs only when the addressee has no
    work by P50, to spare the endpoint (40 addressees)."""
    lit = cfg["literature"]
    wikipedia = cfg.get("_it_wikipedia") or Wikipedia("it")
    titles = wikipedia.section_links(lit["illustrissimi_page"], lit["illustrissimi_section"])
    resolved = wikidata.by_sitelink("itwiki", titles)
    added = 0
    for title in titles:
        qid = resolved.get(title)
        if not qid:
            print(f"illustrissimi: no Wikidata item for {title!r}")
            continue
        pattern = f"  VALUES ?author {{ wd:{qid} }}\n  ?item wdt:P50 ?author ."
        n, found = _bounded_rows(queue, sparql, pattern, f"illustrissimi {title}", "illustrissimi:author", classes)
        if not found:
            n, found = _bounded_rows(queue, sparql, _direct_pattern([qid]), f"illustrissimi {title}",
                                     "illustrissimi", classes)
        if not found:
            print(f"illustrissimi: no literary work for {title!r}")
        added += n
    return added


def candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int:
    """Stage 0: add the seed-author, Illustrissimi, Francis-letter and facet candidates; returns the rows added.
    `wikipedia` (en) is unused here: the Illustrissimi page is read from it.wikipedia (cfg["_it_wikipedia"] in
    tests)."""
    wikidata = wikidata or WikidataClient()
    lit = cfg["literature"]
    classes = [WORK, *lit["series_classes"]]
    added = _works_of(queue, sparql, [a["qid"] for a in lit["authors"]], "author", "authors", classes)
    added += _illustrissimi(queue, sparql, cfg, wikidata, classes)
    francis = lit["francis_2024"]
    added += _works_of(queue, sparql, [e["qid"] for e in francis if e["kind"] == "author"], "francis:author",
                       "francis authors", classes)
    added += _direct(queue, sparql, [e["qid"] for e in francis if e["kind"] == "work"], "francis",
                     "francis works", classes)
    for description, pattern in common.facet_patterns(lit):
        added += _bounded_rows(queue, sparql, pattern, f"facet {description}", "facet", classes)[0]
    return added


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
