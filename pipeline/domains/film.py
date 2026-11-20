"""Film and television: candidate finding (stage 0) and triage (stage 2).

Sources, tagged on each queue row: ``vatican`` (the films linked from the en.wikipedia article on the
1995 Vatican film list), ``award:<QID>`` (films that received an award of the configured classes),
``facet`` (films and series about Jesus, the Bible or a book of it, a saint, a biblical character or
Christianity, or in a Christian, biblical or religious genre) and ``calibration`` (the films and series
SeminaVerbi already has articles on).

A row's ``sitelinks`` holds the Wikipedia article URL per language, plus ``count`` (the item's total
number of sitelinks) and ``instance_of`` (its P31 QIDs), which triage reads.

Arts & Faith dropped (no machine-readable list).
"""
from __future__ import annotations

from pathlib import Path

from sync import categories
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
    return _query(f"  {{ SELECT DISTINCT ?item WHERE {{\n{_ORDERED}{pattern}\n  ?item wdt:P31/wdt:P279* wd:{cls} .\n"
                  f"  }} LIMIT {LIMIT} }}")


def _values(qids) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def _row_data(row: dict) -> tuple[dict, dict]:
    labels = {l: row[f"l_{l}"] for l in LANGS if row.get(f"l_{l}")}
    sitelinks = {l: row[f"s_{l}"] for l in LANGS if row.get(f"s_{l}")}
    sitelinks["count"] = int(row.get("n") or len(sitelinks))
    sitelinks["instance_of"] = row.get("types", "").split()
    return labels, sitelinks


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
        return sparql.select(query)
    except Exception as exc:  # logged, then re-raised: a failed source must stop the run, not shrink it
        print(f"film candidates: {label} failed: {exc}")
        raise


def _take(queue, rows: list[dict], source: str, calibration: bool = False) -> int:
    added = 0
    for row in rows:
        labels, sitelinks = _row_data(row)
        added += queue.add(row["item"], "film", source, labels, sitelinks, calibration)
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
    qids: list[str] = []
    unmapped: list[str] = []
    for lang, wiki in wikis.items():
        for node in nodes:
            if node.key not in keys or lang not in node.names:
                continue
            for title, ns in wiki.members(f"Category:{node.names[lang]}"):
                if ns != 0:
                    continue
                qid = _wikidata_qid(wiki, wikidata, lang, title)
                if qid is None:
                    if f"{lang}:{title}" not in unmapped:
                        unmapped.append(f"{lang}:{title}")
                elif qid not in qids:
                    qids.append(qid)
    return qids, unmapped


def _wikidata_qid(wiki, wikidata, lang: str, title: str) -> str | None:
    item = wiki.item_for(lang, title)
    if not item:
        return None
    links = wiki.item_sitelinks(item)[1]
    sites = ["enwiki"] + sorted(s for s in links if s.endswith("wiki") and s != "enwiki")
    for site in sites:
        if site in links and site not in NON_WIKIPEDIA:
            qid = wikidata.by_sitelink(site, [links[site]]).get(links[site])
            if qid:
                return qid
    return None


def triage(row, cfg: dict) -> tuple[str, str]:
    """Stage 2: ('drop', reason) for an episode, trailer or clip, or a facet-only row under the sitelink floor."""
    for qid in row.sitelinks.get("instance_of", []):
        if qid in NON_WORKS:
            return "drop", f"a {NON_WORKS[qid]}, not a work of its own"
    if set(row.sources) == {"facet"}:
        count = row.sitelinks.get("count", 0)
        if count < cfg["sitelink_floor"]:
            return "drop", f"{count} sitelinks, under the floor of {cfg['sitelink_floor']}"
    return "pass", ""
