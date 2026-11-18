"""python -m tools.cleanup_articles [--wiki en,it] [--page TITLE]… [--apply]

The mechanical cleanup of the articles (main namespace, no redirects, not the main page), from the old
templates the pages brought from Wikipedia or from before the SV templates (config/cleanup.json):

  Bible quotes  en {{Bible quote}}, it {{Citazione bibbia}} -> {{SV Bible quote}} (the English name, as
                templates.md asks): rif= -> ref=, versione= -> version=, version= dropped when it is the
                wiki's default (config/bible.json), inline= kept. A call with a parameter SV Bible quote
                does not know, a positional call ({{Bibleverse}}), an inline= value the two templates read
                differently, or a reference whose book names are not the wiki's language (config/
                bible_books.json, BibleGet's names) is left as it is and reported.
  leftovers     Wikipedia's page-maintenance templates (short description, date and English variety,
                protection, stub, portal, authority control, sister-project and external-link boxes) are
                removed with their line; a heading the removal leaves with an empty section goes too.
                Calls to templates that do not exist on the wiki are reported, not removed.
  references    {{Reflist}}, {{References}}, <references/> -> {{SV references}} (which writes its own
                heading: the list's own heading goes when the section holds only the list); a page that
                already has SV references just loses the old list. List-defined references (refs=,
                <references>…</references>), several lists, or a list whose section holds more are left
                and reported.

Nothing else changes. The dry run (default) prints each page's unified diff and what was left and why.
Every changed page is parsed old and new (action=parse): a page whose new text has a red link, a missing
template or an error/tracking category the old one did not, or more error messages, is refused. --apply
saves the others as tools.file_items does (base revision, a conflict skips the page, the tree categories
kept), bot flag, then checks that the saved revision's text is the text sent.
Credentials (with --apply): SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import mwparserfromhell
import requests
from mwparserfromhell.nodes import Comment, Heading, Tag, Template, Text, Wikilink

from sync.categories import load
from sync.pages import LANGS
from sync.wiki import SyncError, WikiClient
from tools.file_items import guarded_save
from tools.imports import page_key
from tools.switch_infobox import tree_names

ROOT = Path(__file__).resolve().parent.parent
# one page's API failure (a MediaWiki error, a timeout) fails that page, not the run
PAGE_ERRORS = (SyncError, requests.RequestException)
TAG = "seminaverbi-mcp"
BULLETS = {"*", "#", ":", ";"}
CATEGORY_NS = {"category", "categoria", "categoría", "catégorie", "kategorie"}
MAGIC = ("DEFAULTSORT:", "DISPLAYTITLE:")
ERROR_ELEMENT = re.compile(r'<\w+[^>]*?\bclass="([^"]*)"[^>]*>([^<]*)')
ERROR_CLASSES = ("error", "bibleQuoteRefBroken")
BOOK = re.compile(r"\s*((?:[1-4]\s*)?[^\W\d_][^\d]*)")
CONTINUATION = re.compile(r"\s*\d(?:[\d\s:,.\-–—]|(?<=\d)[a-c])*")  # "4", "13:10", "5:1-3,7a"


def normal(name: str) -> str:
    """A template name as MediaWiki matches it: underscores as spaces, first letter capital, no Template:."""
    name = re.sub(r"[\s_]+", " ", re.sub(r"<!--.*?(?:-->|\Z)", "", str(name), flags=re.S)).strip()
    if name.casefold().startswith("template:"):
        name = name[len("template:"):].strip()
    return name[:1].upper() + name[1:]


def _book_key(book: str) -> str:
    return re.sub(r"[\s.]", "", book).casefold()


def load_config(root: Path = ROOT) -> dict:
    root = Path(root)

    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))
    conf = read("config/cleanup.json")
    conf["defaults"] = read("config/bible.json")["defaults"]
    books = read("config/bible_books.json")
    conf["books"] = {lang: {_book_key(f) for book in books[lang] for f in book} for lang in LANGS}
    aliases = read("config/aliases.json")
    target = conf["references"]["target"]
    conf["sv_references"] = {lang: {target} | {normal(a) for a, t in aliases.get(lang, {}).items()
                                               if normal(t) == target} for lang in LANGS}
    conf["messages"] = {lang: read(f"i18n/{lang}.json") for lang in LANGS}
    return conf


class Cleanup(NamedTuple):
    text: str
    converted: int        # Bible quote calls converted
    removed: list         # names of the removed templates, in page order
    references: str | None  # "converted", "removed" (the page had SV references) or None
    reported: list        # what was left, and why


def _short(node) -> str:
    s = re.sub(r"\s+", " ", str(node)).strip()
    return s if len(s) <= 90 else s[:89] + "…"


def _index(nodes, node) -> int:
    return next(i for i, n in enumerate(nodes) if n is node)


def _drop(code, node) -> None:
    del code.nodes[_index(code.nodes, node)]


def _blank_text(node) -> bool:
    return isinstance(node, Text) and node.value.strip() == ""


def _invisible(node) -> bool:
    """Whitespace, comments, category lines, sort keys: nothing a reader sees in a section."""
    if _blank_text(node) or isinstance(node, Comment):
        return True
    if isinstance(node, Wikilink):
        title = str(node.title).strip()
        return (":" in title and not title.startswith(":")
                and title.split(":", 1)[0].strip().casefold() in CATEGORY_NS)
    if isinstance(node, Template):
        name = str(node.name).strip()
        return name.upper().startswith(MAGIC) or normal(name) == "SV categories"
    return False


# --- 1. Bible quotes ---

def _book_problem(ref: str, lang: str, conf: dict) -> str | None:
    books = conf["books"]
    first = True
    for segment in ref.split(";"):
        m = BOOK.match(segment)
        if not m:
            if first:
                return f"no book in ref {ref!r}"
            if not CONTINUATION.fullmatch(segment):
                return f"cannot read {segment.strip()!r} in ref {ref!r}"
            continue  # a chapter of the book before
        first = False
        book = m.group(1).strip().rstrip(".").strip()
        key = _book_key(book)
        if key in books[lang]:
            continue
        others = [other for other in LANGS if other != lang and key in books[other]]
        if others:
            return f"book {book!r} is not {lang} but {', '.join(others)}"
        return f"unknown book {book!r}"
    return None


def _bible_problem(tpl, lang: str, conf: dict) -> str | None:
    """Why `tpl` cannot become SV Bible quote, or None (then it is converted in place)."""
    bq = conf["bible_quote"]
    params = bq["wikis"][lang]["params"]
    seen = {}
    for param in tpl.params:
        key = str(param.name).strip()
        target = params.get(key) if param.showkey else None
        if target is None:
            return f"unknown parameter {key}"
        if target in seen:
            return f"both {seen[target]}= and {key}="
        seen[target] = key
    if "ref" not in seen:
        return "no ref"
    value = tpl.get(seen["ref"]).value
    if any(not isinstance(n, Text) for n in value.nodes):
        return "ref holds markup"
    ref = str(value).strip()
    if not ref:
        return "empty ref"
    if "inline" in seen:
        inline = str(tpl.get(seen["inline"]).value).strip()
        if inline not in bq["inline_values"]:
            return f"inline={inline} (the templates read it differently)"
    problem = _book_problem(ref, lang, conf)
    if problem:
        return problem
    return None


def _rename(text: str, new: str) -> str:
    m = re.match(r"(\s*)(.*?)(\s*)$", text, re.S)
    return m.group(1) + new + m.group(3)


def _convert_bible(tpl, lang: str, conf: dict) -> None:
    params = conf["bible_quote"]["wikis"][lang]["params"]
    for param in list(tpl.params):
        key = str(param.name).strip()
        target = params[key]
        if target == "version" and str(param.value).strip() in ("", conf["defaults"][lang]):
            tpl.remove(param)
        elif target != key:
            param.name = _rename(str(param.name), target)
    tpl.name = _rename(str(tpl.name), conf["bible_quote"]["target"])


def _bible_quotes(code, lang: str, conf: dict, reported: list) -> int:
    bq = conf["bible_quote"]
    wiki = bq["wikis"].get(lang)
    old = {normal(n) for n in wiki["templates"]} if wiki else set()
    report_only = {normal(n) for n in bq.get("report", [])}
    converted = 0
    for tpl in code.filter_templates(recursive=True):
        name = normal(tpl.name)
        if name in report_only:
            reported.append(f"Bible quote left: {_short(tpl)} ({name} is not converted; quote it with SV Bible quote)")
        elif name in old:
            problem = _bible_problem(tpl, lang, conf)
            if problem:
                reported.append(f"Bible quote left: {_short(tpl)} ({problem})")
            else:
                _convert_bible(tpl, lang, conf)
                converted += 1
    return converted


# --- 2. Wikipedia leftovers ---

def _removable(name: str, lang: str, conf: dict) -> bool:
    rules = conf["remove"]
    names = {normal(n) for n in rules["names"]["*"] + rules["names"].get(lang, [])}
    return (name in names or any(name.startswith(p) for p in rules["prefixes"])
            or any(name.casefold().endswith(s.casefold()) for s in rules["suffixes"]))


def _remove_line(code, node, top: bool) -> None:
    """Removes `node`; when it is alone on its line (but for list bullets), the whole line goes."""
    nodes = code.nodes
    i = _index(nodes, node)
    prefix, cut, at_start = [], None, False
    k = i - 1
    while k >= 0:
        n = nodes[k]
        if isinstance(n, Text):
            nl = n.value.rfind("\n")
            if nl >= 0:
                at_start = n.value[nl + 1:].strip(" \t") == ""
                cut = (n, n.value[:nl + 1])
                break
            if n.value.strip(" \t") == "":
                prefix.append(n)
                k -= 1
                continue
            break
        if isinstance(n, Tag) and n.wiki_markup in BULLETS:
            prefix.append(n)
            k -= 1
            continue
        break
    else:
        at_start = top
    at_end, after = False, None
    if i + 1 >= len(nodes):
        at_end = top
    elif isinstance(nodes[i + 1], Text):
        rest = nodes[i + 1].value.lstrip(" \t")
        if rest.startswith("\n"):
            at_end, after = True, (nodes[i + 1], rest[1:])
        elif rest == "" and i + 2 >= len(nodes) and top:
            at_end, after = True, (nodes[i + 1], "")
    if at_start and at_end:
        if cut:
            cut[0].value = cut[1]
        for n in prefix:
            _drop(code, n)
        if after:
            after[0].value = after[1]
    _drop(code, node)


def _section_blank(code, heading) -> bool:
    """The heading's own text (up to the next heading) shows nothing, and it has no subsection."""
    nodes = code.nodes
    for n in nodes[_index(nodes, heading) + 1:]:
        if isinstance(n, Heading):
            return n.level <= heading.level
        if not _invisible(n):
            return False
    return True


