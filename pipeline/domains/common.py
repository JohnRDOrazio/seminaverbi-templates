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
from sync.wiki import SyncError, repo_client

LANGS = ("en", "it", "es", "fr", "de", "pt")
SAINT_PROPERTY = "P411"
LIMIT = 500  # per query: keeps each one well under the endpoint's 60 s timeout
CHUNK = 50
CLOSURE_CACHE = Path("data/class_closure.json")  # git-ignored with the rest of data/
CLOSURE_DAYS = 30
# a closure up to this many classes goes into a query as a VALUES list; a larger one as the P31/P279* path (sculpture's
# 145,420 classes made a 2 MB query, which the endpoint refused with HTTP 413)
VALUES_MAX = 500
# walk such a path up from the item, not down from the class through every subclass (timeouts otherwise)
_FORWARD = " hint:Prior hint:gearing \"forward\" ."
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


def bounded(pattern: str, cls: str, subclasses: Iterable[str] | None = None, floor: int | None = None,
            test: str = "") -> str:
    """The query of `pattern` on items of class `cls`, at most LIMIT items: the most linked ones (sitelink
    count), so a truncated result drops the least notable. `subclasses` (a class closure) of up to VALUES_MAX
    classes replaces the `P31/P279*` path by a list of classes (`FILTER IN`: a VALUES list, joined before the
    pattern, timed out on WDQS); a larger closure keeps the path, walked up from the item. `floor` keeps only
    items with at least that many sitelinks, tested right after the pattern, before the class. `test` is a pattern
    on ?item and ?sitelinks, tested last (its lines end with a newline)."""
    listed = None if subclasses is None else set(subclasses) | {cls}
    if listed is None:
        kind = f"  ?item wdt:P31/wdt:P279* wd:{cls} .\n"
    elif len(listed) <= VALUES_MAX:
        kind = f"  ?item wdt:P31 ?cls . FILTER(?cls IN ({one_of(listed)}))\n"
    else:
        kind = f"  ?item wdt:P31/wdt:P279* wd:{cls} .{_FORWARD}\n"
    if floor is None:
        body = f"{pattern}\n{kind}  ?item wikibase:sitelinks ?sitelinks .\n"
    else:
        body = f"{pattern}\n  ?item wikibase:sitelinks ?sitelinks . FILTER(?sitelinks >= {int(floor)})\n{kind}"
    body += test
    return query(f"  {{ SELECT DISTINCT ?item ?sitelinks WHERE {{\n{_ORDERED}{body}"
                 f"  }} ORDER BY DESC(?sitelinks) LIMIT {LIMIT} }}")


def one_of(qids: Iterable[str]) -> str:
    """The QIDs as the list of a SPARQL `IN (...)`, sorted."""
    return ", ".join(f"wd:{q}" for q in sorted(set(qids)))


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


def press_exempt(row, threshold: float = 0.60) -> bool:
    """True when a Catholic-media mention of the work was kept at `threshold` or more: a press-only work skips the
    sitelink floor."""
    press = row.data.get(0, {}).get("press") or {}
    return bool(press) and max(press.values()) >= threshold


def entity_sitelinks(entity: dict) -> dict[str, str]:
    return {site: v["title"] for site, v in entity.get("sitelinks", {}).items() if "title" in v}


def sections(wiki, title: str) -> list[tuple[str, str]]:
    """The page's sections, or none when the page does not exist; any other API error propagates."""
    return [(heading(h), t) for h, t in _unless_missing(wiki.sections, title)]


def section_tree(wiki, title: str) -> list[tuple[int, str, str]]:
    """`sections`, each with its level before it."""
    return [(level, heading(h), t) for level, h, t in _unless_missing(wiki.section_tree, title)]


def _unless_missing(read, title: str) -> list:
    try:
        return read(title)
    except SyncError as exc:
        if "missingtitle" in str(exc) or "nosuchpage" in str(exc):
            return []
        raise


def wanted(heading: str, words: Iterable[str], skip: Iterable[str] = ()) -> bool:
    """True when one of `words` (lower-case fragments) starts a word of the section heading; a fragment with a trailing
    blank is a whole word, whatever follows it (the end of the heading, punctuation, a hyphen). A heading of `skip` (whole, lower-case) is not wanted."""
    if heading.strip().lower() in skip:
        return False
    low = heading.lower()
    return any(re.search(rf"\b{re.escape(word.rstrip())}" + (r"\b" if word.endswith(" ") else ""), low)
               for word in words)


