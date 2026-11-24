"""Sacred architecture: the owned classes, Jev's significance wording, triage (stage 2) and candidates (stage 0).

Sources, tagged on each queue row: ``building`` (the work classes), ``unesco`` (a World Heritage designation, P1435,
no sitelink floor), ``list`` (the curated works) and ``calibration``. Stage-0 data on a row: ``count`` (sitelinks) and
``instance_of`` (P31 QIDs).

The queries on live WDQS, 2026-11-24, floor 15, one attempt each, 6 s apart: rows and seconds (none timed out or reached
common.LIMIT; the endpoint's times vary, the same query took from 12 to 43 s):

    building  church building       15-19 sitelinks 243, 20.6 s    20 and more 268, 42.3 s
    building  its subclasses        15-19 sitelinks 156,  5.1 s    20 and more 188, 13.2 s
    building  cathedral             15-19 sitelinks 258,  5.0 s    20 and more 359, 43.8 s
    building  basilica 275, 10.7 s    chapel 51, 7.6 s    abbey 80, 19.2 s    monastery 220, 17.2 s
    unesco    church building       1-4 sitelinks 185, 1.8 s       5 and more 299, 7.6 s
    unesco    cathedral             1-4 sitelinks 1, 1.9 s         5 and more 84, 4.5 s
    unesco    basilica 113, 10.5 s    chapel 29, 5.5 s    abbey 18, 3.0 s    monastery 74, 7.2 s
    list      1, 1.2 s

1,538 buildings and 655 UNESCO ones, 436 of them not among the first. Church building asked in one query with its
subclasses timed out, and a FILTER EXISTS on the statements, in place of the OPTIONAL, was slower (chapel: 48 s).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from . import benchmark as benchmark_code
from . import common
from .benchmark import PERSON

# Church building, cathedral, basilica, chapel, abbey, monastery.
WORK_CLASSES: tuple[str, ...] = ("Q16970", "Q2977", "Q163687", "Q108325", "Q160742", "Q44613")
CHURCH = "Q16970"
CHRISTIAN_BY_CLASS: tuple[str, ...] = (CHURCH, "Q2977")  # Christian by definition: no statement is asked of them

# config/pipeline.json, `architecture.christian_religions`: the P140 values of the items of the work classes (their
# closures, WDQS, 2026-11-24) that are Christianity or a Christian confession, rite or order. The query, once per
# class:
#   SELECT ?r (SAMPLE(?lab) AS ?l) (COUNT(DISTINCT ?item) AS ?n) WHERE { hint:Query hint:optimizer "None" .
#     VALUES ?cls { <the class closure> } ?item wdt:P31 ?cls . ?item wikibase:sitelinks ?s . FILTER(?s >= 15)
#     ?item wdt:P140 ?r . OPTIONAL { ?r rdfs:label ?lab FILTER(LANG(?lab) = "en") } } GROUP BY ?r
# Five came from the same query without the floor, on the items with a P1435 of `unesco` (the UNESCO
# buildings below the floor have them): Q856349, Q844083, Q329344, Q894479, Q106687. Kept:
# Q5043 Christianity, Q1841 Catholicism, Q9592 Catholic Church, Q7361618 Roman Catholic, Q597526 Latin Church, Q617075
# Byzantine Rite, Q472189 Ukrainian Greek Catholic Church, Q64885 Melkite Greek Catholic Church, Q64842 Armenian
# Catholic Church, Q64900 Maronite Church, Q657390 Ruthenian Greek Catholic Church, Q64868 Chaldean Catholic Church,
# Q755970 Syriac Catholic Church, Q856349 Romanian Greek Catholic Church, Q3333484 Eastern Orthodoxy, Q35032 Eastern
# Orthodox Church, Q383258 orthodoxy, Q7970362 Greek Orthodoxy, Q211004 Ecumenical Patriarchate of Constantinople,
# Q632602 Greek Orthodox Patriarchate of Jerusalem, Q192173 Georgian Orthodox Church, Q188814 Serbian Orthodox Church,
# Q60995 Russian Orthodox Church, Q181901 Romanian Orthodox Church, Q242758 Bulgarian Orthodox Church, Q906685
# Macedonian Orthodox Church, Q752474 Ukrainian Orthodox Church (Moscow Patriarchate), Q59493657 Orthodox Church of
# Ukraine, Q463406 Polish Orthodox Church, Q598783 Belarusian Orthodox Church, Q248559 Moldovan Orthodox Church,
# Q59778 Church of Greece, Q59776 Church of Cyprus, Q475683 Orthodox Church of the Czech Lands and Slovakia, Q255670
# Orthodox Church in Japan (Moscow Patriarchate), Q683724 Armenian Apostolic Church, Q198998 Coptic Orthodox Church,
# Q179829 Ethiopian Orthodox Tewahedo Church, Q844083 Eritrean Orthodox Tewahedo Church, Q210540 Syriac Orthodox
# Church, Q23540 Protestantism, Q75809 Lutheranism, Q6423963 Anglicanism, Q82708 Church of England, Q682443 Episcopal
# Church, Q922480 Church of Scotland, Q170111 Protestant Church in Germany, Q666860 Church of Norway, Q749243 Church
# of Sweden, Q855585 Church of Denmark, Q1379849 Evangelical Lutheran Church of Finland, Q1413245 Church of Iceland,
# Q1379796 Evangelical Church of the Augsburg Confession in Poland, Q329344 Evangelical Church of Augustan Confession
# in Romania, Q391951 Protestant Church in the Netherlands, Q338309 Protestant Church of Switzerland, Q426316 United
# Church of Christ, Q463455 American Baptist Churches USA, Q194253 Evangelicalism, Q49830 Continental Reformed
# Protestantism, Q101849 Reformed Christianity, Q966577 united and uniting churches, Q2060869 Czechoslovak Hussite
# Church, Q894479 Bosnian Church, Q42504 The Church of Jesus Christ of Latter-day Saints, Q106687 Unitarianism.
# Q383258 (orthodoxy) is the concept, found only on Uspenski Cathedral, for Eastern Orthodoxy. Q42504 and Q106687 are
# kept as confessions that call themselves Christian. Left out as other religions: Q432 Islam, Q483654 Sunni Islam,
# Q9268 Judaism, Q748 Buddhism, Q319218 Gelug, Q848771 Nyingma, Q784270 Drukpa Lineage, Q650035 Sakya, Q132265
# Theravāda, Q1571776 Rimé movement, Q471187 Kagyu, Q483889 Tibetan Buddhism, Q4098001 Buddhist Traditional Sangha of
# Russia, Q588362 Kadam, Q940709 Chan Buddhism, and Q1622507 Unitarian Universalism, which is not a Christian
# confession. Left out as no religion at all: Q46825 Gothic art and Q66085482, a web page (the one cathedral with it
# has Q1841 too).

# Churches and cathedrals pass common.LIMIT in one query, or come near it: each source asks them in two, below and from
# this many sitelinks.
BANDS = {"building": 20, "unesco": 5}

# Jev's wording (significance, under the key `plausible`, as art's). Verbatim: cached answers are keyed by a hash of
# this text.
QUESTIONS = {
    "plausible": ("Does the building described in `text` shape how the Christian faith is lived or understood "
                  "(through its liturgy, as a place of pilgrimage, or through a theology expressed in its forms), or "
                  "express the beauty of the faith through the richness and quality of its art and symbolism, beyond "
                  "being a well-known religious building? A building that set how a church is imagined, or whose art "
                  "and symbolism carry a theological reading discussed in its sources, counts; an ordinary parish "
                  "church, however old, does not."),
    "connection": ("`text` is a dossier on one building. How does the building relate to the Gospel? Judge what its "
                   "forms, its art and its use express and how its sources read them, not that it is or was a place "
                   "of worship; do not rest the judgment on Gnostic or esoteric readings."),
    "connections": {
        "direct": "direct Christian or biblical expression in its forms, art, symbolism or use",
        "seeds": "implicit seeds of the Word: a truth the Gospel fulfils, in the building or in a part of it",
        "entertainment": "Christian forms used for decoration, spectacle or tourism, without engaging their meaning",
        "none": "no connection beyond its being, or having been, a religious building",
    },
}


def press_stage0(qid: str, entity: dict, sparql) -> dict:
    """No stage-0 data: a building has no parts to fold."""
    return {}


def owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool:
    """True when a P31 QID is one of the architecture classes or a subclass of one (the Catholic media hook)."""
    return common.owns(instance_of, WORK_CLASSES, sparql, cache)


def triage(row, cfg: dict) -> tuple[str, str]:
    """Stage 2: ('drop', reason) for a person, or a row under the sitelink floor (a `unesco` or `list` row, and one
    with a kept press mention, pass without a floor). Calibration always passes."""
    if row.calibration:
        return "pass", "calibration"
    data = row.data.get(0, {})
    if PERSON in data.get("instance_of", []):
        return "drop", "a person, not a work"
    if {"unesco", "list"} & set(row.sources) or common.press_exempt(row):
        return "pass", ""
    count = data.get("count", 0)
    if count < cfg["sitelink_floor"]:
        return "drop", f"{count} sitelinks, under the floor of {cfg['sitelink_floor']}"
    return "pass", ""


def _branches(sparql) -> dict[str, set[str]]:
    """Work class -> its closure, without the closure of any work class nested in it. On Wikidata chapel, basilica
    and cathedral are subclasses of church building, and abbey of monastery: each is asked by its own query, so that
    a chapel does not enter as a church, with no proof that it is Christian."""
    closures = {cls: common.class_closure(sparql, [cls]) for cls in WORK_CLASSES}
    return {cls: closure.difference(*(closures[c] for c in WORK_CLASSES if c != cls and c in closure))
            for cls, closure in closures.items()}


def _christian(cls: str, religions: Iterable[str]) -> str:
    """The Christian test of an item of `cls`: no P140, or one of `religions` among its P140 values (a converted
    church may have two); outside CHRISTIAN_BY_CLASS an item with no P140 must have a dedication (P825)."""
    test = ("  OPTIONAL { ?item wdt:P140 ?religion }\n"
            f"  FILTER(!BOUND(?religion) || ?religion IN ({common.one_of(religions)}))\n")
    return test if cls in CHRISTIAN_BY_CLASS else test + "  FILTER(BOUND(?religion) || EXISTS { ?item wdt:P825 [] })\n"


def _bands(source: str, cls: str, floor: int) -> list[tuple[int, str]]:
    """The (floor, test on ?sitelinks) of each query of a source and class."""
    split = BANDS[source]
    if cls not in CHRISTIAN_BY_CLASS or floor >= split:
        return [(floor, "")]
    return [(floor, f"  FILTER(?sitelinks < {split})\n"), (split, "")]


def _patterns(source: str, cls: str, branch: set[str], arch: dict) -> list[tuple[str, str, set[str]]]:
    """The (label, pattern, classes) a source asks of a work class. `unesco` starts from the designation; `building`
    has nothing narrower to start from than the classes, and asks church building apart from its subclasses
    (together they timed out)."""
    if source == "unesco":
        heritage = f"  VALUES ?heritage {{ {common.values(arch['unesco'])} }}\n  ?item wdt:P1435 ?heritage ."
        return [(cls, heritage, branch)]
    groups = [(cls, {cls}), (f"{cls} subclasses", branch - {cls})] if cls == CHURCH else [(cls, branch)]
    return [(label, f"  VALUES ?kind {{ {common.values(sorted(kinds))} }}\n  ?item wdt:P31 ?kind .", kinds)
            for label, kinds in groups if kinds]


def _queries(sparql, arch: dict, floor: int) -> Iterator[tuple[str, str, str]]:
    """(source, label, query) of the class-bounded sources."""
    branches = _branches(sparql)
    for source, lowest in (("building", floor), ("unesco", 1)):
        for cls, branch in branches.items():
            test = _christian(cls, arch["christian_religions"])
            for label, pattern, kinds in _patterns(source, cls, branch, arch):
                for low, band in _bands(source, cls, lowest):
                    yield source, f"{source} from {low} sitelinks ({label})", common.bounded(
                        pattern, cls, kinds, low, band + test)


def _add(queue, row: dict, source: str) -> int:
    if common.other_domain(queue, row["item"], "architecture"):
        return 0
    labels, sitelinks, data = common.row_data(row)
    return queue.add(row["item"], "architecture", source, labels, sitelinks, False, data)


def candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int:
    """Stage 0: add the buildings of the work classes, those with a UNESCO designation and the curated `works` (which
    need no class, religion or floor) to the queue; returns the rows added. `wikipedia` and `wikidata` are unused."""
    arch = cfg["architecture"]
    floor = arch.get("sitelink_floor", cfg["sitelink_floor"])
    added = 0
    try:
        for source, label, query in _queries(sparql, arch, floor):
            for row in common.run_query(sparql, "architecture", label, query):
                if PERSON in row.get("types", "").split():
                    print(f"architecture candidates: {row['item']} is a person, skipped")
                else:
                    added += _add(queue, row, source)
        for row in benchmark_code.listed(sparql, "architecture", [w["qid"] for w in arch["works"]]):
            added += _add(queue, row, "list")
    finally:  # a failed source keeps the rows already written: the benchmark works among them are held all the same
        try:
            benchmark_code.hold(queue, "architecture", arch.get("benchmark", []))
        finally:  # a floor drop that gained `unesco` is triaged again, whatever failed before
            common.retriage(queue, "architecture", triage, common.domain_config(cfg, "architecture"))
    return added
