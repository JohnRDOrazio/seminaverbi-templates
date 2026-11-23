"""Music: the owned classes, Jev's wording and triage (stage 2).

Sources, tagged on each queue row: ``form`` (sacred forms), ``genre``, ``facet`` (subjects), ``artist`` (works of
the configured artists) and ``calibration``. Stage-0 data on a row: ``count`` (sitelinks), ``instance_of`` (P31
QIDs) and ``folded_into`` (the composition QID a part was folded into).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import common

# Musical work/composition, song, album, hymn, and the single (a release; Q134556, checked on Wikidata).
WORK_CLASSES: tuple[str, ...] = ("Q105543609", "Q7366", "Q482994", "Q484692", "Q134556")
FOLD_WHOLES = ("Q105543609",)  # a part folds only into a composition...
FOLD_STOP = ("Q482994",)  # ...never into an album: a song stays a song

PERSON = "Q5"
GROUPS = {"Q215380", "Q2088357"}  # musical group, musical ensemble
RECORDINGS = {"Q3302947", "Q106805967", "Q6942562"}  # audio recording, performed music, musical performance

# Jev's wording for the quick judgment, the connection choice and its options (pipeline.judge asks with these).
# Verbatim: the cached answers are keyed by a hash of this text.
QUESTIONS = {
    "plausible": ("Does the musical work described in `text` plausibly show the influence of the Gospel, or a seed "
                  "of the Word (a moral or spiritual truth that the Gospel brings to fulfilment)? The work need not "
                  "be religious: an implicit seed in one song, verse or movement is enough."),
    "connection": ("`text` is a dossier on one musical work (a song, an album, or a composition with its "
                   "movements). How does the work relate to the Gospel? A work need not be religious to carry seeds "
                   "of the Word: a moral or spiritual truth that the Gospel brings to fulfilment (mercy, "
                   "forgiveness, sacrifice, conversion, hope...) is enough, even when it is implicit and found in a "
                   "single song, verse or movement. Judge the work, its words and its setting, not the artist's "
                   "reputation."),
    "connections": {
        "direct": "direct Christian or biblical influence",
        "seeds": "implicit seeds of the Word: a truth the Gospel fulfils, in the whole work or in one song, "
                 "verse or movement",
        "entertainment": "biblical or Christian imagery used for entertainment",
        "none": "no connection, not even an implicit one",
    },
}

covered = common.covered_with_parts  # stage 1 applies the parts rule


def owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool:
    """True when a P31 QID is one of the music classes or a subclass of one (the Catholic media hook)."""
    return common.owns(instance_of, WORK_CLASSES, sparql, cache)


def triage(row, cfg: dict) -> tuple[str, str]:
    """Stage 2: ('drop', reason) for a person or group, a recording or edition, a part folded into its whole, or a
    row under the sitelink floor (the artist floor when the artist is the only source). Calibration always passes."""
    if row.calibration:
        return "pass", "calibration"
    data = row.data.get(0, {})
    classes = set(data.get("instance_of", []))
    if PERSON in classes:
        return "drop", "a person, not a work"
    is_work = bool(classes & set(WORK_CLASSES))  # a song also tagged as a recording or group stays a work
    if not is_work and classes & GROUPS:
        return "drop", "a person, not a work"
    if not is_work and classes & RECORDINGS:
        return "drop", "a recording or edition, not a work of its own"
    if data.get("folded_into"):
        return "drop", f"folded into {data['folded_into']}"
    floor = cfg["artist_sitelink_floor"] if set(row.sources) == {"artist"} else cfg["sitelink_floor"]
    count = data.get("count", 0)
    if count < floor:
        return "drop", f"{count} sitelinks, under the floor of {floor}"
    return "pass", ""


def _skippable(row: dict) -> bool:
    """A person, or a group that is no work (a row of a work class is kept whatever else it is)."""
    classes = set(row.get("types", "").split())
    return PERSON in classes or (not classes & set(WORK_CLASSES) and bool(classes & GROUPS))


def _gather(sparql, pattern: str, label: str) -> list[dict]:
    """The rows of `pattern` bounded to each work class (its subclasses a VALUES list from the closure), one
    row per item; people and groups are reported and left out."""
    found: dict[str, dict] = {}
    for cls in WORK_CLASSES:
        query = common.bounded(pattern, cls, common.class_closure(sparql, [cls]))
        for row in common.run_query(sparql, "music", f"{label} ({cls})", query):
            if _skippable(row):
                print(f"music candidates: {row['item']} is a person or group, skipped")
            else:
                found.setdefault(row["item"], row)
    return list(found.values())


def candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int:
    """Stage 0: add the form, genre, facet and seed-artist candidates to the queue; returns the rows added.
    Parts fold into their composition (never a song into its album); `wikipedia` and `wikidata` are unused."""
    music = cfg["music"]
    patterns: list[tuple[str, str, str]] = []
    forms = common.values(sorted(common.class_closure(sparql, music["forms"])))
    patterns.append(("form", "forms", f"  VALUES ?f {{ {forms} }}\n  ?item wdt:P7937|wdt:P136|wdt:P31 ?f ."))
    patterns.append(("genre", "genres",
                     f"  VALUES ?genre {{ {common.values(music['genres'])} }}\n  ?item wdt:P136 ?genre ."))
    patterns += [("facet", f"facet {d}", p) for d, p in common.facet_patterns(music) if d != "genres"]
    artists = [a["qid"] for a in music["artists"]]
    for i in range(0, len(artists), common.CHUNK):
        chunk = common.values(artists[i:i + common.CHUNK])
        patterns.append(("artist", f"artists {i // common.CHUNK + 1}",
                         f"  VALUES ?a {{ {chunk} }}\n  ?item wdt:P175|wdt:P86|wdt:P676 ?a ."))
    added = 0
    for source, label, pattern in patterns:
        rows = _gather(sparql, pattern, label)
        added += common.fold_parts(queue, sparql, rows, source, "music", FOLD_WHOLES, FOLD_STOP)
    return added