def read_page(wikipedia, title: str, lang: str, words: Iterable[str], label: str | None = None) -> list[dict]:
    """The lead and the sections of one page whose heading is `wanted`, as dossier notes, headed by `label`
    ("<label> / <heading>") for a page read with another."""
    words = tuple(words)
    notes = []
    if lead := wikipedia.lead(title):
        notes.append({"lang": lang, "url": wikipedia.url(title), "heading": label or "Lead", "text": lead})
    for head, text in sections(wikipedia, title):
        if text and wanted(head, words):
            notes.append({"lang": lang, "url": wikipedia.url(title),
                          "heading": f"{label} / {head}" if label else head, "text": text})
    return notes


def dossier_languages(row, cfg: dict) -> list[str]:
    """The languages of LANGS the work has a Wikipedia page in, at most cfg['dossier_languages']."""
    return [l for l in LANGS if f"{l}wiki" in row.sitelinks][:cfg["dossier_languages"]]


def dossier_outcome(result: dict, calibration: bool) -> tuple[str, dict]:
    """Stage 4 on a built dossier: a non-calibration one with no note but the Wikidata one is dropped (it is a list
    of names, not evidence)."""
    if not any(n["heading"] != "Wikidata" for n in result["notes"]) and not calibration:
        return "drop", {"reason": "empty dossier"}
    return "pass", result


def label(wikidata, qid: str, lang: str = "en") -> str | None:
    return wikidata.entity(qid).get("labels", {}).get(lang, {}).get("value")


def inception_year(entity: dict) -> str | None:
    """The year of the first P571 statement that has one."""
    for claim in entity.get("claims", {}).get("P571", []):
        time = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time", "")
        if match := re.match(r"([+-]?)(\d+)-", time):
            return str(int(match.group(2))) + (" BCE" if match.group(1) == "-" else "")
    return None


def named_fact(entity: dict, prop: str, role: str, labels_of) -> list[str]:
    """["<role>: <names>"] of the items `prop` states, [] when it states none with a label."""
    names = [n for q in qids(entity, prop) if (n := labels_of(q))]
    return [f"{role}: {', '.join(names)}"] if names else []


def facts_note(entity: dict, facts: list[str]) -> dict | None:
    """The dossier's Wikidata note of these facts; None when there are none."""
    if not facts:
        return None
    text = "; ".join(facts)
    return {"lang": "en", "url": f"https://www.wikidata.org/wiki/{entity.get('id', '')}", "heading": "Wikidata",
            "text": text[0].upper() + text[1:] + "."}


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
    repo = repo_client(wikis)  # the farm's Wikibase repository: the client wikis have no wbgetentities
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


_CLOSURE_MEMO: dict[Path, tuple[tuple[int, int] | None, dict]] = {}  # cache path -> (file stamp, parsed content)


def _stamp(cache: Path) -> tuple[int, int] | None:
    try:
        st = cache.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _read_closure_cache(cache: Path) -> dict:
    """The parsed cache file, read once per process (again only when the file changed under us)."""
    stamp = _stamp(cache)
    memo = _CLOSURE_MEMO.get(cache)
    if memo is not None and memo[0] == stamp:
        return memo[1]
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _CLOSURE_MEMO[cache] = (stamp, data)
    return data


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
        _CLOSURE_MEMO[cache] = (_stamp(cache), stored)
    return closure


def owns(instance_of: Iterable[str], work_classes: Iterable[str], sparql, cache: Path | None = None,
         stop: Iterable[str] = ()) -> bool:
    """True when any QID of `instance_of` is one of `work_classes` or a subclass of one, and not one of `stop` or
    a subclass of one (a branch of the work classes' closure the domain does not take)."""
    stop = tuple(stop)
    taken = class_closure(sparql, work_classes, cache) - (class_closure(sparql, stop, cache) if stop else set())
    return not set(instance_of).isdisjoint(taken)


