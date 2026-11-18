"""python -m tools.connect_items [--wiki en,it] [--apply] [--import-log FILE] [--only TITLE]…
       [--wd TITLE=QID]… [--create TITLE=DESCRIPTION]…

Connects the articles that have no Wikibase item. For each one it finds the Wikidata item, from the first
of: the article's title on the Wikipedia of the same language; the page's interlanguage links [[xx:Title]];
the Wikipedia page its first revision's (import) summary names; the |wikidata= of its {{Interprogetto}} /
{{Collegamenti esterni}}; else a Wikidata label search, whose hits always go to the author. Each source keeps
only candidates of the page's kind (the layout of its old Wikipedia infobox, config/infobox_import.json
"kinds"); a hit reached through a Wikipedia redirect on a page with no infobox goes to the author. It plans
one action per page:

  skip-excluded  listed in config/connect_exclude.json (or a leftover default "Main Page"): never
                 connected, printed in the deletion list
  main-page      the wiki's main page: a sitelink on Q4
  join           the same subject is already connected on another SeminaVerbi wiki: share its item
  local          the Wikidata item already has a local copy: link to it
  import         the Wikidata item has no local copy yet: import it on the server, then link
  create         no Wikidata item: a new local item, only once the author names it with --create
  author         several candidates, none of the page's kind, or an item taken on this wiki

Without --apply nothing is written. With --apply, import rows need the server's import output: without
--import-log the tool prints the EnrichItems command to run and stops (exit 3); with it, the local IDs come
from its `IMPORT <wd> = <local> (mapped|created)` lines. Every newly linked item is then filed
(tools.file_items for that item; a filing failure is reported as file-failed and the run goes on). Author
decisions: --wd "TITLE=QID" forces the Wikidata item, --create "TITLE=description" makes a new local item
(label: the title without its "(…)"; description in the wiki's language; a title that contains "=" is
written "TITLE::description", which splits at the first "::" instead of the first "="); write "it:TITLE…" when the title
is unconnected on more than one wiki. --only TITLE (repeatable) plans, and with --apply writes, only the
named articles; without it every unconnected article of --wiki is planned and applied, so review the whole
dry-run plan before --apply. With --only, --wd and --create must name articles among them.
Credentials (with --apply): SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

import mwparserfromhell

from sync.pages import LANGS
from sync.wiki import SyncError, WikiClient
from sync.wikidata import WikidataClient
from tools.imports import (WIKIPEDIA, import_command, local_copy as _local_copy, page_key, parse_import_log,
                           sitelink_clash)

ROOT = Path(__file__).resolve().parent.parent
TAG = "seminaverbi-mcp"
# the repository is en: its edit summaries are in English
SUMMARY = json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8"))["connect_summary"]
MAIN_PAGE_ITEM = "Q4"
DEFAULT_MAIN_PAGE = "Main Page"  # MediaWiki's default, left over on wikis whose main page is localized

COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
# itwiki's {{Album}} also boxes singles: |tipo = Singolo makes the page a song
SUBKINDS = {("it", "Album"): ("tipo", {"singolo": "song"})}
# templates whose |wikidata= names the page's own item (a [[d:Q…]] in the body may name anything)
WIKIDATA_TEMPLATES = {"Interprogetto", "Collegamenti esterni"}
INTERLANGUAGE = re.compile(r"^(%s)\s*:\s*(.+)$" % "|".join(LANGS))
# a Wikipedia page named in an edit summary: [[wikipedia:it:Title]], w:Title, …; no prefix means en.wikipedia
SUMMARY_REF = re.compile(r"(?<![\w:])(?:\[\[\s*:?\s*)?(?:wikipedia|w)\s*:\s*(?:(%s)\s*:\s*)?"
                         r"([^\[\]|#\n]+?)\s*(?:\|[^\]]*)?(?:\]\]|$)" % "|".join(LANGS), re.I)
QID = re.compile(r"^Q\d+$")
# what may precede the page in a whole-page import summary: "Imported from", "Importata da",
# "3 revisions imported from" (Special:Import), … in the wikis' languages; anything else (a section, a
# table "imported from" a page) does not name the article's own subject
IMPORT_PREFIX = re.compile(r"(?:\d+\s+\w+\s+)?(?:import|imported|importat[aoie]|importad[ao]s?|import[ée]e?s?|"
                           r"importiert)\s+(?:from|da|de|desde|depuis|aus|von)\s*:?", re.I)


class Article(NamedTuple):
    lang: str
    title: str
    text: str
    main: bool = False
    comment: str = ""  # edit summary of the first revision (an import may name its Wikipedia source)


class Connect(NamedTuple):
    lang: str
    title: str
    action: str
    wd: str | None
    local: str | None
    reason: str


def load_config(root: Path = ROOT) -> dict:
    imp = json.loads((root / "config" / "infobox_import.json").read_text(encoding="utf-8"))
    exclude = json.loads((root / "config" / "connect_exclude.json").read_text(encoding="utf-8"))
    return {"templates": imp["templates"], "bio": imp["bio"], "kinds": imp["kinds"], "exclude": exclude}


def _template_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw.replace("_", " ")).strip()
    if name.lower().startswith("template:"):
        name = name.split(":", 1)[1].strip()
    return name[:1].upper() + name[1:]


def page_kind(lang: str, text: str, conf: dict) -> str | None:
    """Layout of the first Wikipedia infobox on the page (it: {{Bio}} is a person); None without one."""
    templates = conf["templates"].get(lang, {})
    bio = conf["bio"].get(lang, {}).get("template")
    for template in mwparserfromhell.parse(text).filter_templates():
        name = _template_name(str(template.name))
        if name in templates:
            layout = templates[name]["layout"]
            param, by_value = SUBKINDS.get((lang, name), (None, {}))
            if param and template.has(param):
                value = template.get(param).value.strip_code().strip().lower()
                return by_value.get(value, layout)
            return layout
        if bio and name == bio:
            return "person"
    return None


def claim_ids(entity: dict, prop: str) -> list[str]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            value = snak.get("datavalue", {}).get("value")
            if isinstance(value, dict) and value.get("id"):
                out.append(value["id"])
    return out


def passes(entity: dict, qid: str, allowed) -> bool:
    """The kind filter: an instance (P31) or subclass (P279) of an allowed class, or that class itself."""
    if not allowed:
        return True
    classes = {qid, entity.get("id", qid)} | set(claim_ids(entity, "P31")) | set(claim_ids(entity, "P279"))
    return bool(classes & set(allowed))


def describe(entity: dict, qid: str, lang: str) -> str:
    def text(key):
        values = entity.get(key, {})
        return (values.get(lang) or values.get("en") or {}).get("value")
    words = ", ".join(w for w in (text("labels"), text("descriptions")) if w)
    return f"{qid} ({words})" if words else qid


def summary_page(comment: str) -> tuple[str, str] | None:
    """(site, title) of the one Wikipedia page an import summary names, else None: the summary must name
    exactly one page, and be the bare page or a whole-page import of it (IMPORT_PREFIX, then the page)."""
    refs = list(SUMMARY_REF.finditer(comment or ""))
    if len(refs) != 1:
        return None
    m = refs[0]
    before, after = comment[:m.start()].strip(), comment[m.end():].strip()
    if after not in ("", ".") or (before and not IMPORT_PREFIX.fullmatch(before)):
        return None
    title = m.group(2).strip().replace("_", " ")
    return (f"{(m.group(1) or 'en').lower()}wiki", title[:1].upper() + title[1:]) if title else None


def _interlanguage(text: str) -> list[tuple[str, str]]:
    out = []
    for link in mwparserfromhell.parse(text).filter_wikilinks():
        m = INTERLANGUAGE.match(str(link.title).strip())
        if m:
            out.append((f"{m.group(1)}wiki", m.group(2).strip()))
    return list(dict.fromkeys(out))


def _template_qids(text: str) -> list[str]:
    out = []
    for template in mwparserfromhell.parse(text).filter_templates():
        if _template_name(str(template.name)) in WIKIDATA_TEMPLATES and template.has("wikidata"):
            value = template.get("wikidata").value.strip_code().strip()
            if QID.match(value):
                out.append(value)
    return list(dict.fromkeys(out))


def _search_text(title: str) -> str:
    """The title without a trailing "(…)" disambiguator: search text and new items' labels."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", title) or title


