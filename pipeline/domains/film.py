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
from urllib.parse import unquote

from sync import categories
from sync.wiki import SyncError
from sync.wikidata import WikidataClient

LANGS = ("en", "it", "es", "fr", "de", "pt")
FILM, SERIES = "Q11424", "Q5398426"
SAINT_PROPERTY = "P411"
LIMIT = 500  # per query: keeps each one well under the endpoint's 60 s timeout
CHUNK = 50
# television series episode, trailer, film clip: judged through their series or not at all
NON_WORKS = {"Q21191270": "episode", "Q622550": "trailer", "Q128696755": "clip"}
NON_WIKIPEDIA = {"commonswiki", "wikidatawiki", "metawiki", "specieswiki", "mediawikiwiki"}

# keep the patterns in the order written: left to the optimizer, the wide joins time out on WDQS
_ORDERED = "  hint:Query hint:optimizer \"None\" .\n"
_FIELDS = (
    "SELECT ?item (SAMPLE(?count) AS ?n) (GROUP_CONCAT(DISTINCT ?type; separator=\" \") AS ?types)"
    + "".join(f" (SAMPLE(?lab_{l}) AS ?l_{l}) (SAMPLE(?lnk_{l}) AS ?s_{l})" for l in LANGS)
)
_DETAILS = (
    "  ?item wikibase:sitelinks ?count .\n"
    "  OPTIONAL { ?item wdt:P31 ?t . BIND(STRAFTER(STR(?t), \"/entity/\") AS ?type) }\n"
    + "".join(
        f"  OPTIONAL {{ ?item rdfs:label ?lab_{l} FILTER(LANG(?lab_{l}) = \"{l}\") }}\n"
        f"  OPTIONAL {{ ?lnk_{l} schema:about ?item ; schema:isPartOf <https://{l}.wikipedia.org/> }}\n"
        for l in LANGS)
)


def _query(pattern: str) -> str:
    """Rows (QID, labels, Wikipedia URLs, sitelink count, P31 types) of the items `pattern` binds to ?item."""
    return f"{_FIELDS}\nWHERE {{\n{_ORDERED}{pattern}\n{_DETAILS}}}\nGROUP BY ?item\nLIMIT {LIMIT}"


def _bounded(pattern: str, cls: str) -> str:
    """The query of `pattern` on items of class `cls`, at most LIMIT items: the most linked ones (sitelink
    count), so a truncated result drops the least notable."""
    return _query(f"  {{ SELECT DISTINCT ?item ?sitelinks WHERE {{\n{_ORDERED}{pattern}\n"
                  f"  ?item wdt:P31/wdt:P279* wd:{cls} .\n  ?item wikibase:sitelinks ?sitelinks .\n"
                  f"  }} ORDER BY DESC(?sitelinks) LIMIT {LIMIT} }}")


def _values(qids) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def _title(url: str) -> str:
    return unquote(url.rsplit("/wiki/", 1)[-1]).replace("_", " ")


def _row_data(row: dict) -> tuple[dict, dict, dict]:
    labels = {l: row[f"l_{l}"] for l in LANGS if row.get(f"l_{l}")}
    sitelinks = {f"{l}wiki": _title(row[f"s_{l}"]) for l in LANGS if row.get(f"s_{l}")}
    data = {"count": int(row.get("n") or len(sitelinks)), "instance_of": row.get("types", "").split()}
    return labels, sitelinks, data


def _facet_patterns(cfg: dict) -> list[tuple[str, str]]:
    """(description, pattern) for every facet; each one is run once for films and once for series."""
    film = cfg["film"]
    subjects = _values(film["subjects"])
    link = "?item wdt:P921|wdt:P144 ?subject ."
    return [
        ("subjects", f"  VALUES ?subject {{ {subjects} }}\n  {link}"),
        ("books of the Bible", f"  ?subject wdt:P31 wd:{film['book_class']} .\n  {link}"),
        ("saints", f"  ?subject wdt:{SAINT_PROPERTY} [] ; wdt:P31 wd:Q5 .\n  {link}"),
        ("biblical characters",
         f"  VALUES ?class {{ {_values(film['biblical_classes'])} }}\n  ?subject wdt:P31 ?class .\n  {link}"),
        ("genres", f"  VALUES ?genre {{ {_values(film['genres'])} }}\n  ?item wdt:P136 ?genre ."),
    ]