def other_domain(queue, qid: str, domain: str) -> str | None:
    """The row's domain when the QID is already a candidate of another domain (the queue is keyed by QID alone):
    reported, and the caller skips it."""
    old = queue.row(qid)
    if old and old.domain != domain:
        print(f"{domain} candidates: {qid} is already a {old.domain} candidate, skipped")
        return old.domain
    return None


_FLOOR_REASON = re.compile(r"\d+ sitelinks, under the floor of \d+")  # the reason every domain's triage gives
CLOSED = ("approved", "created", "declined")  # a whole with one of these statuses absorbs nothing


def _instance_of(var: str, roots: Iterable[str], closure: set[str]) -> str:
    """`?whole` is an instance of a class bound to `?var`: one of `closure` (`FILTER IN`) when it has at most
    VALUES_MAX classes, else one of the `roots` through the P31/P279* path, walked up from the whole."""
    if len(closure) <= VALUES_MAX:
        return f"?whole wdt:P31 ?{var} . FILTER(?{var} IN ({one_of(closure)}))"
    return f"VALUES ?{var} {{ {values(sorted(set(roots)))} }} ?whole wdt:P31/wdt:P279* ?{var} .{_FORWARD}"


def _wholes_of(sparql, qids: list[str], classes: tuple[Iterable[str], set[str]],
               stop: tuple[Iterable[str], set[str]]) -> dict[str, list[str]]:
    """Part QID -> the wholes (P361) of it that are instances of one of `classes` and of none of `stop` (each
    a pair of root classes and their closure); one query per CHUNK of parts."""
    found: dict[str, list[str]] = {}
    exclude = f"  FILTER NOT EXISTS {{ {_instance_of('stop', *stop)} }}\n" if stop[1] else ""
    for i in range(0, len(qids), CHUNK):
        rows = run_query(sparql, "parts", "wholes of parts", (
            "SELECT DISTINCT ?item ?whole WHERE {\n"
            f"  VALUES ?item {{ {values(qids[i:i + CHUNK])} }}\n"
            f"  ?item wdt:P361 ?whole .\n  {_instance_of('cls', *classes)}\n{exclude}}}\nLIMIT {LIMIT}"))
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


def _fold_into_whole(queue, whole_qid: str, whole_row: dict, parts: list[dict], source: str, domain: str,
                     recheck=None) -> int:
    """Add or extend the whole's row with `parts` (`query` rows); the part rows already in the queue are marked
    `folded_into` (triage then drops them; an open one is also marked `retired`). A whole already closed gets
    nothing. An open whole that gains parts is re-checked with `recheck(row)` (the domain's stage 1) and, when
    that finds it covered, marked `retired`; its outcomes are kept either way."""
    if other_domain(queue, whole_qid, domain):
        return 0
    parts = [p for p in parts if not other_domain(queue, p["item"], domain)]  # another domain's part stays there
    if not parts:
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
            elif own.status == "open":
                # its proposal page is live, with votes: nothing is deleted, the row leaves publication and the
                # ranking, and the page is the author's to decline or redirect
                queue.add(part["item"], domain, own.sources[0], {}, {}, False, {"retired": True})
                print(f"{domain} candidates: {part['item']} folded into {whole_qid} but already open: retired; "
                      f"decline or redirect {own.page} by hand")
            else:
                print(f"{domain} candidates: {part['item']} folded into {whole_qid} but already "
                      f"{own.status}, left alone")
    data.update(parts=known, count=max([data["count"], *(p["count"] for p in known.values())]))
    pending = bool(new_part and old and old.status == "open" and not old.data.get(0, {}).get("retired"))
    if pending:
        data["recheck_pending"] = True  # stored with the new parts: cleared only once a check succeeds
    added = queue.add(whole_qid, domain, source, labels, sitelinks, False, data)
    if new_part and old and old.status is None:
        queue.redo(1, whole_qid)  # coverage, triage and the later stages saw the previous parts
    elif pending and recheck is not None:
        _recheck_open_whole(queue, whole_qid, domain, recheck)
    return added


