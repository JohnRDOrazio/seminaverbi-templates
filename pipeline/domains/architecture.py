"""Sacred architecture: the owned classes, Jev's significance wording, triage (stage 2), candidates (stage 0), the
dossier (stage 4) and calibration.

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
    list      1, 1.2 s (the Sagrada Família alone)

1,538 buildings and 655 UNESCO ones, 436 of them not among the first. Church building asked in one query with its
subclasses timed out, and a FILTER EXISTS on the statements, in place of the OPTIONAL, was slower (chapel: 48 s).
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from pipeline import load_config
from sync import categories
from sync.wikidata import WikidataClient

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
    a chapel does not enter as a church, with no proof that it is Christian. Reported on stderr, when it stops being so:
    a work class inside another one nested in it (a P279 cycle), which no query asks any more, and a branch too large
    for a list of classes, whose query walks the subclasses of the nested ones too."""
    closures = {cls: common.class_closure(sparql, [cls]) for cls in WORK_CLASSES}
    branches = {cls: closure.difference(*(closures[c] for c in WORK_CLASSES if c != cls and c in closure))
                for cls, closure in closures.items()}
    for cls, branch in branches.items():
        if cls not in branch:
            print(f"architecture candidates: WARNING: work class {cls} is left out (a P279 cycle with another work "
                  "class)", file=sys.stderr)
        elif len(branch) > common.VALUES_MAX:
            print(f"architecture candidates: WARNING: work class {cls} has {len(branch)} classes, over "
                  f"{common.VALUES_MAX}: the work classes nested in it are not left out of its queries",
                  file=sys.stderr)
    return branches


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
    """Stage 0: add the curated `works` (which need no class, religion or floor), the buildings of the work classes and
    those with a UNESCO designation to the queue; returns the rows added. The rows are judged in the order they are
    added: the works, as the config lists them, then the others by sitelinks, most first, whatever query found them. A
    later run adds its new rows after those queued, in the same order. `wikipedia` and `wikidata` are unused."""
    arch = cfg["architecture"]
    floor = arch.get("sitelink_floor", cfg["sitelink_floor"])
    works = [w["qid"] for w in arch["works"]]
    found: list[tuple[str, dict]] = []  # (source, row)
    added: list[int] = []
    try:
        for row in benchmark_code.iter_listed(sparql, "architecture", works):
            found.append(("list", row))
        for source, label, query in _queries(sparql, arch, floor):
            for row in common.run_query(sparql, "architecture", label, query):
                if PERSON in row.get("types", "").split():
                    print(f"architecture candidates: {row['item']} is a person, skipped")
                else:
                    found.append((source, row))
    except BaseException:  # a failed source keeps the rows already found; its error is the one that propagates
        _finish(queue, cfg, found, works, added, in_flight=True)
        raise
    _finish(queue, cfg, found, works, added, in_flight=False)
    return sum(added)


def _in_order(found: list[tuple[str, dict]], works: list[str]) -> list[tuple[str, dict]]:
    """The works in their own order, then the other rows by sitelinks, most first (ties by QID); the rows of an item
    several sources found stay together, in the order the sources were asked."""
    place = {qid: n for n, qid in reversed(list(enumerate(works)))}
    count: dict[str, int] = {}
    for _, row in found:
        count[row["item"]] = max(count.get(row["item"], 0), common.row_data(row)[2]["count"])
    return sorted(found, key=lambda f: (place.get(f[1]["item"], len(works)), -count[f[1]["item"]], f[1]["item"]))


def _finish(queue, cfg: dict, found: list[tuple[str, dict]], works: list[str], added: list[int],
            in_flight: bool) -> None:
    """Add the rows found (how many were new goes to `added`), hold the benchmark works, then triage again the floor
    drops that gained `unesco`; all are always attempted. With an error in flight a failure here is printed and the
    original propagates; otherwise the first one raises."""
    steps = (lambda: added.extend(_add(queue, row, source) for source, row in _in_order(found, works)),
             lambda: hold_benchmark(queue, cfg),
             lambda: common.retriage(queue, "architecture", triage, common.domain_config(cfg, "architecture")))
    first = None
    for step in steps:
        try:
            step()
        except Exception as error:  # noqa: BLE001
            if in_flight or first is not None:
                print(f"architecture candidates: {error}")
            first = first or error
    if first is not None and not in_flight:
        raise first


# ---- stage 4: the dossier ----