def gather(clients: dict, repo, langs=None) -> tuple[list, list]:
    """(unconnected articles with their text, items connected to any article other than the main page).
    Every wiki's connected items count (a translation joins across all of them), but only the wikis in
    `langs` (default: all) have their unconnected articles fetched."""
    articles, connected = [], []
    for lang, client in clients.items():
        titles = client.articles()
        linked = repo.items_for(lang, titles)
        main = client.main_page()
        for title in titles:
            if title in linked:
                connected.append(linked[title])
                continue
            if langs is not None and lang not in langs:
                continue
            rev = client.revision(title)
            articles.append(Article(lang, title, rev[1] if rev else "", title == main, client.first_comment(title)))
    return articles, sorted(set(connected) - {MAIN_PAGE_ITEM})


def known(repo, wikidata, items: list) -> dict:
    """Wikidata ID -> (local item, {SeminaVerbi site: title}) for local items, from the Wikipedia sitelinks
    the mirror copied (looked up on Wikidata, so a renamed Wikipedia article still resolves)."""
    links = repo.sitelinks(list(items)) if items else {}
    out, todo = {}, set(links)
    for site in WIKIPEDIA:
        pairs = [(item, links[item][site]) for item in sorted(todo) if site in links[item]]
        if not pairs:
            continue
        found = wikidata.by_sitelink(site, [title for _, title in pairs])
        for item, title in pairs:
            wd = found.get(title)
            if wd:
                todo.discard(item)
                out.setdefault(wd, (item, {s: t for s, t in links[item].items() if s in LANGS}))
    return out