def _run(sparql, label: str, query: str) -> list[dict]:
    try:
        rows = sparql.select(query)
    except Exception as exc:  # logged, then re-raised: a failed source must stop the run, not shrink it
        print(f"film candidates: {label} failed: {exc}")
        raise
    if len(rows) >= LIMIT:
        print(f"film candidates: {label}: {len(rows)} rows is the query limit, results may be truncated")
    return rows


def _take(queue, rows: list[dict], source: str, calibration: bool = False) -> int:
    added = 0
    for row in rows:
        labels, sitelinks, data = _row_data(row)
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
        rows = _run(sparql, f"vatican ({page})", _query(
            values + f"  {{ ?item wdt:P31/wdt:P279* wd:{FILM} }} UNION {{ ?item wdt:P31/wdt:P279* wd:{SERIES} }}"))
        added += _take(queue, rows, "vatican")

    for award in film["seeds"]["awards"]:
        for cls in (FILM, SERIES):
            pattern = f"  ?award wdt:P31?/wdt:P279? wd:{award} .\n  ?item wdt:P166 ?award ."
            rows = _run(sparql, f"award {award} ({cls})", _bounded(pattern, cls))
            added += _take(queue, rows, f"award:{award}")

    for description, pattern in _facet_patterns(cfg):
        for cls in (FILM, SERIES):
            rows = _run(sparql, f"facet {description} ({cls})", _bounded(pattern, cls))
            added += _take(queue, rows, "facet")
    return added


def add_calibration(queue, sparql, qids: list[str]) -> int:
    """Add the films and series SeminaVerbi already covers, flagged as calibration; returns the rows added."""
    added = 0
    for i in range(0, len(qids), CHUNK):
        values = f"  VALUES ?item {{ {_values(qids[i:i + CHUNK])} }}\n"
        rows = _run(sparql, "calibration", _query(values))
        added += _take(queue, rows, "calibration", calibration=True)
    return added


def calibration_qids(wikis: dict, wikidata=None, tree: dict | None = None) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the film and television categories of every wiki, and the
    'lang:Title' of those with no local item or no Wikipedia sitelink (reported, never guessed)."""
    wikidata = wikidata or WikidataClient()
    tree = tree or categories.load(Path(__file__).resolve().parents[2])
    nodes = categories.nodes(tree)
    keys = {"film", "television"}
    for node in nodes:  # nodes come parent-first, so one pass collects every descendant
        if node.parent_key in keys:
            keys.add(node.key)
    repo = wikis["en"]  # the farm's Wikibase repository: the client wikis have no wbgetentities
    qids: list[str] = []
    unmapped: list[str] = []
    for lang, wiki in wikis.items():
        for node in nodes:
            if node.key not in keys or lang not in node.names:
                continue
            for title, ns in wiki.members(f"Category:{node.names[lang]}"):
                if ns != 0:
                    continue
                qid = _wikidata_qid(repo, wikidata, lang, title)
                if qid is None:
                    if f"{lang}:{title}" not in unmapped:
                        unmapped.append(f"{lang}:{title}")
                elif qid not in qids:
                    qids.append(qid)
    return qids, unmapped


def _wikidata_qid(repo, wikidata, lang: str, title: str) -> str | None:
    """The Wikidata QID of the page `title` on the `lang` SeminaVerbi wiki (site id `lang`), through the local
    item's Wikipedia sitelinks, asked of the repository."""
    item = repo.item_for(lang, title)
    if not item:
        return None
    links = repo.item_sitelinks(item)[1]
    sites = ["enwiki"] + sorted(s for s in links if s.endswith("wiki") and s != "enwiki")
    for site in sites:
        if site in links and site not in NON_WIKIPEDIA:
            qid = wikidata.by_sitelink(site, [links[site]]).get(links[site])
            if qid:
                return qid
    return None


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
    "en": ("plot", "synopsis", "theme", "analysis", "reception", "religio", "critical response", "interpretation"),
    "it": ("trama", "sinossi", "tema", "temi", "analisi", "critica", "accoglienza", "religios", "interpretazione"),
    "es": ("argumento", "sinopsis", "trama", "temas", "análisis", "recepción", "crítica", "religios"),
    "fr": ("intrigue", "synopsis", "thème", "analyse", "accueil", "critique", "réception", "religi"),
    "de": ("handlung", "inhalt", "thema", "themen", "analyse", "rezeption", "kritik", "religi"),
    "pt": ("enredo", "sinopse", "trama", "temas", "análise", "recepção", "crítica", "religios"),
}
SEASONS = {
    "en": ("season", "series"), "it": ("stagione",), "es": ("temporada",), "fr": ("saison",),
    "de": ("staffel",), "pt": ("temporada",),
}
CREATOR_PROPERTIES = ("P57", "P58", "P170")  # director, screenwriter, creator
_TAG = re.compile(r"<[^>]+>")


