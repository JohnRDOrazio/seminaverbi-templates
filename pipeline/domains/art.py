"""Art: the owned classes, Jev's significance wording and triage (stage 2).

Sources, tagged on each queue row: ``depicts`` (what the work shows, scenes included), ``iconclass``, ``genre``, ``list`` and ``calibration``.
Stage-0 data on a row: ``count`` (sitelinks), ``instance_of`` (P31 QIDs) and ``folded_into``.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from pipeline import load_config
from sync import categories
from sync.wikidata import WikidataClient

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


def _gather(sparql, pattern: str, label: str, floor: int) -> list[dict]:
    """The rows of `pattern` bounded to each work class (its closure, see common.bounded) with at least `floor`
    sitelinks, one row per item; people are reported and left out."""
    found: dict[str, dict] = {}
    for cls in WORK_CLASSES:
        query = common.bounded(pattern, cls, common.class_closure(sparql, [cls]), floor)
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
    `wikidata` are unused. The queries keep only works at the sitelink floor, which triage applies anyway: on
    WDQS the floor is what keeps them under the timeout."""
    art = cfg["art"]
    floor = art.get("sitelink_floor", cfg["sitelink_floor"])
    shown = common.values([*art["depicts"], *art["scene_classes"]])  # scenes are events: depicted, not P31
    link = "?item wdt:P180 ?d ."
    patterns: list[tuple[str, str, str]] = [
        ("depicts", "depicts", f"  VALUES ?d {{ {shown} }}\n  {link}"),
        ("depicts", "depicts saints", f"  ?d wdt:{common.SAINT_PROPERTY} [] .\n  {link}"),  # the saints first
        ("depicts", "depicts biblical characters",
         f"  VALUES ?class {{ {common.values(art['biblical_classes'])} }}\n  ?d wdt:P31 ?class .\n  {link}"),
    ]
    prefixes = " || ".join(f'STRSTARTS(STR(?ic), "{p}")' for p in art["iconclass_prefixes"])
    patterns.append(("iconclass", "Iconclass", f"  ?item wdt:P1257 ?ic .\n  FILTER({prefixes})"))
    patterns.append(("genre", "genres",
                     f"  VALUES ?genre {{ {common.values(art['genres'])} }}\n  ?item wdt:P136 ?genre ."))
    added = 0
    try:
        for source, label, pattern in patterns:
            rows = _gather(sparql, pattern, label, floor)
            added += common.fold_parts(queue, sparql, rows, source, "art", WORK_CLASSES, STOP_CLASSES)
        listed = _listed(sparql, [w["qid"] for w in art["works"]])
        added += common.fold_parts(queue, sparql, listed, "list", "art", WORK_CLASSES, STOP_CLASSES)
    finally:  # a failed source keeps the rows already written: the benchmark works among them are held all the same
        _hold_benchmark(queue, art.get("benchmark", []))
    return added


# ---- stage 4: the dossier ----

# lower-case fragments of the section headings worth reading, per language (matched at a word start)
HEADINGS: dict[str, tuple[str, ...]] = {
    "en": ("background", "context", "history", "provenance", "origin", "commission", "description", "iconography",
           "subject", "composition", "symbolism", "interpretation", "analysis", "theme", "meaning", "reception"),
    "it": ("contesto", "storia", "provenienza", "origine", "committenza", "descrizione", "iconografia", "soggetto",
           "composizione", "simbolismo", "interpretazione", "analisi", "tema", "significato", "accoglienza",
           "ricezione"),
    "es": ("antecedentes", "contexto", "historia", "procedencia", "origen", "encargo", "descripción",
           "iconografía", "tema", "composición", "simbolismo", "interpretación", "análisis", "significado",
           "recepción"),
    "fr": ("contexte", "histoire", "provenance", "origine", "commande", "description", "iconographie", "sujet",
           "composition", "symbolisme", "interprétation", "analyse", "thème", "signification", "réception",
           "accueil"),
    "de": ("hintergrund", "geschichte", "provenienz", "entstehung", "auftrag", "beschreibung", "ikonographie",
           "ikonografie", "bildthema", "komposition", "symbolik", "interpretation", "analyse", "bedeutung",
           "rezeption"),
    "pt": ("antecedentes", "contexto", "história", "proveniência", "origem", "encomenda", "descrição",
           "iconografia", "tema", "composição", "simbolismo", "interpretação", "análise", "significado",
           "recepção"),
}
CREATOR = "P170"
WIKIDATA_FACTS = (("P180", "depicts"), ("P170", "creator"), ("P195", "collection"), ("P276", "location"),
                  ("P144", "based on"))