def _drop_heading(code, heading) -> None:
    """Removes `heading` and the blank text after it."""
    i = _index(code.nodes, heading)
    del code.nodes[i]
    while i < len(code.nodes) and _blank_text(code.nodes[i]):
        del code.nodes[i]


def _containers(code, top=True):
    yield code, top
    for node in code.nodes:
        if isinstance(node, Tag) and node.contents is not None and str(node.tag).strip().lower() != "ref":
            yield from _containers(node.contents, False)


def _leftovers(code, lang: str, conf: dict, reported: list) -> list:
    headings = [n for n in code.nodes if isinstance(n, Heading)]
    blank_before = {id(h) for h in headings if _section_blank(code, h)}
    removed = []
    for container, top in list(_containers(code)):
        for node in list(container.nodes):
            if isinstance(node, Template) and _removable(normal(node.name), lang, conf):
                removed.append(normal(node.name))
                _remove_line(container, node, top)
    for tpl in code.filter_templates(recursive=True):
        if _removable(normal(tpl.name), lang, conf):
            reported.append(f"Wikipedia template left inside other markup: {_short(tpl)}")
    if removed:
        for h in headings:
            if id(h) not in blank_before and _section_blank(code, h):
                _drop_heading(code, h)
    return removed


# --- 3. references ---

