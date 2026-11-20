"""Literature: candidate finding (stage 0), triage (stage 2) and the dossier (stage 4).

Sources, tagged on each queue row: ``author`` (works of the configured authors), ``facet`` (works about Jesus,
the Bible, a saint or a biblical character, or in a Christian genre), ``illustrissimi`` and
``illustrissimi:author`` (the works the Illustrissimi letters name, and other works of their authors),
``francis`` (the works Pope Francis's 2024 letter names) and ``calibration`` (the works and series
SeminaVerbi already has articles on).

Stage-0 data on a row: ``count`` (sitelinks), ``instance_of`` (P31 QIDs), ``series`` (bool), ``volumes``
(``{volume_qid: {"sitelinks": {...}, "count": int}}``), ``lead_sitelinks`` and ``folded_into`` (series QID).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote as urlquote

from sync import categories
from sync.wikidata import WikidataClient

from pipeline import stages
from pipeline.sources import Wikipedia

from . import common, film
from .common import CHUNK, LANGS, LIMIT  # noqa: F401

CLOSED = ("approved", "created", "declined")  # a series with one of these statuses absorbs nothing

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


def _other_domain(queue, qid: str):
    """The row's domain when the QID is already a candidate of another domain (the queue is keyed by QID alone):
    reported, and the caller skips it."""
    old = queue.row(qid)
    if old and old.domain != "literature":
        print(f"literature candidates: {qid} is already a {old.domain} candidate, skipped")
        return old.domain
    return None


def _insert(queue, row: dict, source: str, series: bool = False) -> int:
    if _other_domain(queue, row["item"]):
        return 0
    labels, sitelinks, data = common.row_data(row)
    old = queue.row(row["item"])
    stored = old.data.get(0, {}) if old else {}
    if series:
        data["series"] = True
    if (series or stored.get("series") or stored.get("volumes")) and "count" in stored:
        data["count"] = max(data["count"], stored["count"])  # a series folded earlier counts its best volume
    return queue.add(row["item"], "literature", source, labels, sitelinks, False, data)


def _add_rows(queue, rows: list[dict], source: str, series: bool = False, sparql=None,
              series_classes: list[str] | None = None) -> int:
    """Add the rows under `source`; `series`: they came from a query bounded to a series class (P31 may hold only
    a subclass of it), kept as data[0]["series"]. Never written False, so a work query on the same item does not
    clear it. Every source goes through here. Rows of a work query are folded into their series (`fold`) when
    `sparql` is given; rows of a series query are series themselves and are added as they are."""
    if series or sparql is None:
        return sum(_insert(queue, row, source, series) for row in rows)
    return fold(queue, sparql, rows, source, {"literature": {"series_classes": series_classes or []}})


def series_of(sparql, qids: list[str], series_classes: list[str]) -> dict[str, list[str]]:
    """Volume QID -> the literary series (P179) it is part of: an instance of (a subclass of) one of
    `series_classes` that shares an author (P50) with the volume, which keeps publisher series out. One query
    per CHUNK of volumes."""
    found: dict[str, list[str]] = {}
    for i in range(0, len(qids), CHUNK):
        rows = _run(sparql, "series of volumes", (
            "SELECT DISTINCT ?volume ?series WHERE {\n"
            f"  VALUES ?volume {{ {common.values(qids[i:i + CHUNK])} }}\n"
            f"  VALUES ?class {{ {common.values(series_classes)} }}\n"
            "  ?volume wdt:P179 ?series .\n  ?volume wdt:P50 ?author .\n  ?series wdt:P50 ?author .\n"
            f"  ?series wdt:P31/wdt:P279* ?class .\n}}\nLIMIT {LIMIT}"))
        for row in rows:
            if row["series"] not in found.setdefault(row["volume"], []):
                found[row["volume"]].append(row["series"])
    return found


def _series_rows(sparql, qids: list[str]) -> dict[str, dict]:
    """Labels, sitelinks and count of the series `qids`, as `common.query` rows keyed by QID."""
    rows: dict[str, dict] = {}
    for i in range(0, len(qids), CHUNK):
        pattern = f"  VALUES ?item {{ {common.values(qids[i:i + CHUNK])} }}"
        rows.update({r["item"]: r for r in _run(sparql, "series details", common.query(pattern))})
    return rows


def _has_wikipedia(sitelinks: dict) -> bool:
    return any(f"{lang}wiki" in sitelinks for lang in LANGS)


def _fold_into(queue, series_qid: str, series_row: dict, volumes: list[dict], source: str) -> int:
    """Add or extend the series row with `volumes` (common.query rows); the volume rows already in the queue
    are marked folded (their own sources untouched). A series already approved, created or declined gets
    nothing."""
    labels, sitelinks, data = common.row_data(series_row)
    return _extend_series(queue, series_qid, labels, sitelinks, data, volumes, source)


def _extend_series(queue, series_qid: str, labels: dict, sitelinks: dict, data: dict, volumes: list[dict],
                   source: str, foldable: set[str] | None = None) -> int:
    """`foldable`: the volumes whose standalone row may be marked folded (None: all of them; those of `fold` share
    an author with the series by construction). The others are only recorded on the series, for coverage."""
    if _other_domain(queue, series_qid):
        return 0
    old = queue.row(series_qid)
    if old and old.status in CLOSED:
        return 0
    known = dict(old.data.get(0, {}).get("volumes", {})) if old else {}
    new_volume = False
    for volume in volumes:
        _, volume_sitelinks, volume_data = common.row_data(volume)
        entry = {"sitelinks": volume_sitelinks, "count": volume_data["count"]}
        new_volume = new_volume or known.get(volume["item"]) != entry  # new, or its count or pages changed
        known[volume["item"]] = entry
        own = queue.row(volume["item"]) if foldable is None or volume["item"] in foldable else None
        if own is not None and own.domain == "literature" and own.data.get(0, {}).get("folded_into") != series_qid:
            queue.add(volume["item"], "literature", own.sources[0], {}, {}, False, {"folded_into": series_qid})
            if own.status is None:
                queue.redo(2, volume["item"])  # triage runs again and drops the volume
            else:
                print(f"literature candidates: {volume['item']} folded into {series_qid} but already "
                      f"{own.status}, left alone")
    data.update(series=True, volumes=known, count=max([data["count"], *(v["count"] for v in known.values())]))
    if not _has_wikipedia(sitelinks):
        with_page = [v for v in known.values() if _has_wikipedia(v["sitelinks"])]
        data["lead_sitelinks"] = max(with_page or known.values(), key=lambda v: v["count"])["sitelinks"]
    added = queue.add(series_qid, "literature", source, labels, sitelinks, False, data)
    if new_volume and old and old.status is None:
        queue.redo(1, series_qid)  # coverage, triage and the later stages saw the previous volume list
    return added


def attach_volumes(queue, sparql, qids: list[str], series_classes: list[str]) -> None:
    """Give the series `qids` found directly (no volumes yet) their volumes: the parts (P527) and the items that
    are part of them (P179), with their Wikipedia sitelinks. The volumes are recorded on the series (as `fold`
    does) and not added as rows; a volume's own row is folded only when it shares an author with the series and
    is in no other literary series (`series_of`), as in `fold`."""
    todo = []
    for qid in dict.fromkeys(qids):
        row = queue.row(qid)
        if row and row.domain == "literature" and row.data.get(0, {}).get("series") \
                and not row.data[0].get("volumes"):
            todo.append(row)
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        pattern = (f"  VALUES ?series {{ {common.values([r.qid for r in chunk])} }}\n"
                   "  { ?series wdt:P527 ?item } UNION { ?item wdt:P179 ?series }")
        # ?shared is bound only for a volume sharing an author with the series (a BIND(EXISTS) is left unbound)
        pattern += "\n  OPTIONAL { ?item wdt:P50 ?a . ?series wdt:P50 ?a . BIND(\"true\" AS ?shared) }"
        rows = _run(sparql, "volumes of series", common.query(pattern, "?series ?shared "))
        shared = sorted({r["item"] for r in rows if r.get("shared") == "true"})
        other = series_of(sparql, shared, series_classes) if shared else {}
        for row in chunk:
            mine = [r for r in rows if r["series"] == row.qid and r["item"] != row.qid]
            foldable = {r["item"] for r in mine
                        if r.get("shared") == "true" and set(other.get(r["item"], [])) <= {row.qid}}
            if mine:
                _extend_series(queue, row.qid, row.labels, row.sitelinks, {"count": row.data[0].get("count", 0)},
                               mine, row.sources[0], foldable)


def fold(queue, sparql, rows: list[dict], source: str, cfg: dict) -> int:
    """Add the work rows (`common.query` rows), each volume of a literary series replaced by its series: the
    series row carries the source and a `volumes` entry for the volume (see the module docstring). A volume in
    no series, or in several (reported), is added as itself. Returns the rows added to the queue."""
    in_series = series_of(sparql, [r["item"] for r in rows], cfg["literature"]["series_classes"])
    details = _series_rows(sparql, sorted({s[0] for s in in_series.values() if len(s) == 1}))
    grouped: dict[str, list[dict]] = {}
    added = 0
    for row in rows:
        found = in_series.get(row["item"], [])
        if len(found) > 1:
            print(f"literature candidates: {row['item']} in {len(found)} series, kept on its own")
        if len(found) == 1 and found[0] in details:
            grouped.setdefault(found[0], []).append(row)
        else:
            added += _insert(queue, row, source)
    for series_qid, volumes in grouped.items():
        added += _fold_into(queue, series_qid, details[series_qid], volumes, source)
    return added


def _bounded_rows(queue, sparql, pattern: str, label: str, source: str, classes: list[str]) -> tuple[int, int]:
    """Run `pattern` bounded to each class and add the rows: (rows added to the queue, rows found)."""
    added = found = 0
    series_classes = [c for c in classes if c != WORK]
    for cls in classes:
        rows = _run(sparql, f"{label} ({cls})", common.bounded(pattern, cls))
        found += len(rows)
        added += _add_rows(queue, rows, source, series=cls != WORK, sparql=sparql, series_classes=series_classes)
        if cls != WORK:
            attach_volumes(queue, sparql, [r["item"] for r in rows], series_classes)
    return added, found


def _works_of(queue, sparql, qids: list[str], source: str, label: str, classes: list[str]) -> int:
    """Works (P50) of the authors `qids`, one chunk of authors at a time."""
    added = 0
    for i in range(0, len(qids), CHUNK):
        pattern = f"  VALUES ?author {{ {common.values(qids[i:i + CHUNK])} }}\n  ?item wdt:P50 ?author ."
        added += _bounded_rows(queue, sparql, pattern, label, source, classes)[0]
    return added


def _direct_pattern(qids: list[str]) -> str:
    """The entries themselves, or the work of each one that is a fictional character (P1441, present in work;
    never of a human, whose works are found by P50: P1441 on a person lists Bible books and the like)."""
    values = common.values(qids)  # each branch binds its own entries: a group cannot see a sibling's VALUES
    return (f"  {{ VALUES ?entry {{ {values} }}\n"
            "    ?entry wdt:P1441 ?item FILTER NOT EXISTS { ?entry wdt:P31 wd:Q5 } }\n"
            f"  UNION {{ VALUES ?item {{ {values} }} }}")


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
    exclude(queue, sparql, cfg)
    return added


def exclude(queue, sparql, cfg: dict) -> None:
    """Mark the religious texts (an instance of a `religious_classes` class, or part of the Bible) so that triage
    drops them, and move the musical works (`music_class`: musicals, operas, songs with a libretto or lyrics) to
    the music domain, where they wait for its stages. A religious text that is also a musical work (a psalm) is
    excluded. Only the rows that can pass triage are checked (above the floor, or named directly by a source);
    a row already triaged is triaged again, one with a status is left alone."""
    lit = cfg["literature"]
    todo = [r for r in queue.open_rows("literature")
            if set(r.sources) & DIRECT or r.data.get(0, {}).get("count", 0) >= cfg["sitelink_floor"]]
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        rows = _run(sparql, "religious texts and musical works", (
            "SELECT DISTINCT ?item ?kind WHERE {\n"
            f"  VALUES ?item {{ {common.values([r.qid for r in chunk])} }}\n"
            f"  {{ VALUES ?class {{ {common.values(lit['religious_classes'])} }}\n"
            "    ?item wdt:P31/wdt:P279* ?class . BIND(\"religious\" AS ?kind) }\n"
            f"  UNION {{ ?item wdt:P361+ wd:{lit['bible']} . BIND(\"religious\" AS ?kind) }}\n"
            f"  UNION {{ ?item wdt:P31/wdt:P279* wd:{lit['music_class']} . BIND(\"music\" AS ?kind) }}\n"
            f"}}\nLIMIT {LIMIT}"))
        kinds: dict[str, set[str]] = {}
        for row in rows:
            kinds.setdefault(row["item"], set()).add(row["kind"])
        for qid, found in kinds.items():
            if "religious" in found:
                queue.add(qid, "literature", queue.row(qid).sources[0], {}, {}, False,
                          {"excluded": "a religious text"})
                if 2 in queue.row(qid).outcomes:
                    queue.redo(2, qid)
            else:
                queue.set_domain(qid, "music")
                print(f"literature candidates: {qid} is a musical work, moved to the music domain")


HEADINGS = {
    "en": ("plot", "synopsis", "summary", "content", "structure", "theme", "analysis", "interpretation", "religio",
           "christian", "reception"),
    "it": ("trama", "sinossi", "contenuto", "struttura", "tema", "temi", "analisi", "interpretazione", "religios",
           "critica"),
    "es": ("argumento", "sinopsis", "resumen", "contenido", "estructura", "temas", "análisis", "interpretación",
           "religios"),
    "fr": ("intrigue", "résumé", "synopsis", "contenu", "structure", "thème", "analyse", "interprétation",
           "religi"),
    "de": ("handlung", "inhalt", "aufbau", "thema", "themen", "deutung", "interpretation", "analyse", "religi"),
    "pt": ("enredo", "sinopse", "resumo", "conteúdo", "estrutura", "temas", "análise", "interpretação",
           "religios"),
}


def _wanted(heading: str, lang: str) -> bool:
    low = heading.lower()
    return any(re.search(rf"\b{re.escape(word)}", low) for word in HEADINGS[lang])  # at a word start only


def _author_names(entity: dict, lang: str, wikidata) -> list[str]:
    names = []
    for qid in common.qids(entity, "P50"):
        label = wikidata.entity(qid).get("labels", {}).get(lang, {}).get("value")
        if label:
            names.append(label.lower())
    return names


def _volume_pages(entity: dict, stage0: dict, wikidata) -> list[dict[str, str]]:
    """Sitelinks of each volume of a series: the stored `volumes`, else the series' parts (P527); a volume with
    no stored sitelinks is read from its Wikidata entity."""
    volumes = stage0.get("volumes")
    qids = list(volumes) if volumes else common.qids(entity, "P527")
    return [(volumes or {}).get(qid, {}).get("sitelinks") or common.entity_sitelinks(wikidata.entity(qid))
            for qid in qids]


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    """Stage 4: research notes {"notes": [{lang, url, heading, text}], "languages": {lang: notes}, "links": [url]}.

    Languages are the original language (P407, when it is one of the six), then en, it, es, fr, de, pt, limited to
    those the work has a Wikipedia page in (the row's sitelinks, or `lead_sitelinks` when it has none) and to
    cfg['dossier_languages']. Per language: the lead and wanted sections of the work's page and, for a series, of
    each volume's page (headed "<page> / <heading>"); Wikiquote sections naming an author (P50). `links` are the
    work's Wikisource pages in those languages: never fetched, never a note."""
    entity = wikidata.entity(row.qid)
    stage0 = row.data.get(0, {})
    pages = row.sitelinks if _has_wikipedia(row.sitelinks) else stage0.get("lead_sitelinks", {})
    original = next((film.ORIGINAL_LANGUAGES[q] for q in common.qids(entity, "P407")
                     if q in film.ORIGINAL_LANGUAGES), None)
    wanted = [l for l in dict.fromkeys([original, *LANGS]) if l and f"{l}wiki" in pages]
    languages = wanted[:cfg["dossier_languages"]]
    notes, links = [], []
    volumes = _volume_pages(entity, stage0, wikidata) if stage0.get("series") else []
    own = common.entity_sitelinks(entity)
    for lang in languages:
        wikipedia = wiki_for(lang, "wikipedia")
        title = pages[f"{lang}wiki"]
        read = {title}
        lead = wikipedia.lead(title)
        if lead:
            notes.append({"lang": lang, "url": wikipedia.url(title), "heading": "Lead", "text": lead})
        for heading, text in common.sections(wikipedia, title):
            if text and _wanted(heading, lang):
                notes.append({"lang": lang, "url": wikipedia.url(title), "heading": heading, "text": text})
        for page in (v[f"{lang}wiki"] for v in volumes if f"{lang}wiki" in v):
            if page in read:
                continue
            read.add(page)
            if lead := wikipedia.lead(page):
                notes.append({"lang": lang, "url": wikipedia.url(page), "heading": page, "text": lead})
            for heading, text in common.sections(wikipedia, page):
                if text and _wanted(heading, lang):
                    notes.append({"lang": lang, "url": wikipedia.url(page), "heading": f"{page} / {heading}",
                                  "text": text})
        if lang in ("en", "it"):
            authors = _author_names(entity, lang, wikidata)
            quote = wiki_for(lang, "wikiquote")
            for heading, text in common.sections(quote, title) if authors else []:
                if text and any(name in heading.lower() for name in authors):
                    notes.append({"lang": lang, "url": quote.url(title), "heading": heading, "text": text})
        if source := own.get(f"{lang}wikisource"):
            links.append(f"https://{lang}.wikisource.org/wiki/{urlquote(source.replace(' ', '_'), safe='/:()')}")
    counts = {lang: sum(n["lang"] == lang for n in notes) for lang in languages}
    return {"notes": notes, "languages": counts, "links": links}


def dossier_stage(row, cfg: dict, wiki_for, wikidata) -> tuple[str, dict]:
    """Stage 4 function for run_stage: a non-calibration row with no notes is dropped."""
    result = dossier(row, cfg, wiki_for, wikidata)
    if not result["notes"] and not row.calibration:
        return "drop", {"reason": "empty dossier"}
    return "pass", result


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
    if data.get("excluded"):
        return "drop", f"{data['excluded']}, not a literary work"
    if EDITION in data.get("instance_of", []):
        return "drop", "an edition or translation, not a work of its own"
    if not set(row.sources) & DIRECT:
        count, floor = data.get("count", 0), cfg["sitelink_floor"]
        if count < floor:
            return "drop", f"{count} sitelinks, under the floor of {floor}"
    return "pass", ""


def _volume_title(qid: str, sitelinks: dict) -> str:
    return sitelinks.get("enwiki") or next(iter(sitelinks.values()), None) or qid


def covered(row, repo, wikis) -> tuple[str, dict]:
    """Stage 1: `stages.covered` on the series; a series it passes is still a translation suggestion when one of
    its volumes is covered on some wiki (the first such volume sets `have` and the `note`)."""
    outcome, data = stages.covered(row, repo, wikis)
    if outcome != "pass" or row.calibration or data.get("have"):
        return outcome, data  # the series itself is covered (or an existing article): not covered only through a volume
    for qid, volume in row.data.get(0, {}).get("volumes", {}).items():
        sitelinks = volume.get("sitelinks", {})
        stand_in = SimpleNamespace(qid=qid, sitelinks=sitelinks, labels={}, calibration=False, data={})
        volume_outcome, volume_data = stages.covered(stand_in, repo, wikis)
        if volume_outcome != "pass":
            have = volume_data["have"]
            missing = [lang for lang in LANGS if lang not in have]
            return "translation", {"have": have, "missing": missing,
                                   "note": f"covered through {_volume_title(qid, sitelinks)}"}
    return outcome, data