# lower-case fragments of the section headings worth reading, per language, in the order the judge should read them (it
# is sent at most stages.MAX_DOSSIER_CHARS of a dossier, from the start): art, iconography, symbolism and liturgy;
# architecture; history. A heading is of the first group it matches. A fragment is matched at a word start; one with a
# trailing blank is a whole word ("art " is not "articles", "bau " not "Bauernkrieg"). German compounds are listed by
# their first word, the only one a word start reaches.
GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "en": (
        ("decorat", "art ", "arts ", "artwork", "artistic", "iconograph", "symbol", "mosaic", "stained glass",
         "stained-glass", "sculpture", "liturg", "worship", "pilgrimage", "significance", "interpretation"),
        ("architectur", "descri", "design ", "designs ", "designed ", "plan ", "plans ", "exterior", "interior",
         "façade", "facade"),
        ("histor", "construction", "consecration", "dedication"),
    ),
    "it": (
        ("decorazion", "arte ", "arti ", "artist", "iconografi", "simbol", "mosaic", "vetrat", "scultur",
         "liturgi", "culto", "culti ", "pellegrinagg", "significat", "interpretazion"),
        ("architettur", "descri", "progett", "pianta", "piante ", "estern", "interno", "interni ", "interna ",
         "interne ", "facciat"),
        ("storia", "storic", "costruzion", "consacrazion", "dedicazion"),
    ),
    "es": (
        ("decoraci", "arte ", "artes ", "artístic", "iconograf", "simbol", "símbol", "mosaico", "vidriera",
         "vitral", "escultur", "liturgia", "litúrgic", "culto", "peregrinaci", "significado", "interpretaci"),
        ("arquitect", "descri", "diseño", "proyecto", "planta ", "plantas ", "exterior", "interior", "fachada"),
        ("historia", "históric", "construcci", "consagraci", "dedicaci"),
    ),
    "fr": (
        ("décor", "art ", "arts ", "artistique", "œuvre", "iconograph", "symbol", "mosaïque", "vitra",
         "sculptur", "liturgi", "culte", "pèlerinage", "signification", "interprétation"),
        ("architectur", "descri", "conception", "projet", "plan ", "plans ", "extérieur", "intérieur", "façade",
         "facade"),
        ("histoire", "histori", "construction", "consécration", "dédicace"),
    ),
    "de": (
        ("ausstattung", "innenausstattung", "ausmalung", "kunst", "ikonographi", "ikonografi", "symbol",
         "mosaik", "fenster", "kirchenfenster", "glasfenster", "glasmalerei", "buntglas", "wandmalerei",
         "deckenmalerei", "malerei", "fresken", "skulptur", "liturgi", "gottesdienst", "wallfahrt", "bedeutung",
         "deutung", "interpretation"),
        ("architektur", "beschreibung", "baubeschreibung", "bauform", "baugestalt", "baukunst", "baukörper",
         "bauplan", "baustil", "bauwerk", "entwurf", "entwürf", "plan ", "pläne", "planung", "grundriss",
         "äußere", "außenansicht", "außenbau", "außenfassade", "außengestalt", "inneres ", "innere ",
         "innenansicht", "innenarchitektur", "innenbau", "inneneinrichtung", "innenraum", "fassade"),
        ("geschichte", "bau ", "bauabschnitt", "bauarbeit", "baubeginn", "baugeschichte", "bauphase", "bauten",
         "bautätigkeit", "bauzeit", "kirchenbau", "neubau", "anbau", "aufbau", "ausbau", "umbau",
         "wiederaufbau", "entstehung", "weihe", "patrozinium"),
    ),
    "pt": (
        ("decoraç", "arte ", "artes ", "artístic", "iconograf", "simbol", "símbol", "mosaico", "vitra",
         "escultur", "liturgia", "litúrgic", "culto", "peregrinaç", "significado", "interpretaç"),
        ("arquitet", "arquitect", "descri", "projeto", "projecto", "planta ", "plantas ", "exterior",
         "interior", "fachada"),
        ("história", "históric", "construç", "consagraç", "dedicaç"),
    ),
}
HEADINGS = {lang: tuple(word for group in groups for word in group) for lang, groups in GROUPS.items()}
# The most characters read of a page's lead, and of one wanted heading with its subsections, by group: chosen on seven
# pages (2026-11-24: Chartres en, it and es, the Votivkirche de, San Vitale it, the Sainte-Chapelle fr, the Sagrada
# Família es), so that no history or façade fills what the judge is sent (the Sagrada Família's history alone is 22,350).
# A building rich in art in three languages (Chartres) still fills the first MAX_DOSSIER_CHARS with leads and art, for
# the connection and the field; the themes are asked of the rest too. The subsections share the
# cap, the first ones only when there are too many for about MIN_SHARE each.
LEAD_CAP = 2500
CAPS = (6000, 3000, 2000)
MIN_SHARE = 500
# subsections of a wanted heading that say nothing of what the building means: instruments and bells (technical
# specifications), measurements, visitor information and popular culture. A heading that is wanted is never stopped, nor
# one that names the part of the building an instrument is in, or its case (HOLDERS): a bell tower is architecture.
STOP: dict[str, tuple[str, ...]] = {
    "en": ("organ ", "organs ", "bell ", "bells ", "statistics", "dimensions", "tourism", "popular culture"),
    "it": ("organo ", "organi ", "campana ", "campane ", "dimensioni", "turismo", "cultura di massa"),
    "es": ("órgano ", "órganos ", "campana ", "campanas ", "dimensiones", "turismo", "cultura popular"),
    "fr": ("orgue ", "orgues ", "cloche ", "cloches ", "dimensions", "tourisme", "culture populaire"),
    "de": ("orgel ", "orgeln ", "chororgel ", "glocke ", "glocken ", "geläut", "maße ", "tourismus", "populärkultur"),
    "pt": ("órgão ", "órgãos ", "sino ", "sinos ", "dimensões", "turismo", "cultura popular"),
}
HOLDERS: dict[str, tuple[str, ...]] = {
    "en": ("tower", "belfry", "loft", "gallery", "case "),
    "it": ("torre ", "torri ", "cella ", "cantoria", "cassa "),
    "es": ("torre ", "torres ", "tribuna", "caja "),
    "fr": ("tour ", "tours ", "tribune", "buffet"),
    "de": ("turm ", "türme ", "empore"),
    "pt": ("torre ", "torres ", "tribuna", "caixa "),
}
# the headings of the link and reference sections, whole and lower-case: never read, though a fragment may match
LINK_SECTIONS = frozenset({
    "see also", "references", "further reading", "external links", "sister projects", "gallery",
    "note", "voci correlate", "bibliografia", "altri progetti", "collegamenti esterni",
    "véase también", "referencias", "bibliografía", "proyectos hermanos", "enlaces externos",
    "voir aussi", "notes et références", "bibliographie", "articles connexes", "article connexe", "autres projets",
    "liens externes", "siehe auch", "literatur", "einzelnachweise", "schwesterprojekte", "weblinks",
    "ver também", "referências", "outros projetos", "outros projectos", "projetos irmãos", "projectos irmãos",
    "ligações externas",
})
WIKIDATA_FACTS = (("P84", "architect"), ("P149", "style"), ("P140", "religion"), ("P825", "dedicated to"),
                  ("P131", "location"), ("P571", "date"), ("P1435", "heritage"))