def _ref_lists(code, lang: str, conf: dict):
    old_names = {normal(n) for n in conf["references"]["templates"]}
    sv_names = conf["sv_references"][lang]
    old, sv = [], []
    for node in code.filter(recursive=True, forcetype=(Template, Tag)):
        if isinstance(node, Template):
            name = normal(node.name)
            if name in sv_names:
                sv.append(node)
            elif name in old_names:
                old.append(node)
        elif str(node.tag).strip().lower() == "references":
            old.append(node)
    return old, sv


def _list_problem(node, conf: dict) -> str | None:
    rc = conf["references"]
    if isinstance(node, Template):
        for param in node.params:
            key = str(param.name).strip()
            if key not in rc["keep_params"] and key not in rc["drop_params"]:
                return f"parameter {key}=" + (" (list-defined references)" if key == "refs" else "")
        return None
    if node.contents is not None and str(node.contents).strip():
        return "list-defined references"
    for attr in node.attributes:
        key = str(attr.name).strip().lower()
        if key not in rc["keep_attributes"] and key not in rc["drop_attributes"]:
            return f"attribute {key}"
    return None


def _group(node) -> str | None:
    if isinstance(node, Template):
        value = str(node.get("group").value).strip() if node.has("group") else ""
    else:
        attr = next((a for a in node.attributes if str(a.name).strip().lower() == "group"), None)
        value = str(attr.value).strip() if attr is not None and attr.value is not None else ""
    return value or None