def plan(articles: list, repo, wikidata, existing: dict, conf: dict | None = None, forced: dict | None = None,
         create=None) -> list[Connect]:
    conf = conf or load_config()
    forced, create = forced or {}, set(create or ())
    q4 = repo.sitelinks([MAIN_PAGE_ITEM]).get(MAIN_PAGE_ITEM, {})
    rows: list[Connect | None] = [None] * len(articles)
    todo = []
    for n, a in enumerate(articles):
        if a.title in conf["exclude"].get(a.lang, []):
            rows[n] = Connect(a.lang, a.title, "skip-excluded", None, None, "excluded")
        elif a.main:
            current = q4.get(a.lang)
            if current and current != a.title:
                rows[n] = Connect(a.lang, a.title, "main-page", None, None,
                                  f"{MAIN_PAGE_ITEM} already links {a.lang}:{current}")
            else:
                rows[n] = Connect(a.lang, a.title, "main-page", None, MAIN_PAGE_ITEM, "")
        elif a.title == DEFAULT_MAIN_PAGE and q4.get(a.lang) and q4[a.lang] != a.title:
            rows[n] = Connect(a.lang, a.title, "skip-excluded", None, None,
                              f"leftover default page; the main page {a.lang}:{q4[a.lang]} is on {MAIN_PAGE_ITEM}")
        elif (a.lang, a.title) in create:
            rows[n] = Connect(a.lang, a.title, "create", None, None, "author: create")
        else:
            todo.append(n)
    # one batched lookup per Wikipedia: the pages' own titles, their interlanguage links and import summaries
    wanted = {}
    for n in todo:
        a = articles[n]
        if (a.lang, a.title) in forced:
            continue
        pairs = [(f"{a.lang}wiki", a.title), *_interlanguage(a.text)]
        pairs += [p for p in [summary_page(a.comment)] if p]
        for site, title in pairs:
            wanted.setdefault(site, []).append(title)
    hits = {site: wikidata.by_sitelink(site, list(dict.fromkeys(titles))) for site, titles in wanted.items()}
    asked = {(site, title) for site, titles in wanted.items() for title in titles}
    wikidata.prefetch(sorted({q for found in hits.values() for q in found.values()}))
    for n in todo:
        rows[n] = _plan_one(articles[n], repo, wikidata, existing, conf, forced, hits, asked)
    return _one_page_per_item(rows)


