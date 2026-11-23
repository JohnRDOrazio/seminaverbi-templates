"""Code shared by the pipeline domains: the SPARQL query builders, row parsing, Wikipedia section reading and
the calibration walk over the category tree."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote

from sync import categories
from sync.wiki import SyncError

LANGS = ("en", "it", "es", "fr", "de", "pt")
SAINT_PROPERTY = "P411"
LIMIT = 500  # per query: keeps each one well under the endpoint's 60 s timeout
CHUNK = 50
CLOSURE_CACHE = Path("data/class_closure.json")  # git-ignored with the rest of data/
CLOSURE_DAYS = 30
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


def query(pattern: str, key: str = "") -> str:
    """Rows (QID, labels, Wikipedia URLs, sitelink count, P31 types) of the items `pattern` binds to ?item;
    `key` (e.g. "?series ") is a variable of the pattern kept next to ?item in each row."""
    return (f"{_FIELDS.replace('SELECT ', 'SELECT ' + key, 1)}\nWHERE {{\n{_ORDERED}{pattern}\n{_DETAILS}}}\n"
            f"GROUP BY {key}?item\nLIMIT {LIMIT}")


def bounded(pattern: str, cls: str, subclasses: Iterable[str] | None = None) -> str:
    """The query of `pattern` on items of class `cls`, at most LIMIT items: the most linked ones (sitelink
    count), so a truncated result drops the least notable. `subclasses` (a class closure) replaces the
    `P31/P279*` path by a VALUES list of classes, which the endpoint answers faster."""
    kind = (f"  VALUES ?cls {{ {values(sorted(set(subclasses) | {cls}))} }}\n  ?item wdt:P31 ?cls .\n"
            if subclasses is not None else f"  ?item wdt:P31/wdt:P279* wd:{cls} .\n")
    return query(f"  {{ SELECT DISTINCT ?item ?sitelinks WHERE {{\n{_ORDERED}{pattern}\n"
                  f"{kind}  ?item wikibase:sitelinks ?sitelinks .\n"
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


def domain_config(cfg: dict, name: str) -> dict:
    """A copy of `cfg` where every key of `cfg[name]` that also exists at the top level (the sitelink floor)
    overrides it; the domain's block stays in the copy. `cfg` is not changed."""
    block = cfg.get(name, {})
    return {**cfg, **{key: value for key, value in block.items() if key in cfg}}


def _read_closure_cache(cache: Path) -> dict:
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _fresh_entry(entry) -> list[str] | None:
    """The cached subclasses of an entry younger than CLOSURE_DAYS (and not dated in the future); None when it is missing, stale or malformed
    (including a subclass that is not a string)."""
    try:
        age = date.today() - date.fromisoformat(entry["date"])
        if timedelta(0) <= age < timedelta(days=CLOSURE_DAYS):
            subclasses = entry["subclasses"]
            if isinstance(subclasses, list) and all(isinstance(sub, str) for sub in subclasses):
                return subclasses
    except (KeyError, TypeError, ValueError):
        pass
    return None


def class_closure(sparql, classes: Iterable[str], cache: Path | None = None) -> set[str]:
    """The `classes` and all their subclasses (P279*), one query per class not cached within CLOSURE_DAYS; the
    cache is a JSON file {class: {"date": ..., "subclasses": [...]}}. A fetch failure is logged and raised."""
    cache = cache or CLOSURE_CACHE
    stored = _read_closure_cache(cache)
    closure: set[str] = set()
    fetched = False
    for cls in classes:
        subclasses = _fresh_entry(stored.get(cls))
        if subclasses is None:
            rows = run_query(sparql, "closure", cls, f"SELECT ?sub WHERE {{ ?sub wdt:P279* wd:{cls} . }}", limit=10**9)
            subclasses = sorted({cls} | {row["sub"] for row in rows if row.get("sub")})
            stored[cls] = {"date": date.today().isoformat(), "subclasses": subclasses}
            fetched = True
        closure.update(subclasses)
        closure.add(cls)
    if fetched:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(stored, indent=1, sort_keys=True), encoding="utf-8")
    return closure


def owns(instance_of: Iterable[str], work_classes: Iterable[str], sparql, cache: Path | None = None) -> bool:
    """True when any QID of `instance_of` is one of `work_classes` or a subclass of one."""
    return not set(instance_of).isdisjoint(class_closure(sparql, work_classes, cache))


def other_domain(queue, qid: str, domain: str) -> str | None:
    """The row's domain when the QID is already a candidate of another domain (the queue is keyed by QID alone):
    reported, and the caller skips it."""
    old = queue.row(qid)
    if old and old.domain != domain:
        print(f"{domain} candidates: {qid} is already a {old.domain} candidate, skipped")
        return old.domain
    return None


CLOSED = ("approved", "created", "declined")  # a whole with one of these statuses absorbs nothing


def _wholes_of(sparql, qids: list[str], classes: set[str], stop: set[str]) -> dict[str, list[str]]:
    """Part QID -> the wholes (P361) of it that are instances of one of `classes` and of none of `stop`; one
    query per CHUNK of parts."""
    found: dict[str, list[str]] = {}
    for i in range(0, len(qids), CHUNK):
        exclude = (f"  FILTER NOT EXISTS {{ VALUES ?stop {{ {values(sorted(stop))} }} ?whole wdt:P31 ?stop . }}\n"
                   if stop else "")
        rows = run_query(sparql, "parts", "wholes of parts", (
            "SELECT DISTINCT ?item ?whole WHERE {\n"
            f"  VALUES ?item {{ {values(qids[i:i + CHUNK])} }}\n"
            f"  VALUES ?cls {{ {values(sorted(classes))} }}\n"
            f"  ?item wdt:P361 ?whole .\n  ?whole wdt:P31 ?cls .\n{exclude}}}\nLIMIT {LIMIT}"))
        for row in rows:
            if row["whole"] not in found.setdefault(row["item"], []):
                found[row["item"]].append(row["whole"])
    return found