def _context(code, node):
    """(heading of the node's section or None, only blank text between the two, nothing visible after it in
    the section)."""
    nodes = code.nodes
    i = _index(nodes, node)
    heading, direct = None, True
    for n in reversed(nodes[:i]):
        if isinstance(n, Heading):
            heading = n
            break
        if not _blank_text(n):
            direct = False
    alone_after = True
    for n in nodes[i + 1:]:
        if isinstance(n, Heading):
            alone_after = heading is None or n.level <= heading.level
            break
        if not _invisible(n):
            alone_after = False
            break
    return heading, direct and heading is not None, alone_after


def _references(code, lang: str, conf: dict, reported: list) -> str | None:
    old, sv = _ref_lists(code, lang, conf)
    if not old:
        return None
    for node in old:
        problem = _list_problem(node, conf)
        if problem:
            reported.append(f"references list left: {_short(node)} ({problem})")
            return None
    if len(old) + len(sv) > 1 and not (len(old) == 1 and len(sv) == 1):
        reported.append(f"references lists left: the page has {len(old) + len(sv)} lists")
        return None
    node = old[0]
    if sv and _group(node) != _group(sv[0]):
        reported.append(f"references list left: {_short(node)} (its group differs from the SV references')")
        return None
    if not any(n is node for n in code.nodes):
        reported.append(f"references list left: {_short(node)} (inside other markup)")
        return None
    heading, direct, alone = _context(code, node)
    ref_headings = {h.casefold() for h in conf["references"]["headings"].get(lang, [])}
    if sv and not any(n is sv[0] for n in code.nodes):
        reported.append(f"references list left: {_short(node)} (the SV references call is inside other markup)")
        return None
    if sv:
        if direct and alone:
            _drop_heading_through(code, heading, node)
        else:
            _remove_line(code, node, True)
        return "removed"
    group = _group(node)
    if group and re.search(r"[|{}\[\]]", group):
        reported.append(f"references list left: {_short(node)} (group {group!r} cannot be one parameter)")
        return None
    call = "{{" + conf["references"]["target"] + (f"|group={group}" if group else "") + "}}"
    if direct:
        if heading.level != 2:
            reported.append(f"references list left: {_short(node)} (under a level-{heading.level} heading)")
            return None
        if not alone:
            reported.append(f"references list left: {_short(node)} (its section holds more than the list)")
            return None
        i = _index(code.nodes, heading)
        while code.nodes[i + 1] is not node:
            del code.nodes[i + 1]
        del code.nodes[i + 1]
        code.nodes[i] = Text(call)
        return "converted"
    if heading is not None and str(heading.title).strip().casefold() in ref_headings:
        reported.append(f"references list left: {_short(node)} (its section {str(heading.title).strip()!r} "
                        "holds more than the list)")
        return None
    if not alone:
        reported.append(f"references list left: {_short(node)} (text follows it in its section)")
        return None
    following = _next_heading(code, node)
    if following is not None and following.level > 2:
        reported.append(f"references list left: {_short(node)} (a level-{following.level} heading follows it, "
                        "which the new References heading would take over)")
        return None
    code.nodes[_index(code.nodes, node)] = Text(call)
    return "converted"