def _recheck_open_whole(queue, whole_qid: str, domain: str, recheck) -> None:
    """A published whole is covered through a part it gained: it leaves publication and the ranking (its page
    is live, with votes: nothing is deleted, and the page is the author's to decline or redirect). The
    `recheck_pending` mark is cleared only after `recheck` returns; a failure leaves it for the next run."""
    row = queue.row(whole_qid)
    outcome, _ = recheck(row)
    covered = outcome in ("translation", "drop")
    queue.add(whole_qid, domain, row.sources[0], {}, {}, False,
              {"recheck_pending": False, **({"retired": True} if covered else {})})
    if covered:
        print(f"{domain} candidates: {whole_qid} gained parts and is now covered ({outcome}) but already open: "
              f"retired; decline or redirect {row.page} by hand")


def floor_drop(row) -> bool:
    """True when the row's stage-2 drop is the sitelink floor's and the author has not skipped the row."""
    return (row.outcomes.get(2) == "drop" and not row.skipped
            and bool(_FLOOR_REASON.fullmatch(row.data.get(2, {}).get("reason", ""))))


def retriage(queue, domain: str, triage, cfg: dict) -> int:
    """Stage 2 again for the rows of `domain` the sitelink floor dropped that a later source may now exempt (a floor
    drop that gained `unesco`): a row with no status, not calibration, which is a `floor_drop` and which `triage`, on
    the row as stored now, passes has its outcomes from stage 2 on cleared. A row with a status, one skipped by hand
    and one dropped for another reason are never touched. Returns how many were cleared."""
    cleared = 0
    for row in queue.open_rows(domain):
        if floor_drop(row) and triage(row, cfg)[0] == "pass":
            queue.redo(2, row.qid)
            cleared += 1
    return cleared


def recheck_pending(queue, domain: str, recheck) -> None:
    """Re-check every open, unretired whole of `domain` whose earlier check did not complete."""
    for row in queue.by_status("open"):
        stage0 = row.data.get(0, {})
        if row.domain == domain and stage0.get("recheck_pending") and not stage0.get("retired"):
            _recheck_open_whole(queue, row.qid, domain, recheck)


def fold_parts(queue, sparql, rows: list[dict], source: str, domain: str, whole_classes: Iterable[str],
               stop_classes: Iterable[str] = (), recheck=None) -> int:
    """Add the `query` rows under `domain`/`source`, each part replaced by its whole (P361) when it has exactly
    one in the closure of `whole_classes` and outside that of `stop_classes`: the whole carries the source and
    a stage-0 `parts` entry for the part. A part with no such whole, or with several (reported), is added as
    itself. `recheck` re-checks an open whole that gains parts (see _fold_into_whole). Returns the rows added to
    the queue."""
    whole_classes, stop_classes = tuple(whole_classes), tuple(stop_classes)
    classes = class_closure(sparql, whole_classes)
    stop = class_closure(sparql, stop_classes) if stop_classes else set()
    in_whole = _wholes_of(sparql, [r["item"] for r in rows], (whole_classes, classes), (stop_classes, stop))
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
        added += _fold_into_whole(queue, whole_qid, details[whole_qid], parts, source, domain, recheck)
    return added


def press_parts(qid: str, entity: dict, sparql, domain: str, whole_classes: Iterable[str],
                stop_classes: Iterable[str] = ()) -> dict:
    """The stage-0 data coverage needs for a whole the press found that no query of `domain` added: its parts
    (items part of it, P361, or listed as its parts, P527) as `fold_parts` stores them, so `covered_with_parts`
    sees a part SeminaVerbi already has. {} for an item outside the closure of `whole_classes`, inside that of
    `stop_classes`, or with no parts."""
    instance_of = qids(entity, "P31")
    if not owns(instance_of, whole_classes, sparql) or (stop_classes and owns(instance_of, stop_classes, sparql)):
        return {}
    pattern = f"  {{ ?item wdt:P361 wd:{qid} }} UNION {{ wd:{qid} wdt:P527 ?item }}"
    parts = {}
    for row in run_query(sparql, domain, "parts of a press whole", query(pattern)):
        if row["item"] != qid:
            _, sitelinks, data = row_data(row)
            parts[row["item"]] = {"sitelinks": sitelinks, "count": data["count"]}
    if not parts:
        return {}
    own = len([s for s in entity_sitelinks(entity) if s.endswith("wiki") and s not in NON_WIKIPEDIA])
    return {"parts": parts, "count": max([own, *(p["count"] for p in parts.values())])}


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
