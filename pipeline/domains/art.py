"""Art: the owned classes, Jev's significance wording and triage (stage 2).

Sources, tagged on each queue row: ``depicts`` (what the work shows, scenes included), ``iconclass``, ``genre``, ``list`` and ``calibration``.
Stage-0 data on a row: ``count`` (sitelinks), ``instance_of`` (P31 QIDs) and ``folded_into``.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import common

# Painting, sculpture, icon, altarpiece, reredos, retable, stained-glass window, mural (the brief), then mosaic,
# tapestry and the liturgical goldsmith's works: monstrance, chalice, reliquary, ciborium, paten (checked on
# Wikidata).
WORK_CLASSES: tuple[str, ...] = ("Q3305213", "Q860861", "Q132137", "Q15711026", "Q46686", "Q3714446",
                                 "Q21061279", "Q219423", "Q133067", "Q184296", "Q335661", "Q210723", "Q722604",
                                 "Q198373", "Q1044124")
STOP_CLASSES: tuple[str, ...] = ("Q41176", "Q16970")  # building, church building: folding never goes into them

PERSON = "Q5"
COPIES = {"Q1784021"}  # artwork copy (a faithful reproduction of an artwork)

# Jev's wording. For art the quick question asks about significance, not plausibility; the key stays `plausible`
# so that pipeline.judge and stage 3 need no change. Verbatim: cached answers are keyed by a hash of this text.
QUESTIONS = {
    "plausible": ("Does the artwork described in `text` shape how the scene it depicts, or the Christian faith, is "
                  "imagined or understood, or carry a theological reading discussed in its sources, beyond "
                  "depicting a religious subject? A famous work that set how a scene is pictured, or a secular "
                  "work with an implicit seed of the Word, counts; one devotional image among many of the same "
                  "subject does not."),
    "connection": ("`text` is a dossier on one artwork (possibly an altarpiece or cycle with its panels or "
                   "scenes). How does the work relate to the Gospel? A work need not be religious to carry seeds "
                   "of the Word: a moral or spiritual truth that the Gospel brings to fulfilment (mercy, "
                   "forgiveness, sacrifice, conversion, hope...) is enough, even when it is implicit. Judge what "
                   "the work shows and how its sources read it; do not rest the judgment on Gnostic or esoteric "
                   "readings."),
    "connections": {
        "direct": "direct Christian or biblical influence",
        "seeds": "implicit seeds of the Word: a truth the Gospel fulfils, in the whole work or in one panel or "
                 "scene",
        "entertainment": "Christian or biblical imagery used for decoration, satire or shock, without engaging "
                         "its meaning",
        "none": "no connection, not even an implicit one",
    },
}

covered = common.covered_with_parts  # stage 1 applies the parts rule


def owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool:
    """True when a P31 QID is one of the art classes or a subclass of one (the Catholic media hook)."""
    return common.owns(instance_of, WORK_CLASSES, sparql, cache)


def triage(row, cfg: dict) -> tuple[str, str]:
    """Stage 2: ('drop', reason) for a person, a copy or study (unless the row is also of a work class), a part
    folded into its whole, or a row under the sitelink floor (a `list` row passes without a floor).
    Calibration always passes."""
    if row.calibration:
        return "pass", "calibration"
    data = row.data.get(0, {})
    classes = set(data.get("instance_of", []))
    if PERSON in classes:
        return "drop", "a person, not a work"
    if not classes & set(WORK_CLASSES) and classes & COPIES:
        return "drop", "a copy or study, not a work of its own"
    if data.get("folded_into"):
        return "drop", f"folded into {data['folded_into']}"
    if "list" in row.sources:
        return "pass", ""
    count = data.get("count", 0)
    if count < cfg["sitelink_floor"]:
        return "drop", f"{count} sitelinks, under the floor of {cfg['sitelink_floor']}"
    return "pass", ""


def _gather(sparql, pattern: str, label: str) -> list[dict]:
    """The rows of `pattern` bounded to each work class (its subclasses a VALUES list from the closure), one
    row per item; people are reported and left out."""
    found: dict[str, dict] = {}
    for cls in WORK_CLASSES:
        query = common.bounded(pattern, cls, common.class_closure(sparql, [cls]))
        for row in common.run_query(sparql, "art", f"{label} ({cls})", query):
            if PERSON in row.get("types", "").split():
                print(f"art candidates: {row['item']} is a person, skipped")
            else:
                found.setdefault(row["item"], row)
    return list(found.values())


def _listed(sparql, qids: list[str]) -> list[dict]:
    """The curated works by QID: one query per chunk, no class filter (a listed work need not be of a work
    class), the query limit kept; people are reported and left out."""
    found: list[dict] = []
    for i in range(0, len(qids), common.CHUNK):
        pattern = f"  VALUES ?item {{ {common.values(qids[i:i + common.CHUNK])} }}"
        for row in common.run_query(sparql, "art", f"listed works {i // common.CHUNK + 1}", common.query(pattern)):
            if PERSON in row.get("types", "").split():
                print(f"art candidates: {row['item']} is a person, skipped")
            else:
                found.append(row)
    return found


def candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int:
    """Stage 0: add the depicts, Iconclass, genre and curated-list candidates to the queue; returns the rows
    added. Panels and scenes fold into their altarpiece or cycle (never into a building); `wikipedia` and
    `wikidata` are unused."""
    art = cfg["art"]
    shown = common.values([*art["depicts"], *art["scene_classes"]])  # scenes are events: depicted, not P31
    link = "?item wdt:P180 ?d ."
    patterns: list[tuple[str, str, str]] = [
        ("depicts", "depicts", f"  VALUES ?d {{ {shown} }}\n  {link}"),
        ("depicts", "depicts saints", f"  {link}\n  ?d wdt:{common.SAINT_PROPERTY} [] ."),
        ("depicts", "depicts biblical characters",
         f"  VALUES ?class {{ {common.values(art['biblical_classes'])} }}\n  ?d wdt:P31 ?class .\n  {link}"),
    ]
    prefixes = " || ".join(f'STRSTARTS(STR(?ic), "{p}")' for p in art["iconclass_prefixes"])
    patterns.append(("iconclass", "Iconclass", f"  ?item wdt:P1257 ?ic .\n  FILTER({prefixes})"))
    patterns.append(("genre", "genres",
                     f"  VALUES ?genre {{ {common.values(art['genres'])} }}\n  ?item wdt:P136 ?genre ."))
    added = 0
    for source, label, pattern in patterns:
        rows = _gather(sparql, pattern, label)
        added += common.fold_parts(queue, sparql, rows, source, "art", WORK_CLASSES, STOP_CLASSES)
    listed = _listed(sparql, [w["qid"] for w in art["works"]])
    return added + common.fold_parts(queue, sparql, listed, "list", "art", WORK_CLASSES, STOP_CLASSES)
