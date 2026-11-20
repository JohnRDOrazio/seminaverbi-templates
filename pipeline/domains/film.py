"""Film and television: candidate finding (stage 0), triage (stage 2) and the dossier (stage 4).

Sources, tagged on each queue row: ``vatican`` (the films linked from the en.wikipedia article on the
1995 Vatican film list), ``award:<QID>`` (films that received an award of the configured classes),
``facet`` (films and series about Jesus, the Bible or a book of it, a saint, a biblical character or
Christianity, or in a Christian, biblical or religious genre) and ``calibration`` (the films and series
SeminaVerbi already has articles on).

A row's ``sitelinks`` maps site ids (enwiki, itwiki, ...) to article titles; the item's total number of
sitelinks (``count``) and its P31 QIDs (``instance_of``) are kept in the row's stage-0 data, which
triage reads.

Arts & Faith dropped (no machine-readable list).
"""
from __future__ import annotations

import re
from pathlib import Path

from sync import categories
from sync.wikidata import WikidataClient

from . import common
from .common import CHUNK, LANGS, LIMIT, NON_WIKIPEDIA  # noqa: F401  (re-exported: tests patch film.LIMIT)

_query, _bounded, _values, _title = common.query, common.bounded, common.values, common.title
_row_data, _qids, _sitelinks = common.row_data, common.qids, common.entity_sitelinks
_sections, _heading, _wikidata_qid = common.sections, common.heading, common.wikidata_qid


def _facet_patterns(cfg: dict) -> list[tuple[str, str]]:
    """(description, pattern) for every facet; each one is run once for films and once for series."""
    return common.facet_patterns(cfg["film"])


def _run(sparql, label: str, query: str) -> list[dict]:
    return common.run_query(sparql, "film", label, query, limit=LIMIT)  # LIMIT read here, so tests can patch it

FILM, SERIES = "Q11424", "Q5398426"
# television series episode, trailer, film clip: judged through their series or not at all
NON_WORKS = {"Q21191270": "episode", "Q622550": "trailer", "Q128696755": "clip"}

def _take(queue, rows: list[dict], source: str, calibration: bool = False, series: bool = False) -> int:
    """Add the rows; `series`: they came from a query bounded to the series class (P31 may hold only a subclass
    of it), kept as data[0]["series"]. Never written False, so a film query on the same item does not clear it."""
    added = 0
    for row in rows:
        labels, sitelinks, data = _row_data(row)
        if series:
            data["series"] = True
        added += queue.add(row["item"], "film", source, labels, sitelinks, calibration, data)
    return added


def candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int:
    """Stage 0: add the Vatican-list, award and facet candidates to the queue; returns the rows added."""
    wikidata = wikidata or WikidataClient()
    film = cfg["film"]
    added = 0

    page = film["seeds"]["vatican_page"]
    titles = wikipedia.links(page)
    resolved = wikidata.by_sitelink("enwiki", titles)
    for title in titles:
        if title not in resolved:
            print(f"film candidates: vatican: no Wikidata item for {title!r}")
    qids = list(dict.fromkeys(resolved.values()))
    for i in range(0, len(qids), CHUNK):
        values = f"  VALUES ?item {{ {_values(qids[i:i + CHUNK])} }}\n"
        for cls in (FILM, SERIES):
            rows = _run(sparql, f"vatican ({page}, {cls})", _query(values + f"  ?item wdt:P31/wdt:P279* wd:{cls} ."))
            added += _take(queue, rows, "vatican", series=cls == SERIES)

    for award in film["seeds"]["awards"]:
        for cls in (FILM, SERIES):
            pattern = f"  ?award wdt:P31?/wdt:P279? wd:{award} .\n  ?item wdt:P166 ?award ."
            rows = _run(sparql, f"award {award} ({cls})", _bounded(pattern, cls))
            added += _take(queue, rows, f"award:{award}", series=cls == SERIES)

    for description, pattern in _facet_patterns(cfg):
        for cls in (FILM, SERIES):
            rows = _run(sparql, f"facet {description} ({cls})", _bounded(pattern, cls))
            added += _take(queue, rows, "facet", series=cls == SERIES)
    return added