INCEPTION = "P571"


def wikidata_note(entity: dict, labels_of: Callable[[str], str | None]) -> dict | None:
    """One note of what Wikidata records about the building: architect (P84), style (P149), religion (P140),
    dedication (P825), location (P131), the year of its inception (P571) and heritage designation (P1435). Names
    only. None when there is nothing to say, or for a person."""
    if PERSON in common.qids(entity, "P31"):
        return None
    facts = []
    for prop, role in WIKIDATA_FACTS:
        if prop != INCEPTION:
            facts += common.named_fact(entity, prop, role, labels_of)
        elif year := common.inception_year(entity):
            facts.append(f"{role}: {year}")
    return common.facts_note(entity, facts)


def _group(heading: str, lang: str) -> int | None:
    """The first group of GROUPS the heading matches; None for an unwanted one."""
    return next((n for n, words in enumerate(GROUPS[lang]) if common.wanted(heading, words)), None)


_OUT = object()  # a section left out with everything under it


def _blocks(tree: list[tuple[int, str, str]], lang: str) -> list[tuple[int, list[tuple[str, str]]]]:
    """(group, [(heading, text)]) of each wanted heading of a page and its subtree: a subsection belongs to the nearest
    wanted heading above it, unless it is wanted itself (it is then a block of its own), so no section is read twice.
    A link section (LINK_SECTIONS) or a stopped heading (STOP) leaves its subtree out."""
    blocks: list[tuple[int, list[tuple[str, str]]]] = []
    above: list[tuple] = []  # (level, the block, its first heading) of the sections the current one is in
    for level, head, text in tree:
        while above and above[-1][0] >= level:
            above.pop()
        block, root = above[-1][1:] if above else (None, None)
        group = _group(head, lang)
        if block is _OUT or head.strip().lower() in LINK_SECTIONS or (
                group is None and common.wanted(head, STOP[lang]) and not common.wanted(head, HOLDERS[lang])):
            block = _OUT
        elif group is not None:
            block, root = (group, []), head
            blocks.append(block)
        above.append((level, block, root))
        if text and block is not None and block is not _OUT:
            block[1].append((head if head is root else f"{root} / {head}", text))
    return blocks