def _plan_one(a: Article, repo, wikidata, existing, conf, forced, hits, asked=frozenset()) -> Connect:
    kind = page_kind(a.lang, a.text, conf)
    allowed = conf["kinds"].get(kind) if kind else None
    site = f"{a.lang}wiki"

    def row(action, wd=None, local=None, reason=""):
        return Connect(a.lang, a.title, action, wd, local, reason)

    wd, source, dropped = forced.get((a.lang, a.title)), "author", []
    resolved = getattr(wikidata, "resolved", {})

    def by_link(pairs):
        """{QID: source} for Wikipedia pages (site, title); lookups already made in the batch are reused."""
        found = {}
        for link_site, link_title in pairs:
            answers = hits.get(link_site, {}) if (link_site, link_title) in asked \
                else wikidata.by_sitelink(link_site, [link_title])
            q = answers.get(link_title)
            if q:
                target = resolved.get((link_site, link_title))
                found.setdefault(q, f"{link_site}:{link_title}" + (f" -> {target} (redirect)" if target else ""))
        return found

    sources = [
        lambda: by_link([(site, a.title)]),
        lambda: {q: f"interlanguage link {src}" for q, src in by_link(_interlanguage(a.text)).items()},
        lambda: {q: f"import summary {src}" for q, src in by_link([p for p in [summary_page(a.comment)] if p]).items()},
        lambda: {q: "the page's {{Interprogetto}}/{{Collegamenti esterni}}" for q in _template_qids(a.text)},
    ]
    for source_of in ([] if wd else sources):
        found = source_of()
        wikidata.prefetch(list(found))
        fit = {q: src for q, src in found.items() if passes(wikidata.entity(q), q, allowed)}
        dropped += [q for q in found if q not in fit and q not in dropped]
        if len(fit) > 1:
            return row("author", reason="the page points to several items; candidates: " + ", ".join(
                f"{describe(wikidata.entity(q), q, a.lang)} from {src}" for q, src in fit.items()))
        if fit:
            (wd, source), = fit.items()
            if allowed is None and "(redirect)" in source:
                # a redirect may lead to a broader article; with no infobox to check the kind, ask
                return row("author", reason=f"{source} gives {describe(wikidata.entity(wd), wd, a.lang)}, "
                                            f"no infobox to check its kind; confirm with --wd")
            break
    if not wd:
        found = wikidata.search(_search_text(a.title), a.lang)
        wikidata.prefetch(found)
        fit = [q for q in found if q not in dropped and passes(wikidata.entity(q), q, allowed)]
        not_kind = "; ".join(f"{describe(wikidata.entity(q), q, a.lang)} is not a {kind}" for q in dropped)
        prefix = f"{not_kind}; " if not_kind else ""
        if fit:
            return row("author", reason=prefix + "label search, confirm with --wd; candidates: " + ", ".join(
                describe(wikidata.entity(q), q, a.lang) for q in fit))
        if found or dropped:
            return row("author", reason=f"{prefix}no {kind or 'matching'} item among the search results "
                                        f"({', '.join(found) or '-'}); --wd or --create")
        return row("create", reason=f"no Wikidata item; confirm with --create \"{a.lang}:{a.title}=DESCRIPTION\"")
    entity = wikidata.entity(wd)
    mismatches, local = [], None
    if wd in existing:
        # known() matched the item by one Wikipedia sitelink: its others must not belong to other items
        clash = sitelink_clash(repo, wikidata, existing[wd][0], wd)
        if clash:
            mismatches.append(f"{existing[wd][0]} carries {clash}")
        else:
            local, sv = existing[wd]
    if not local:
        local = _local_copy(repo, entity, wikidata, mismatches)
        if mismatches and not local:
            # --wd reaches this same check, so only repairing the mirror settles it
            return row("author", wd, None, "the mirror's copy is not trusted: " + "; ".join(dict.fromkeys(mismatches))
                       + f" (sitelinks of other Wikidata items than {wd}); repair the mirror first: remove "
                         "from that local item the Wikipedia sitelinks of the Wikidata item it does not "
                         "mirror (its WikibaseImport mapping names the one it does), then rerun")
        sv = {s: t for s, t in repo.sitelinks([local]).get(local, {}).items() if s in LANGS} if local else {}
    if local and sv.get(a.lang) and sv[a.lang] != a.title:
        return row("author", wd, local, f"{local} is already linked to {a.lang}:{sv[a.lang]}")
    if local and sv:
        return row("join", wd, local, "with " + ", ".join(f"{s}:{t}" for s, t in sorted(sv.items())))
    if local:
        return row("local", wd, local, source)
    return row("import", wd, None, source)


def _one_page_per_item(rows: list) -> list:
    """One item, one page per wiki: two pages of one wiki planned onto the same item go to the author."""
    seen = {}
    for r in rows:
        if r.wd and r.action in ("join", "local", "import"):
            seen.setdefault((r.lang, r.wd), []).append(r.title)
    out = []
    for r in rows:
        titles = seen.get((r.lang, r.wd), []) if r.wd and r.action in ("join", "local", "import") else []
        if len(titles) > 1:
            others = ", ".join(f"{r.lang}:{t}" for t in titles if t != r.title)
            r = r._replace(action="author", reason=f"{r.wd} also planned for {others}")
        out.append(r)
    return out


