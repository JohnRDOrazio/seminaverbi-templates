"""Music: the owned classes, Jev's wording and triage (stage 2).

Sources, tagged on each queue row: ``form`` (sacred forms), ``genre``, ``facet`` (subjects), ``artist`` (works of
the configured artists) and ``calibration``. Stage-0 data on a row: ``count`` (sitelinks), ``instance_of`` (P31
QIDs) and ``folded_into`` (the composition QID a part was folded into).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from sync import categories
from sync.wikidata import WikidataClient

from . import common, film

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
    # the artist floor is music's own key: domain_config lifts only the keys the top level has (the sitelink floor)
    artist_only = set(row.sources) == {"artist"}
    floor = cfg["music"]["artist_sitelink_floor"] if artist_only else cfg["sitelink_floor"]
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


# ---- stage 4: the dossier ----

# lower-case fragments of the section headings worth reading, per language (matched at a word start)
HEADINGS: dict[str, tuple[str, ...]] = {
    "en": ("background", "composition", "writing", "recording", "lyrics", "text", "structure", "music", "theme",
           "meaning", "reception", "liturgical"),
    "it": ("contesto", "composizione", "scrittura", "registrazione", "testo", "struttura", "musica", "tema",
           "temi", "significato", "accoglienza", "ricezione", "liturgic"),
    "es": ("antecedentes", "contexto", "composición", "escritura", "grabación", "letra", "texto", "estructura",
           "música", "tema", "significado", "recepción", "litúrgic"),
    "fr": ("contexte", "composition", "écriture", "enregistrement", "paroles", "texte", "structure", "musique",
           "thème", "signification", "accueil", "réception", "liturgi"),
    "de": ("hintergrund", "entstehung", "komposition", "text", "liedtext", "aufbau", "struktur", "musik", "thema",
           "themen", "bedeutung", "rezeption", "liturgi"),
    "pt": ("antecedentes", "contexto", "composição", "escrita", "gravação", "letra", "texto", "estrutura",
           "música", "tema", "significado", "recepção", "litúrgic"),
}
BIBLE_BOOK = "Q29154430"  # book of the Bible
CREATOR_PROPERTIES = ("P86", "P676", "P175")  # composer, lyricist, performer
WIKIDATA_FACTS = (("P86", "composer"), ("P676", "lyricist"), ("P175", "performer"))


def _wanted(heading: str, lang: str) -> bool:
    low = heading.lower()
    return any(re.search(rf"\b{re.escape(word)}", low) for word in HEADINGS[lang])


def _year(entity: dict) -> str | None:
    for prop in ("P577", "P571"):
        for claim in entity.get("claims", {}).get(prop, []):
            time = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time", "")
            if match := re.match(r"[+-]?(\d+)-", time):
                return str(int(match.group(1)))
    return None


def wikidata_note(entity: dict, labels_of: Callable[[str], str | None],
                  is_bible_book: Callable[[str], bool] | None = None) -> dict | None:
    """One note of what Wikidata records about the work: composer, lyricist, performer, date, and the text it
    is based on (P144) or about (P921), with "a book of the Bible" when `is_bible_book(qid)` says so. Names
    only: never the words of a lyric or a liturgical text. None when there is nothing to say."""
    facts = []
    for prop, role in WIKIDATA_FACTS:
        names = [n for q in common.qids(entity, prop) if (n := labels_of(q))]
        if names:
            facts.append(f"{role}: {', '.join(names)}")
    if year := _year(entity):
        facts.append(f"date: {year}")
    for prop, role in (("P144", "based on"), ("P921", "about")):
        named = []
        for qid in common.qids(entity, prop):
            if name := labels_of(qid):
                named.append(f"{name} (a book of the Bible)" if is_bible_book and is_bible_book(qid) else name)
        if named:
            facts.append(f"{role}: {', '.join(named)}")
    if not facts:
        return None
    qid = entity.get("id", "")
    text = "; ".join(facts)
    return {"lang": "en", "url": f"https://www.wikidata.org/wiki/{qid}", "heading": "Wikidata",
            "text": text[0].upper() + text[1:] + "."}


def _label(wikidata, qid: str, lang: str = "en") -> str | None:
    return wikidata.entity(qid).get("labels", {}).get(lang, {}).get("value")


def _creator_names(entity: dict, lang: str, wikidata) -> list[str]:
    names = []
    for prop in CREATOR_PROPERTIES:
        for qid in common.qids(entity, prop):
            if label := _label(wikidata, qid, lang):
                names.append(label.lower())
    return names


def _part_pages(entity: dict, stage0: dict, wikidata) -> list[dict[str, str]]:
    """Sitelinks of each part of a whole: the stored `parts`, else the whole's parts (P527)."""
    parts = stage0.get("parts")
    qids = list(parts) if parts else common.qids(entity, "P527")
    return [(parts or {}).get(qid, {}).get("sitelinks") or common.entity_sitelinks(wikidata.entity(qid))
            for qid in qids]


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    """Stage 4: research notes {"notes": [{lang, url, heading, text}], "languages": {lang: notes}}.

    Languages are the original language (P407, when it is one of the six), then en, it, es, fr, de, pt, limited
    to those the work has a Wikipedia page in and to cfg['dossier_languages']. Per language: the lead and wanted
    sections of the work's page and of each part's page (headed "<part> / <heading>"); Wikiquote sections naming
    a composer, lyricist or performer. One Wikidata note closes the list (names and dates, never a text)."""
    entity = {**wikidata.entity(row.qid), "id": row.qid}
    stage0 = row.data.get(0, {})
    original = next((film.ORIGINAL_LANGUAGES[q] for q in common.qids(entity, "P407")
                     if q in film.ORIGINAL_LANGUAGES), None)
    wanted = [l for l in dict.fromkeys([original, "en", "it", *common.LANGS]) if l and f"{l}wiki" in row.sitelinks]
    languages = wanted[:cfg["dossier_languages"]]
    notes = []
    parts = _part_pages(entity, stage0, wikidata)
    for lang in languages:
        wikipedia = wiki_for(lang, "wikipedia")
        title = row.sitelinks[f"{lang}wiki"]
        read = {title}
        if lead := wikipedia.lead(title):
            notes.append({"lang": lang, "url": wikipedia.url(title), "heading": "Lead", "text": lead})
        for heading, text in common.sections(wikipedia, title):
            if text and _wanted(heading, lang):
                notes.append({"lang": lang, "url": wikipedia.url(title), "heading": heading, "text": text})
        for page in (p[f"{lang}wiki"] for p in parts if f"{lang}wiki" in p):
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
            creators = _creator_names(entity, lang, wikidata)
            quote = wiki_for(lang, "wikiquote")
            for heading, text in common.sections(quote, title) if creators else []:
                if text and any(name in heading.lower() for name in creators):
                    notes.append({"lang": lang, "url": quote.url(title), "heading": heading, "text": text})
    counts = {lang: sum(n["lang"] == lang for n in notes) for lang in languages}
    note = wikidata_note(entity, lambda qid: _label(wikidata, qid),
                         lambda qid: BIBLE_BOOK in common.qids(wikidata.entity(qid), "P31"))
    return {"notes": notes + ([note] if note else []), "languages": counts}


