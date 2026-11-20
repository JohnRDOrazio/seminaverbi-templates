"""Code shared by the pipeline domains: the SPARQL query builders, row parsing, Wikipedia section reading and
the calibration walk over the category tree."""
from __future__ import annotations

import re
from urllib.parse import unquote

from sync import categories
from sync.wiki import SyncError

LANGS = ("en", "it", "es", "fr", "de", "pt")
SAINT_PROPERTY = "P411"
LIMIT = 500  # per query: keeps each one well under the endpoint's 60 s timeout
CHUNK = 50
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


def query(pattern: str) -> str:
    """Rows (QID, labels, Wikipedia URLs, sitelink count, P31 types) of the items `pattern` binds to ?item."""
    return f"{_FIELDS}\nWHERE {{\n{_ORDERED}{pattern}\n{_DETAILS}}}\nGROUP BY ?item\nLIMIT {LIMIT}"


def bounded(pattern: str, cls: str) -> str:
    """The query of `pattern` on items of class `cls`, at most LIMIT items: the most linked ones (sitelink
    count), so a truncated result drops the least notable."""
    return query(f"  {{ SELECT DISTINCT ?item ?sitelinks WHERE {{\n{_ORDERED}{pattern}\n"
                  f"  ?item wdt:P31/wdt:P279* wd:{cls} .\n  ?item wikibase:sitelinks ?sitelinks .\n"
                  f"  }} ORDER BY DESC(?sitelinks) LIMIT {LIMIT} }}")


def values(qids) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def title(url: str) -> str:
    return unquote(url.rsplit("/wiki/", 1)[-1]).replace("_", " ")


def row_data(row: dict) -> tuple[dict, dict, dict]:
    labels = {l: row[f"l_{l}"] for l in LANGS if row.get(f"l_{l}")}
    sitelinks = {f"{l}wiki": title(row[f"s_{l}"]) for l in LANGS if row.get(f"s_{l}")}
    data = {"count": int(row.get("n") or len(sitelinks)), "instance_of": row.get("types", "").split()}
    return labels, sitelinks, data


_TAG = re.compile(r"<[^>]+>")


def heading(raw: str) -> str:
    return _TAG.sub("", raw).strip()


def qids(entity: dict, prop: str) -> list[str]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out


def entity_sitelinks(entity: dict) -> dict[str, str]:
    return {site: v["title"] for site, v in entity.get("sitelinks", {}).items() if "title" in v}


def sections(wiki, title: str) -> list[tuple[str, str]]:
    """The page's sections, or none when the page does not exist; any other API error propagates."""
    try:
        return [(heading(h), t) for h, t in wiki.sections(title)]
    except SyncError as exc:
        if "missingtitle" in str(exc) or "nosuchpage" in str(exc):
            return []
        raise


def wikidata_qid(repo, wikidata, lang: str, title: str) -> str | None:
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


def field_keys(tree: dict, roots: set[str]) -> set[str]:
    """The `roots` and every descendant of them in the category tree."""
    keys = set(roots)
    for node in categories.nodes(tree):  # nodes come parent-first, so one pass collects every descendant
        if node.parent_key in keys:
            keys.add(node.key)
    return keys


def calibration_qids(wikis: dict, wikidata, tree: dict, roots: set[str]) -> tuple[list[str], list[str]]:
    """Wikidata QIDs of the articles in the categories of `roots` (and their descendants) on every wiki, and
    the 'lang:Title' of those with no local item or no Wikipedia sitelink (reported, never guessed)."""
    keys = field_keys(tree, roots)
    repo = wikis["en"]  # the farm's Wikibase repository: the client wikis have no wbgetentities
    qids: list[str] = []
    unmapped: list[str] = []
    for lang, wiki in wikis.items():
        for node in categories.nodes(tree):
            if node.key not in keys or lang not in node.names:
                continue
            for page, ns in wiki.members(f"Category:{node.names[lang]}"):
                if ns != 0:
                    continue
                qid = wikidata_qid(repo, wikidata, lang, page)
                if qid is None:
                    if f"{lang}:{page}" not in unmapped:
                        unmapped.append(f"{lang}:{page}")
                elif qid not in qids:
                    qids.append(qid)
    return qids, unmapped


def facet_patterns(dcfg: dict) -> list[tuple[str, str]]:
    """(description, pattern) for every facet; each one is run once per class."""
    film = dcfg
    subjects = values(film["subjects"])
    link = "?item wdt:P921|wdt:P144 ?subject ."
    return [
        ("subjects", f"  VALUES ?subject {{ {subjects} }}\n  {link}"),
        ("books of the Bible", f"  ?subject wdt:P31 wd:{film['book_class']} .\n  {link}"),
        ("saints", f"  ?subject wdt:{SAINT_PROPERTY} [] ; wdt:P31 wd:Q5 .\n  {link}"),
        ("biblical characters",
         f"  VALUES ?class {{ {values(film['biblical_classes'])} }}\n  ?subject wdt:P31 ?class .\n  {link}"),
        ("genres", f"  VALUES ?genre {{ {values(film['genres'])} }}\n  ?item wdt:P136 ?genre ."),
    ]


def run_query(sparql, domain: str, label: str, query: str, limit: int | None = None) -> list[dict]:
    try:
        rows = sparql.select(query)
    except Exception as exc:  # logged, then re-raised: a failed source must stop the run, not shrink it
        print(f"{domain} candidates: {label} failed: {exc}")
        raise
    if len(rows) >= (LIMIT if limit is None else limit):
        print(f"{domain} candidates: {label}: {len(rows)} rows is the query limit, results may be truncated")
    return rows