def _line(r: Connect) -> str:
    parts = [f"{r.lang}:{r.title}: {r.action}"]
    if r.wd:
        parts.append(r.wd)
    if r.local:
        parts.append(f"-> {r.local}")
    line = " ".join(parts)
    return f"{line} ({r.reason})" if r.reason else line


def _link(repo, item: str, lang: str, title: str) -> bool:
    """Sets the sitelink after checking nothing changed since the plan; False when it was already set."""
    now = repo.items_for(lang, [title]).get(title)
    if now == item:
        return False
    if now:
        raise SyncError(f"{lang}:{title} is now linked to {now}")
    rev, links = repo.item_sitelinks(item)
    current = links.get(lang)
    if current and current != title:
        raise SyncError(f"{item} is now linked to {lang}:{current}")
    # the revision read with the check: a link set on the item since then is refused, not overwritten
    repo.set_sitelink(item, lang, title, tags=TAG, summary=SUMMARY, baserevid=rev)
    return True


def run(rows: list, repo, apply: bool, import_log: str | None = None, create=(), file_item=None, out=print,
        tools_dir: str = "<tools-dir>") -> int:
    create = dict(create or {})  # (lang, title) -> the author's description for a new item
    for r in rows:
        out(_line(r))
    imports = list(dict.fromkeys(r.wd for r in rows if r.action == "import"))
    counts = {}
    for r in rows:
        counts[r.action] = counts.get(r.action, 0) + 1
    out("plan: " + (", ".join(f"{a} {n}" for a, n in sorted(counts.items())) or "nothing to connect"))
    deletions = [r for r in rows if r.action == "skip-excluded"]

    def deletion_list():
        if deletions:
            out("Pages to delete (never connected):")
            for r in deletions:
                out(f"  {r.lang}:{r.title}" + (f" ({r.reason})" if r.reason != "excluded" else ""))

    if not apply:
        if imports:
            out(f"import command (run on the server before --apply): {import_command(imports, tools_dir)}")
        deletion_list()
        out("(dry run: nothing written)")
        return 0
    if imports and import_log is None:
        out("Run the import on the server from the MediaWiki root, then rerun with --import-log FILE "
            "(its output):")
        out(import_command(imports, tools_dir))
        deletion_list()
        return 3
    mapped, import_failed = parse_import_log(import_log or "")
    linked, failed, file_failed = 0, [], []

    def fail(r, why):
        failed.append((r.lang, r.title, why))
        out(f"{r.lang}:{r.title}: FAILED {why}")

    def file(r, local):
        if not file_item:
            return
        try:
            file_item(local)
        except Exception as exc:  # the sitelink stands; filing is reported and rerun on its own
            file_failed.append((r.lang, r.title, local, str(exc)))
            out(f"{r.lang}:{r.title}: FILE-FAILED {local}: {exc}")

    for r in rows:
        try:
            if r.action == "main-page" and r.local:
                linked += _link(repo, r.local, r.lang, r.title)
                continue
            if r.action in ("join", "local"):
                local = r.local
            elif r.action == "import":
                if r.wd in import_failed:
                    fail(r, f"import {r.wd}: {import_failed[r.wd]}")
                    continue
                local = mapped.get(r.wd)
                if not local:
                    fail(r, f"import {r.wd}: not in the import log")
                    continue
            elif r.action == "create" and (r.lang, r.title) in create:
                now = repo.items_for(r.lang, [r.title]).get(r.title)
                if now:  # linked since the plan (as _link checks before it writes)
                    raise SyncError(f"{r.lang}:{r.title} is now linked to {now}")
                local = repo.new_item({r.lang: _search_text(r.title)}, {r.lang: create[r.lang, r.title]},
                                      {r.lang: r.title}, tags=TAG, summary=SUMMARY)
                out(f"{r.lang}:{r.title}: created {local}")
                linked += 1
                file(r, local)
                continue
            else:
                continue
            linked += _link(repo, local, r.lang, r.title)
            out(f"{r.lang}:{r.title}: linked to {local}")
            file(r, local)
        except SyncError as exc:
            fail(r, str(exc))
    out(f"linked {linked}; file-failed {len(file_failed)}; failed {len(failed)}")
    deletion_list()
    return 1 if failed or file_failed else 0