def _whole_rows(sparql, qids: list[str]) -> dict[str, dict]:
    """Labels, sitelinks and count of the wholes `qids`, as `query` rows keyed by QID."""
    rows: dict[str, dict] = {}
    for i in range(0, len(qids), CHUNK):
        pattern = f"  VALUES ?item {{ {values(qids[i:i + CHUNK])} }}"
        rows.update({r["item"]: r for r in run_query(sparql, "parts", "whole details", query(pattern))})
    return rows


def _insert_row(queue, row: dict, source: str, domain: str) -> int:
    if other_domain(queue, row["item"], domain):
        return 0
    labels, sitelinks, data = row_data(row)
    old = queue.row(row["item"])
    stored = old.data.get(0, {}) if old else {}
    if stored.get("parts") and "count" in stored:
        data["count"] = max(data["count"], stored["count"])  # a whole folded earlier counts its best part
    return queue.add(row["item"], domain, source, labels, sitelinks, False, data)


def _fold_into_whole(queue, whole_qid: str, whole_row: dict, parts: list[dict], source: str, domain: str) -> int:
    """Add or extend the whole's row with `parts` (`query` rows); the part rows already in the queue are marked
    `folded_into` (triage then drops them). A whole already closed gets nothing."""
    if other_domain(queue, whole_qid, domain):
        return 0
    old = queue.row(whole_qid)
    if old and old.status in CLOSED:
        return 0
    labels, sitelinks, data = row_data(whole_row)
    known = dict(old.data.get(0, {}).get("parts", {})) if old else {}
    new_part = False
    for part in parts:
        _, part_sitelinks, part_data = row_data(part)
        entry = {"sitelinks": part_sitelinks, "count": part_data["count"]}
        new_part = new_part or known.get(part["item"]) != entry
        known[part["item"]] = entry
        own = queue.row(part["item"])
        if own is not None and own.domain == domain and own.data.get(0, {}).get("folded_into") != whole_qid:
            queue.add(part["item"], domain, own.sources[0], {}, {}, False, {"folded_into": whole_qid})
            if own.status is None:
                queue.redo(2, part["item"])
            else:
                print(f"{domain} candidates: {part['item']} folded into {whole_qid} but already "
                      f"{own.status}, left alone")
    data.update(parts=known, count=max([data["count"], *(p["count"] for p in known.values())]))
    added = queue.add(whole_qid, domain, source, labels, sitelinks, False, data)
    if new_part and old and old.status is None:
        queue.redo(1, whole_qid)  # coverage, triage and the later stages saw the previous parts
    return added


def fold_parts(queue, sparql, rows: list[dict], source: str, domain: str, whole_classes: Iterable[str],
               stop_classes: Iterable[str] = ()) -> int:
    """Add the `query` rows under `domain`/`source`, each part replaced by its whole (P361) when it has exactly
    one in the closure of `whole_classes` and outside that of `stop_classes`: the whole carries the source and
    a stage-0 `parts` entry for the part. A part with no such whole, or with several (reported), is added as
    itself. Returns the rows added to the queue."""
    classes = class_closure(sparql, whole_classes)
    stop = class_closure(sparql, stop_classes) if stop_classes else set()
    in_whole = _wholes_of(sparql, [r["item"] for r in rows], classes, stop)
    details = _whole_rows(sparql, sorted({w[0] for w in in_whole.values() if len(w) == 1}))
    grouped: dict[str, list[dict]] = {}
    added = 0
    for row in rows:
        found = in_whole.get(row["item"], [])
        if len(found) > 1:
            print(f"{domain} candidates: {row['item']} has {len(found)} wholes, kept on its own")
        if len(found) == 1 and found[0] in details:
            grouped.setdefault(found[0], []).append(row)
        else:
            added += _insert_row(queue, row, source, domain)
    for whole_qid, parts in grouped.items():
        added += _fold_into_whole(queue, whole_qid, details[whole_qid], parts, source, domain)
    return added


def _part_title(qid: str, sitelinks: dict) -> str:
    return sitelinks.get("enwiki") or next(iter(sitelinks.values()), None) or qid


def covered_with_parts(row, repo, wikis) -> tuple[str, dict]:
    """Stage 1: `stages.covered` on the whole; a whole it passes is still a translation suggestion when one of
    its parts is covered on some wiki (the first such part sets `have` and the `note`)."""
    from types import SimpleNamespace

    from pipeline import stages

    outcome, data = stages.covered(row, repo, wikis)
    if outcome != "pass" or row.calibration or data.get("have"):
        return outcome, data
    for qid, part in row.data.get(0, {}).get("parts", {}).items():
        sitelinks = part.get("sitelinks", {})
        stand_in = SimpleNamespace(qid=qid, sitelinks=sitelinks, labels={}, calibration=False, data={})
        part_outcome, part_data = stages.covered(stand_in, repo, wikis)
        if part_outcome != "pass":
            have = part_data["have"]
            return "translation", {"have": have, "missing": [lang for lang in LANGS if lang not in have],
                                   "note": f"covered through {_part_title(qid, sitelinks)}"}
    return outcome, data