def _wanted(heading: str, lang: str) -> bool:
    low = heading.lower()
    return any(re.search(rf"\b{re.escape(word)}", low) for word in HEADINGS[lang])


def _year(entity: dict) -> str | None:
    for claim in entity.get("claims", {}).get("P571", []):
        time = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time", "")
        if match := re.match(r"([+-]?)(\d+)-", time):
            return str(int(match.group(2))) + (" BCE" if match.group(1) == "-" else "")
    return None


def wikidata_note(entity: dict, labels_of: Callable[[str], str | None]) -> dict | None:
    """One note of what Wikidata records about the work: what it depicts (P180), creator (P170), date (P571),
    collection (P195), location (P276) and what it is based on (P144). Names only. None when there is nothing
    to say."""
    facts = []
    for prop, role in WIKIDATA_FACTS:
        names = [n for q in common.qids(entity, prop) if (n := labels_of(q))]
        if names:
            facts.append(f"{role}: {', '.join(names)}")
        if prop == CREATOR and (year := _year(entity)):
            facts.append(f"date: {year}")
    if not facts:
        return None
    qid = entity.get("id", "")
    text = "; ".join(facts)
    return {"lang": "en", "url": f"https://www.wikidata.org/wiki/{qid}", "heading": "Wikidata",
            "text": text[0].upper() + text[1:] + "."}


def _label(wikidata, qid: str, lang: str = "en") -> str | None:
    return wikidata.entity(qid).get("labels", {}).get(lang, {}).get("value")


def _part_pages(entity: dict, stage0: dict, wikidata) -> list[dict[str, str]]:
    """Sitelinks of each part of a whole: the stored `parts` (their stored sitelinks first), then the whole's
    other parts (P527)."""
    parts = stage0.get("parts")
    qids = list(dict.fromkeys([*(parts or {}), *common.qids(entity, "P527")]))
    return [(parts or {}).get(qid, {}).get("sitelinks") or common.entity_sitelinks(wikidata.entity(qid))
            for qid in qids]


def _read_page(wikipedia, title: str, lang: str, label: str | None = None) -> list[dict]:
    """The lead and the wanted sections of one page, headed by `label` ("<part> / <heading>") for a part."""
    notes = []
    if lead := wikipedia.lead(title):
        notes.append({"lang": lang, "url": wikipedia.url(title), "heading": label or "Lead", "text": lead})
    for heading, text in common.sections(wikipedia, title):
        if text and _wanted(heading, lang):
            notes.append({"lang": lang, "url": wikipedia.url(title),
                          "heading": f"{label} / {heading}" if label else heading, "text": text})
    return notes


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    """Stage 4: research notes {"notes": [{lang, url, heading, text}], "languages": {lang: notes}}.

    Languages are en, it, es, fr, de, pt (an artwork has no original-language rule), limited to those the work
    has a Wikipedia page in and to cfg['dossier_languages']. Per language: the lead and wanted sections of the
    work's page and of each part's page (headed "<part> / <heading>"); Wikiquote sections naming the creator.
    One Wikidata note closes the list (names and dates)."""
    entity = {**wikidata.entity(row.qid), "id": row.qid}
    languages = [l for l in common.LANGS if f"{l}wiki" in row.sitelinks][:cfg["dossier_languages"]]
    notes: list[dict] = []
    parts = _part_pages(entity, row.data.get(0, {}), wikidata)
    for lang in languages:
        wikipedia = wiki_for(lang, "wikipedia")
        title = row.sitelinks[f"{lang}wiki"]
        notes += _read_page(wikipedia, title, lang)
        read = {title}
        for page in (p[f"{lang}wiki"] for p in parts if f"{lang}wiki" in p):
            if page not in read:
                read.add(page)
                notes += _read_page(wikipedia, page, lang, page)
        if lang in ("en", "it"):
            creators = [label.lower() for q in common.qids(entity, CREATOR) if (label := _label(wikidata, q, lang))]
            quote = wiki_for(lang, "wikiquote")
            for heading, text in common.sections(quote, title) if creators else []:
                if text and any(name in heading.lower() for name in creators):
                    notes.append({"lang": lang, "url": quote.url(title), "heading": heading, "text": text})
    counts = {lang: sum(n["lang"] == lang for n in notes) for lang in languages}
    note = wikidata_note(entity, lambda qid: _label(wikidata, qid))
    return {"notes": notes + ([note] if note else []), "languages": counts}