def decisions(specs: list, articles: list, kind: str, what: str = "an unconnected article") -> dict:
    """CLI author decisions -> {(lang, title): QID} (kind "qid", TITLE=QID, split at the last "=") or
    {(lang, title): description} (kind "description": TITLE::description if the spec has "::", else
    TITLE=description split at the first "=")."""
    unconnected = {(a.lang, a.title) for a in articles}
    out = {}
    for spec in specs:
        if kind == "qid":
            key, sep, qid = spec.rpartition("=")
            if not sep or not key or not QID.match(qid):
                raise ValueError(f"expected TITLE=QID, got {spec!r}")
        else:
            # "TITLE::description" when the title holds a "="; else "TITLE=description" (first "=")
            key, sep, qid = spec.partition("::" if "::" in spec else "=")
            qid = qid.strip()
            if not sep or not key or not qid:
                raise ValueError(f"expected TITLE=description (the new item's description), got {spec!r}")
        lang, title = page_key(key, unconnected, what)
        out[lang, title] = qid
    return out


def main(argv=None, repo=None, clients=None, wikidata=None, classify=None, out=print, file_item=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.connect_items")
    parser.add_argument("--wiki", default=",".join(LANGS))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--import-log", metavar="FILE", help="output of the EnrichItems --import command")
    parser.add_argument("--only", action="append", default=[], metavar="TITLE",
                        help="plan and apply only this unconnected article (repeatable; LANG:TITLE)")
    parser.add_argument("--wd", action="append", default=[], metavar="TITLE=QID",
                        help="the author's Wikidata item for an article (repeatable; LANG:TITLE=QID)")
    parser.add_argument("--create", action="append", default=[], metavar="TITLE=DESCRIPTION",
                        help="create a new local item for an article, with its description in the wiki's "
                             "language (repeatable; LANG:TITLE=DESCRIPTION, split at the first '='; "
                             "LANG:TITLE::DESCRIPTION for a title containing '=')")
    parser.add_argument("--tools-dir", default=os.environ.get("SV_TOOLS_DIR", "<tools-dir>"),
                        help="server directory holding EnrichItems.php (for the printed command)")
    args = parser.parse_args(argv)
    langs = args.wiki.split(",")
    unknown = [lang for lang in langs if lang not in LANGS]
    if unknown:
        parser.error(f"unknown wiki {', '.join(unknown)}; choose from {', '.join(LANGS)}")
    import_log = None
    if args.import_log:
        try:
            import_log = Path(args.import_log).read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"--import-log: {exc}")
    try:
        if clients is None:
            clients = {lang: WikiClient(lang) for lang in LANGS}
            repo = clients["en"]
            if args.apply:
                user, password = os.environ["SV_EDITOR_USER"], os.environ["SV_EDITOR_PASSWORD"]
                for client in clients.values():
                    client.login(user, password)
        wikidata = wikidata or WikidataClient()
        articles, connected = gather(clients, repo, langs)  # all wikis' items, only --wiki's page texts
        articles = [a for a in articles if a.lang in langs]
        what = "an unconnected article"
        try:
            if args.only:
                pages = {(a.lang, a.title) for a in articles}
                chosen = {page_key(key, pages, what) for key in args.only}
                articles = [a for a in articles if (a.lang, a.title) in chosen]
                what = "an unconnected article named by --only"
            forced = decisions(args.wd, articles, "qid", what)
            create = decisions(args.create, articles, "description", what)
        except ValueError as exc:
            parser.error(str(exc))
        rows = plan(articles, repo, wikidata, known(repo, wikidata, connected), forced=forced, create=create)
        if args.apply and file_item is None:
            file_item = _filer(repo, clients, classify, out)
        return run(rows, repo, args.apply, import_log, create, file_item, out, args.tools_dir)
    except (SyncError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _filer(repo, clients, classify, out):
    """file_items.run for one item, with the arguments file_items.main builds."""
    from sync.categories import load
    from tools import file_items, jev

    data = load(ROOT)
    types = json.loads((ROOT / "config" / "infobox_types.json").read_text(encoding="utf-8"))
    props = json.loads((ROOT / "config" / "wikibase.json").read_text(encoding="utf-8"))
    if classify is None:
        holder = {}

        def classify(text):
            if "client" not in holder:
                holder["client"] = jev.make_client()
            return jev.classify(holder["client"], text, data)

    def file_item(item):
        report = file_items.run(repo, clients, data, types, props, True, only_item=item, classify=classify,
                                record=jev.record, out=out)
        if report.exit_code:
            raise SyncError(f"filing {item}: stopped {len(report.stopped)}, unsafe {len(report.unsafe)}, "
                            f"failed {len(report.failed)} (see above)")
    return file_item


if __name__ == "__main__":
    sys.exit(main())