def dossier_stage(row, cfg: dict, wiki_for, wikidata) -> tuple[str, dict]:
    """Stage 4 function for run_stage: a non-calibration row with no Wikipedia or Wikiquote note is dropped (the
    Wikidata note alone is a list of names, not evidence)."""
    result = dossier(row, cfg, wiki_for, wikidata)
    if not any(n["heading"] != "Wikidata" for n in result["notes"]) and not row.calibration:
        return "drop", {"reason": "empty dossier"}
    return "pass", result


# ---- calibration ----

def add_calibration(queue, sparql, qids: list[str]) -> int:
    """Add the music works SeminaVerbi already covers, flagged as calibration; returns the rows added. A QID that
    answers to no work class, or is a person or group, is reported and skipped."""
    found: set[str] = set()
    added = 0
    for i in range(0, len(qids), common.CHUNK):
        values = f"  VALUES ?item {{ {common.values(qids[i:i + common.CHUNK])} }}\n"
        for cls in WORK_CLASSES:
            rows = common.run_query(sparql, "music", f"calibration ({cls})",
                                    common.query(values + f"  ?item wdt:P31/wdt:P279* wd:{cls} ."),
                                    limit=common.LIMIT)
            for row in rows:
                if row["item"] in found or _skippable(row):
                    continue
                labels, sitelinks, data = common.row_data(row)
                found.add(row["item"])
                added += queue.add(row["item"], "music", "calibration", labels, sitelinks, True, data)
    for qid in qids:
        if qid not in found:
            print(f"music calibration: {qid} skipped (a person or group)")
    return added


def calibration_qids(wikis: dict, wikidata=None, tree: dict | None = None) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the music, musical theatre and opera categories of every wiki, and the
    'lang:Title' of those with no local item or no Wikipedia sitelink (reported, never guessed)."""
    wikidata = wikidata or WikidataClient()
    tree = tree or categories.load(Path(__file__).resolve().parents[2])
    return common.calibration_qids(wikis, wikidata, tree, {"music", "musical-theatre", "opera"})