def _next_heading(code, node):
    """The first heading after `node` among the top-level nodes, or None."""
    return next((n for n in code.nodes[_index(code.nodes, node) + 1:] if isinstance(n, Heading)), None)


def _drop_heading_through(code, heading, node) -> None:
    """Removes `heading`, the blank text up to `node`, `node`, and the blank text after it."""
    i = _index(code.nodes, heading)
    while code.nodes[i] is not node:
        del code.nodes[i]
    del code.nodes[i]
    while i < len(code.nodes) and _blank_text(code.nodes[i]):
        del code.nodes[i]


# --- the page ---

def clean(text: str, lang: str, conf: dict, missing: frozenset = frozenset()) -> Cleanup:
    """`missing`: names of the templates that do not exist on the wiki (their calls are reported)."""
    code = mwparserfromhell.parse(text)
    reported = []
    removed = _leftovers(code, lang, conf, reported)
    converted = _bible_quotes(code, lang, conf, reported)
    references = _references(code, lang, conf, reported)
    missing = {normal(n) for n in missing}
    calls = {}
    for tpl in code.filter_templates(recursive=True):
        name = normal(tpl.name)
        if name in missing:
            calls[name] = calls.get(name, 0) + 1
    for name in sorted(calls):
        n = calls[name]
        reported.append(f"missing template {{{{{name}}}}} ({n} call{'s' if n > 1 else ''}) left")
    return Cleanup(str(code), converted, removed, references, reported)


# --- checks on the rendered page ---

class Checks(NamedTuple):
    red_links: frozenset
    missing_templates: frozenset
    tracking: frozenset    # hidden categories and error categories
    error_marks: Counter   # (class, text) of each error message in the HTML (script, cite, Bible quote)


def checks(parsed: dict, conf: dict) -> Checks:
    words = [w.casefold() for w in conf["tracking_words"]]
    red = frozenset(link["title"] for link in parsed.get("links", []) if not link.get("exists"))
    missing = frozenset(t["title"].split(":", 1)[-1] for t in parsed.get("templates", []) if not t.get("exists"))
    tracking = set()
    for c in parsed.get("categories", []):
        name = c["category"].replace("_", " ")
        if c.get("hidden") or any(w in name.casefold() for w in words):
            tracking.add(name)
    marks = Counter()
    for classes, shown in ERROR_ELEMENT.findall(parsed.get("text", "")):
        kind = next((c for c in ERROR_CLASSES if c in classes.split()), None)
        if kind:
            marks[kind, re.sub(r"\s+", " ", shown).strip()[:100]] += 1
    return Checks(red, missing, frozenset(tracking), marks)


def regressions(old: Checks, new: Checks) -> list:
    out = []
    if new.red_links - old.red_links:
        out.append("new red links: " + ", ".join(sorted(new.red_links - old.red_links)))
    if new.missing_templates - old.missing_templates:
        out.append("new missing templates: " + ", ".join(sorted(new.missing_templates - old.missing_templates)))
    if new.tracking - old.tracking:
        out.append("new error/tracking categories: " + ", ".join(sorted(new.tracking - old.tracking)))
    added = new.error_marks - old.error_marks  # the messages the new text shows more often
    if added:
        out.append("new error messages: " + ", ".join(f"{kind} {text!r}" + (f" ×{n}" if n > 1 else "")
                                                       for (kind, text), n in sorted(added.items())))
    return out