def dossier_stage(row, cfg: dict, wiki_for, wikidata) -> tuple[str, dict]:
    """Stage 4 function for run_stage: a non-calibration row with no Wikipedia or Wikiquote note is dropped (the
    Wikidata note alone is a list of names, not evidence)."""
    result = dossier(row, cfg, wiki_for, wikidata)
    if not any(n["heading"] != "Wikidata" for n in result["notes"]) and not row.calibration:
        return "drop", {"reason": "empty dossier"}
    return "pass", result


# ---- calibration and the benchmark ----

def _released(queue, qid: str) -> bool:
    row = queue.row(qid)
    return bool(row and row.data.get(0, {}).get("released"))


def _hold_benchmark(queue, benchmark: list[dict]) -> None:
    """A benchmark work some other source found is held back too (a benchmark calibration row, as add_calibration
    makes it), so it is never proposed before `release-benchmark`, whatever the order of the commands."""
    for work in benchmark:
        row = queue.row(work["qid"])
        if row and row.domain == "art" and row.status is None and not row.data.get(0, {}).get("released") and not (
                row.calibration and row.data.get(0, {}).get("benchmark")):
            queue.add(row.qid, row.domain, row.sources[0], row.labels, row.sitelinks, True,
                      {"benchmark": True, "benchmark_kind": work["kind"]})
            queue.redo(1, row.qid)


def _settled(queue, qid: str) -> bool:
    """True, reported, for a row that already has a status (open, approved, created, declined): calibration
    leaves its flag and its outcomes alone."""
    row = queue.row(qid)
    if row and row.status is not None:
        print(f"art calibration: {qid} is already {row.status}, left alone")
        return True
    return False


def add_calibration(queue, sparql, qids: list[str], benchmark: list[dict] | None = None) -> int:
    """Add the art works SeminaVerbi already covers, flagged as calibration, and every benchmark work with
    stage-0 data {"benchmark": True, "benchmark_kind": kind} (held back until `release-benchmark`); returns the
    rows added. A QID that answers to no work class, or is a person, is reported and skipped; a benchmark work
    need not be of a work class (a chapel's frescos, a cycle). A row that already has a status, or belongs to
    another domain, is left alone (reported)."""
    benchmark = load_config()["art"]["benchmark"] if benchmark is None else benchmark
    kinds = {b["qid"]: b["kind"] for b in benchmark}
    kinds = {qid: kind for qid, kind in kinds.items()
             if not common.other_domain(queue, qid, "art") and not _released(queue, qid) and not _settled(queue, qid)}
    found: set[str] = set()
    added = 0
    ordinary = [q for q in qids if q not in kinds and not common.other_domain(queue, q, "art")
                and not _released(queue, q) and not _settled(queue, q)]
    for i in range(0, len(ordinary), common.CHUNK):
        values = f"  VALUES ?item {{ {common.values(ordinary[i:i + common.CHUNK])} }}\n"
        for cls in WORK_CLASSES:
            rows = common.run_query(sparql, "art", f"calibration ({cls})",
                                    common.query(values + f"  ?item wdt:P31/wdt:P279* wd:{cls} ."),
                                    limit=common.LIMIT)
            for row in rows:
                if row["item"] in found or PERSON in row.get("types", "").split():
                    continue
                labels, sitelinks, data = common.row_data(row)
                found.add(row["item"])
                added += queue.add(row["item"], "art", "calibration", labels, sitelinks, True, data)
    for qid in ordinary:
        if qid not in found:
            print(f"art calibration: {qid} skipped (a person, or not an art work)")
    for row in (r for r in _listed(sparql, list(kinds)) if r["item"] in kinds):
        labels, sitelinks, data = common.row_data(row)
        data = {**data, "benchmark": True, "benchmark_kind": kinds[row["item"]]}
        added += queue.add(row["item"], "art", "calibration", labels, sitelinks, True, data)
    return added


def _without(tree: dict, key: str) -> dict:
    """A copy of the category tree with the `key` node and its subtree removed."""
    def prune(items):
        return [{**item, "children": prune(item["children"])} if "children" in item else item
                for item in items if item["key"] != key]
    return {**tree, "fields": prune(tree.get("fields", []))} if "fields" in tree else tree


def calibration_qids(wikis: dict, wikidata=None, tree: dict | None = None) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the visual arts and crafts categories of every wiki, comics excluded,
    and the 'lang:Title' of those with no local item or no Wikipedia sitelink (reported, never guessed)."""
    wikidata = wikidata or WikidataClient()
    tree = tree or categories.load(Path(__file__).resolve().parents[2])
    return common.calibration_qids(wikis, wikidata, _without(tree, "comics"), {"visual-arts", "crafts"})