def _cut(text: str, room: int) -> str:
    return text if len(text) <= room else (text[:room - 2].rsplit(None, 1) or [""])[0] + " …"


def _capped(sections: list[tuple[str, str]], cap: int) -> list[tuple[str, str]]:
    """The sections cut to `cap` characters of text in all: an equal share each, what a short one leaves going to the
    longer ones. All of them when they fit; else the first ones, until MIN_SHARE of each (all of a shorter one) has
    filled the cap."""
    kept, spent = [], 0
    for head, text in sections:
        if spent >= cap:
            break
        kept.append((head, text))
        spent += min(len(text), MIN_SHARE)
    sections = kept
    room, left = {}, cap
    for done, n in enumerate(sorted(range(len(sections)), key=lambda n: len(sections[n][1]))):
        room[n] = min(len(sections[n][1]), left // (len(sections) - done))
        left -= room[n]
    return [(head, _cut(text, room[n])) for n, (head, text) in enumerate(sections)]


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    """Stage 4: research notes {"notes": [{lang, url, heading, text}], "languages": {lang: notes}}.

    Per language (common.dossier_languages), of the building's own page: the lead and each wanted heading with its
    subsections ("<heading> / <subsection>"), within LEAD_CAP and CAPS. The leads come first, then the art and liturgy
    of every language, the architecture, the history. No part or whole is read: the art a building holds has its own
    rows. One Wikidata note closes the list."""
    entity = {**wikidata.entity(row.qid), "id": row.qid}
    leads: list[dict] = []
    grouped: list[list[dict]] = [[] for _ in CAPS]
    languages = common.dossier_languages(row, cfg)
    for lang in languages:
        wikipedia, title = wiki_for(lang, "wikipedia"), row.sitelinks[f"{lang}wiki"]
        source = {"lang": lang, "url": wikipedia.url(title)}
        if lead := wikipedia.lead(title):
            leads.append({**source, "heading": "Lead", "text": _cut(lead, LEAD_CAP)})
        for group, sections in _blocks(common.section_tree(wikipedia, title), lang):
            grouped[group] += [{**source, "heading": head, "text": text}
                               for head, text in _capped(sections, CAPS[group])]
    notes = [*leads, *(n for group in grouped for n in group)]
    counts = {lang: sum(n["lang"] == lang for n in notes) for lang in languages}
    note = wikidata_note(entity, lambda qid: common.label(wikidata, qid))
    return {"notes": notes + ([note] if note else []), "languages": counts}


def dossier_stage(row, cfg: dict, wiki_for, wikidata) -> tuple[str, dict]:
    """Stage 4 function for run_stage: a non-calibration row with no Wikipedia note is dropped."""
    return common.dossier_outcome(dossier(row, cfg, wiki_for, wikidata), row.calibration)


# ---- calibration and the benchmark ----

def hold_benchmark(queue, cfg: dict) -> None:
    """Hold back the configured benchmark buildings, whatever source found them (see domains.hold_benchmarks)."""
    benchmark_code.hold(queue, "architecture", cfg.get("architecture", {}).get("benchmark", []))


def add_calibration(queue, sparql, qids: list[str], benchmark: list[dict] | None = None) -> int:
    """Add the buildings SeminaVerbi already covers, flagged as calibration, and every benchmark building (kinds
    `strong`, `weak` and `control`) held back until `release-benchmark` (see benchmark.add). A benchmark building that
    is also one of `works` is held back all the same; released, it keeps its `list` source and needs no floor."""
    benchmark = load_config()["architecture"]["benchmark"] if benchmark is None else benchmark
    return benchmark_code.add(queue, sparql, "architecture", qids, benchmark, WORK_CLASSES)


def calibration_qids(wikis: dict, wikidata=None, tree: dict | None = None) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the sacred architecture category of every wiki, and the 'lang:Title' of those
    with no local item or no Wikipedia sitelink (reported, never guessed)."""
    wikidata = wikidata or WikidataClient()
    tree = tree or categories.load(Path(__file__).resolve().parents[2])
    return common.calibration_qids(wikis, wikidata, tree, {"sacred-architecture"})