# --- execution ---

class Report:
    def __init__(self):
        self.pages = self.changed = self.saved = 0
        self.converted = self.removed = self.references = self.reported = 0
        self.refused, self.unsafe, self.failed = [], [], []  # (lang, title, detail)
        self.stopped = False  # a saved text differed from the one sent: someone else is editing

    @property
    def exit_code(self) -> int:
        return 1 if self.refused or self.unsafe or self.failed else 0


def summary(lang: str, result: Cleanup, conf: dict) -> str:
    msgs = conf["messages"].get(lang) or conf["messages"]["en"]
    parts = []
    if result.converted:
        parts.append(msgs["cleanup_bible"].replace("$1", str(result.converted)))
    if result.removed:
        parts.append(msgs["cleanup_removed"].replace("$1", ", ".join(dict.fromkeys(result.removed))))
    if result.references:
        parts.append(msgs["cleanup_references"])
    return "; ".join(parts)


def _missing_templates(parsed: dict) -> frozenset:
    return frozenset(t["title"].split(":", 1)[-1] for t in parsed.get("templates", [])
                     if not t.get("exists") and t.get("ns") == 10)


def run(clients: dict, conf: dict, apply: bool, pages: set | None = None, tree: dict | None = None,
        out=print) -> Report:
    """`pages`: (lang, title) to look at (default: every article); `tree`: lang -> the tree's category
    names, which every save must keep."""
    tree = tree or {}
    report = Report()
    for lang, client in clients.items():
        main_page = client.main_page()
        titles = client.articles()
        if pages is not None:
            for gone in sorted(t for lg, t in pages if lg == lang and t not in set(titles)):
                report.failed.append((lang, gone, "no longer an article"))
                out(f"FAILED {lang}:{gone}: no longer an article")
        for title in titles:
            if title == main_page or (pages is not None and (lang, title) not in pages):
                continue
            report.pages += 1
            _page(client, lang, title, conf, apply, tree.get(lang, set()), report, out)
            if report.stopped:
                out("stopped: a saved text differs from the one previewed (a concurrent edit was merged?); "
                    "no further writes")
                return report
    return report


def _page(client, lang, title, conf, apply, tree, report, out) -> None:
    where = f"{lang}:{title}"
    try:
        rev = client.revision(title)
        if rev is None:
            report.failed.append((lang, title, "page missing"))
            out(f"FAILED {where}: page missing")
            return
        revid, text = rev
        old_parsed = client.parse(title, text)
    except PAGE_ERRORS as exc:
        report.failed.append((lang, title, str(exc)))
        out(f"FAILED {where}: {exc}")
        return
    result = clean(text, lang, conf, _missing_templates(old_parsed))
    new = result.text.rstrip()  # MediaWiki stores the text right-trimmed: the saved text must equal it
    changed = new != text.rstrip()
    if not changed and not result.reported:
        return
    out(f"== {where}")
    for item in result.reported:
        out(f"  left: {item}")
    report.reported += len(result.reported)
    if not changed:
        return
    report.changed += 1
    report.converted += result.converted
    report.removed += len(result.removed)
    report.references += bool(result.references)
    out(f"  Bible quotes {result.converted}; removed {', '.join(result.removed) or '-'}; "
        f"references {result.references or '-'}")
    for line in difflib.unified_diff(text.splitlines(), new.splitlines(), f"{where} (rev {revid})", "cleaned",
                                     lineterm="", n=1):
        out(line)
    try:
        problems = regressions(checks(old_parsed, conf), checks(client.parse(title, new), conf))
    except PAGE_ERRORS as exc:
        report.failed.append((lang, title, f"preview: {exc}"))
        out(f"FAILED {where}: preview: {exc}")
        return
    if problems:
        detail = "; ".join(problems)
        report.refused.append((lang, title, detail))
        out(f"REFUSED {where}: {detail}; not saved")
        return
    if not apply:
        return
    keep = {c["category"].replace("_", " ") for c in old_parsed.get("categories", [])} & set(tree)
    try:
        status = guarded_save(client, title, new, revid, keep, summary(lang, result, conf), lambda m: out(f"  {m}"))
    except PAGE_ERRORS as exc:  # a timeout: the save may or may not have happened
        report.failed.append((lang, title, f"save: {exc}; check the page"))
        out(f"FAILED {where}: save: {exc}; check the page")
        return
    if status != "saved":
        (report.unsafe if status == "unsafe" else report.failed).append((lang, title, status.detail))
        if status.saved and status.revid:  # saved, then a check failed: was a concurrent edit merged?
            _verify(client, lang, title, status.revid, new, report, out)
        return
    if not status.revid:
        out(f"  {where}: the edit made no new revision (the page already had this text?); not counted")
        return
    if _verify(client, lang, title, status.revid, new, report, out):
        report.saved += 1
        out(f"  SAVED {where} (rev {status.revid})")