def add_calibration(queue, sparql, qids: list[str]) -> int:
    """Add the films and series SeminaVerbi already covers, flagged as calibration; returns the rows added."""
    added = 0
    for i in range(0, len(qids), CHUNK):
        values = f"  VALUES ?item {{ {_values(qids[i:i + CHUNK])} }}\n"
        rows = _run(sparql, "calibration", _query(values))
        added += _take(queue, rows, "calibration", calibration=True)
        series = _run(sparql, "calibration (series)", _query(values + f"  ?item wdt:P31/wdt:P279* wd:{SERIES} ."))
        _take(queue, series, "calibration", calibration=True, series=True)
    return added


def calibration_qids(wikis: dict, wikidata=None, tree: dict | None = None) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the film and television categories of every wiki, and the
    'lang:Title' of those with no local item or no Wikipedia sitelink (reported, never guessed)."""
    wikidata = wikidata or WikidataClient()
    tree = tree or categories.load(Path(__file__).resolve().parents[2])
    return common.calibration_qids(wikis, wikidata, tree, {"film", "television"})


def triage(row, cfg: dict) -> tuple[str, str]:
    """Stage 2: ('drop', reason) for an episode, trailer or clip, or a facet-only row under the sitelink floor.

    A calibration row always passes: it is an article SeminaVerbi already has."""
    if row.calibration:
        return "pass", "calibration"
    data = row.data.get(0, {})
    for qid in data.get("instance_of", []):
        if qid in NON_WORKS:
            return "drop", f"a {NON_WORKS[qid]}, not a work of its own"
    if set(row.sources) == {"facet"}:
        count = data.get("count", 0)
        if count < cfg["sitelink_floor"]:
            return "drop", f"{count} sitelinks, under the floor of {cfg['sitelink_floor']}"
    return "pass", ""


# ---- stage 4: the dossier ----

# original language (P364) item -> code, among the six wikis
ORIGINAL_LANGUAGES = {"Q1860": "en", "Q652": "it", "Q1321": "es", "Q150": "fr", "Q188": "de", "Q5146": "pt"}
# lower-case fragments of the section headings worth reading, per language
HEADINGS = {
    "en": ("plot", "synopsis", "theme", "analysis", "reception", "religio", "critical response", "interpretation",
           "premise", "overview", "story", "mytholog", "summary"),
    "it": ("trama", "sinossi", "tema", "temi", "analisi", "critica", "accoglienza", "religios", "interpretazione",
           "premessa", "riassunto", "mitologia"),
    "es": ("argumento", "sinopsis", "trama", "temas", "análisis", "recepción", "crítica", "religios",
           "premisa", "resumen", "mitología"),
    "fr": ("intrigue", "synopsis", "thème", "analyse", "accueil", "critique", "réception", "religi",
           "résumé", "prémisse", "mythologie"),
    "de": ("handlung", "inhalt", "thema", "themen", "analyse", "rezeption", "kritik", "religi",
           "prämisse", "überblick", "mythologie"),
    "pt": ("enredo", "sinopse", "trama", "temas", "análise", "recepção", "crítica", "religios",
           "premissa", "resumo", "mitologia"),
}
SEASONS = {
    "en": ("season", "series"), "it": ("stagione",), "es": ("temporada",), "fr": ("saison",),
    "de": ("staffel",), "pt": ("temporada",),
}
SEASON = "Q3464665"  # television series season
# on a season or episode-list page every section is read except these (lower-case word starts, any language)
SEASON_SKIP = ("cast", "interpreti", "personaggi e interpreti", "reparto", "elenco", "distribution", "besetzung",
               "note", "notes", "references", "referencias", "références", "einzelnachweise", "referências",
               "bibliogra", "external links", "collegamenti esterni", "enlaces externos", "liens externes",
               "weblinks", "ligações externas", "see also", "voci correlate", "véase también", "voir aussi",
               "siehe auch", "ver também", "ratings", "ascolti", "audiencia", "audiences", "audiencias",
               "einschaltquoten", "audiência", "production", "produzione", "producción", "produktion",
               "produção", "development", "sviluppo", "desarrollo", "développement", "entwicklung",
               "desenvolvimento", "home media", "home video", "release", "broadcast", "trasmissione",
               "emisión", "diffusion", "ausstrahlung", "exibição", "awards", "premi", "premios", "récompenses",
               "auszeichnungen", "prêmios", "music", "musica", "música", "musique", "musik")
CREATOR_PROPERTIES = ("P57", "P58", "P170")  # director, screenwriter, creator


def _wanted(heading: str, lang: str, series: bool) -> bool:
    low = heading.lower()
    if any(re.search(rf"\b{re.escape(word)}", low) for word in HEADINGS[lang]):  # at a word start only
        return True
    return series and any(re.match(rf"{word}\b", low) for word in SEASONS[lang])


def _creator_names(entity: dict, lang: str, wikidata) -> list[str]:
    names = []
    for prop in CREATOR_PROPERTIES:
        for qid in _qids(entity, prop):
            label = wikidata.entity(qid).get("labels", {}).get(lang, {}).get("value")
            if label:
                names.append(label.lower())
    return names


def _skipped(heading: str) -> bool:
    low = heading.lower()
    return any(re.match(rf"{re.escape(word)}", low) for word in SEASON_SKIP)


def _season_pages(entity: dict, wikidata) -> list[dict[str, str]]:
    """Sitelinks of the series' seasons (P527 items that are a television series season), in order; when there
    are none, of its list of episodes (P1811)."""
    seasons = []
    for qid in _qids(entity, "P527"):
        part = wikidata.entity(qid)
        if SEASON in _qids(part, "P31"):
            seasons.append(_sitelinks(part))
    if seasons:
        return seasons
    return [_sitelinks(wikidata.entity(qid)) for qid in _qids(entity, "P1811")]


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    """Stage 4: research notes {"notes": [{lang, url, heading, text}], "languages": {lang: notes}}.

    Languages are the original language (P364, when it is one of the six), en, then it, limited to those the row
    has a Wikipedia sitelink for and to cfg['dossier_languages']. `wiki_for(lang, project)` gives a Wikipedia or
    Wikiquote reader. On Wikiquote only sections whose heading names a director, screenwriter or creator are kept."""
    entity = wikidata.entity(row.qid)
    stage0 = row.data.get(0, {})
    series = bool(stage0.get("series")) or SERIES in stage0.get("instance_of", [])
    original = next((ORIGINAL_LANGUAGES[q] for q in _qids(entity, "P364") if q in ORIGINAL_LANGUAGES), None)
    wanted = [l for l in dict.fromkeys([original, "en", "it"]) if l and f"{l}wiki" in row.sitelinks]
    languages = wanted[:cfg["dossier_languages"]]
    notes = []
    season_pages = _season_pages(entity, wikidata) if series else []
    for lang in languages:
        title = row.sitelinks[f"{lang}wiki"]
        wikipedia = wiki_for(lang, "wikipedia")
        lead = wikipedia.lead(title)
        if lead:
            notes.append({"lang": lang, "url": wikipedia.url(title), "heading": "Lead", "text": lead})
        for heading, text in _sections(wikipedia, title):
            if text and _wanted(heading, lang, series):
                notes.append({"lang": lang, "url": wikipedia.url(title), "heading": heading, "text": text})
        for page in (p[f"{lang}wiki"] for p in season_pages if f"{lang}wiki" in p):
            for heading, text in wikipedia.season(page):
                if text and not _skipped(heading):
                    notes.append({"lang": lang, "url": wikipedia.url(page),
                                  "heading": f"{page} / {heading}" if heading else page, "text": text})
        if lang in ("en", "it"):
            creators = _creator_names(entity, lang, wikidata)
            quote = wiki_for(lang, "wikiquote")
            for heading, text in _sections(quote, title) if creators else []:
                if text and any(name in heading.lower() for name in creators):
                    notes.append({"lang": lang, "url": quote.url(title), "heading": heading, "text": text})
    counts = {lang: sum(n["lang"] == lang for n in notes) for lang in languages}
    return {"notes": notes, "languages": counts}


def dossier_stage(row, cfg: dict, wiki_for, wikidata) -> tuple[str, dict]:
    """Stage 4 function for run_stage: a non-calibration row with no notes is dropped."""
    result = dossier(row, cfg, wiki_for, wikidata)
    if not result["notes"] and not row.calibration:
        return "drop", {"reason": "empty dossier"}
    return "pass", result