def _heading(raw: str) -> str:
    return _TAG.sub("", raw).strip()


def _wanted(heading: str, lang: str, series: bool) -> bool:
    low = heading.lower()
    if any(re.search(rf"\b{re.escape(word)}", low) for word in HEADINGS[lang]):  # at a word start only
        return True
    return series and any(re.match(rf"{word}\b", low) for word in SEASONS[lang])


def _qids(entity: dict, prop: str) -> list[str]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out


def _creator_names(entity: dict, lang: str, wikidata) -> list[str]:
    names = []
    for prop in CREATOR_PROPERTIES:
        for qid in _qids(entity, prop):
            label = wikidata.entity(qid).get("labels", {}).get(lang, {}).get("value")
            if label:
                names.append(label.lower())
    return names


def _sections(wiki, title: str) -> list[tuple[str, str]]:
    """The page's sections, or none when the page does not exist; any other API error propagates."""
    try:
        return [(_heading(h), t) for h, t in wiki.sections(title)]
    except SyncError as exc:
        if "missingtitle" in str(exc) or "nosuchpage" in str(exc):
            return []
        raise


def dossier(row, cfg: dict, wiki_for, wikidata) -> dict:
    """Stage 4: research notes {"notes": [{lang, url, heading, text}], "languages": {lang: notes}}.

    Languages are the original language (P364, when it is one of the six), en, then it, limited to those the row
    has a Wikipedia sitelink for and to cfg['dossier_languages']. `wiki_for(lang, project)` gives a Wikipedia or
    Wikiquote reader. On Wikiquote only sections whose heading names a director, screenwriter or creator are kept."""
    entity = wikidata.entity(row.qid)
    series = SERIES in row.data.get(0, {}).get("instance_of", [])
    original = next((ORIGINAL_LANGUAGES[q] for q in _qids(entity, "P364") if q in ORIGINAL_LANGUAGES), None)
    wanted = [l for l in dict.fromkeys([original, "en", "it"]) if l and f"{l}wiki" in row.sitelinks]
    languages = wanted[:cfg["dossier_languages"]]
    notes = []
    for lang in languages:
        title = row.sitelinks[f"{lang}wiki"]
        wikipedia = wiki_for(lang, "wikipedia")
        for heading, text in _sections(wikipedia, title):
            if text and _wanted(heading, lang, series):
                notes.append({"lang": lang, "url": wikipedia.url(title), "heading": heading, "text": text})
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