def _verify(client, lang, title, revid, new, report, out) -> bool:
    """The text of the saved revision `revid` is `new`. A mismatch (a concurrent edit merged into the save)
    stops the run's further writes."""
    where = f"{lang}:{title}"
    try:
        saved = client.revision_text(revid)
    except PAGE_ERRORS as exc:
        report.failed.append((lang, title, f"saved, but not verified: {exc}"))
        out(f"FAILED {where}: saved, but not verified: {exc}")
        return False
    if saved != new:
        report.failed.append((lang, title, f"saved text (rev {revid}) differs from the text sent; check it"))
        out(f"FAILED {where}: saved text (rev {revid}) differs from the text sent; check it")
        report.stopped = True
        return False
    return True


def main(argv=None, clients=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.cleanup_articles")
    parser.add_argument("--wiki", default=",".join(LANGS))
    parser.add_argument("--page", action="append", default=[], metavar="TITLE",
                        help="only this article (repeatable; LANG:TITLE when the title is on several wikis)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    langs = args.wiki.split(",")
    unknown = [lang for lang in langs if lang not in LANGS]
    if unknown:
        parser.error(f"unknown wiki {', '.join(unknown)}; choose from {', '.join(LANGS)}")
    user, password = os.environ.get("SV_EDITOR_USER"), os.environ.get("SV_EDITOR_PASSWORD")
    if clients is None and args.apply and not (user and password):
        print("ERROR: --apply needs SV_EDITOR_USER and SV_EDITOR_PASSWORD (a bot password)", file=sys.stderr)
        return 1
    try:
        if clients is None:
            clients = {lang: WikiClient(lang) for lang in langs}
            if args.apply:
                for client in clients.values():
                    client.login(user, password)
        only = None
        if args.page:
            pairs = [(lang, t) for lang, client in clients.items() for t in client.articles()]
            try:
                only = {page_key(spec, pairs, "an article") for spec in args.page}
            except ValueError as exc:
                parser.error(str(exc))
            for lang, title in sorted(only):
                if title == clients[lang].main_page():
                    parser.error(f"{lang}:{title} is the main page, which the cleanup leaves alone")
        report = run(clients, load_config(), args.apply, only, tree_names(load(ROOT)), out)
    except (SyncError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    verb = "" if args.apply else " (dry run: nothing written)"
    out(f"pages {report.pages}; to change {report.changed}; saved {report.saved}{verb}")
    out(f"Bible quotes converted {report.converted}; Wikipedia templates removed {report.removed}; "
        f"reference lists {report.references}; left and reported {report.reported}")
    out(f"refused {len(report.refused)}; unsafe {len(report.unsafe)}; failed {len(report.failed)}")
    for kind, items in (("refused", report.refused), ("unsafe", report.unsafe), ("failed", report.failed)):
        for lang, title, detail in items:
            out(f"  {kind}: {lang}:{title}: {detail}")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
