"""python -m tools.file_items [--wiki en,it,…] [--item Q…] [--apply]

Moves articles' filing from hand-written category lines to their Wikibase item: the item gets one
SV field statement and an SV Gospel theme statement per theme (the union of the themes its pages carry
on all wikis), and each page's hand-written tree category lines give way to {{SV categories}} (or to
the SV infobox it already has), which outputs the same categories from the item. Pages disagreeing on
the field stop that item for the author; a field that does not fit the item's type is re-checked by
Jev. Without --apply nothing is written.
Credentials: SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

from sync.categories import leaf_themes, load, nodes
from sync.pages import LANGS
from sync.wiki import SyncError, WikiClient, repo_client
from tools import jev
from tools.migrate_categories import _line_regex

MODULE_CALL = re.compile(r"\{\{\s*SV[ _](?:infobox(?:[ _](?:work|person))?|categories)\s*(\|(?:[^{}]|\{\{[^{}]*\}\})*)?\}\}")


# wikitext MediaWiki shows instead of running: comments, nowiki, pre and code blocks
INACTIVE = re.compile(r"<!--.*?(?:-->|\Z)|<(nowiki|pre|syntaxhighlight|source|math|code)\b[^>]*?(?:/>|>.*?(?:</\1\s*>|\Z))",
                      re.S | re.I)


def _mask(text: str) -> str:
    """`text` with its inactive spans blanked (same length, newlines kept), so matches keep their positions."""
    return INACTIVE.sub(lambda m: re.sub(r"[^\n]", "\x01", m.group(0)), text)


class PageFacts(NamedTuple):
    lang: str
    title: str
    revid: int
    text: str
    categories: set  # bare names, template-added included


class ItemPlan(NamedTuple):
    item: str
    field: str | None
    themes: list
    add: list       # (prop key, Q-ID): statements to write
    edits: dict     # lang -> (title, new text, base revid)
    stop: str | None
    recheck: bool


def _field_nodes(data):
    all_ = nodes(data)
    keys = [n.key for n in all_]
    return all_[1:keys.index("themes-root")]


def tree_keys(lang: str, names: set, data) -> tuple[set, set]:
    """(field keys, leaf theme keys) among the bare category names `names` on wiki `lang`."""
    fields = {n.names[lang]: n.key for n in _field_nodes(data)}
    themes = {n.names[lang]: n.key for n in leaf_themes(data)}
    return {fields[c] for c in names if c in fields}, {themes[c] for c in names if c in themes}


def fits(field_key: str, layout: str, data, types) -> bool:
    """The field lies in (or under) one of the subtrees `types["fit"][layout]`; layouts without an entry fit anything."""
    allowed = types.get("fit", {}).get(layout)
    if not allowed:
        return True
    parent = {n.key: n.parent_key for n in nodes(data)}
    key = field_key
    while key:
        if key in allowed:
            return True
        key = parent.get(key)
    return False


def calls_module(text: str) -> bool:
    return bool(MODULE_CALL.search(_mask(text)))


def rewrite(text: str, remove: list, namespaces, has_module: bool) -> str:
    """`text` without the hand-written [[Category:…]] lines of `remove` (bare names). {{SV categories}}
    takes the first removed line's place unless the page already calls an SV infobox / SV categories;
    the first sort key found is kept as sortkey=."""
    masked = _mask(text)
    matches = sorted((m for name in remove for m in _line_regex(name, namespaces).finditer(masked)),
                     key=lambda m: m.start())
    if not matches:
        return text
    # the first sort key; a blank one ([[Category:X| ]], first in the category) survives as &#32;,
    # which Module:SV/Infobox turns back into a space (template parameters are trimmed)
    raw = next((m.group(2)[1:] for m in matches if m.group(2) and m.group(2)[1:]), None)
    sortkey = (raw.strip() or "&#32;") if raw else None
    marker = "\x00SVCAT\x00"
    out, pos = [], 0
    for i, m in enumerate(matches):
        if m.start() < pos:
            continue  # overlapping match of another name
        start, end = m.start(), m.end()
        if i > 0 or has_module:
            # drop the whole line: its newline after, or before when it is the last line
            if text[end:end + 1] == "\n":
                end += 1
            elif start > 0 and text[start - 1] == "\n":
                start -= 1
        out.append(text[pos:start])
        if i == 0 and not has_module:
            out.append(marker)
        pos = end
    out.append(text[pos:])
    result = "".join(out)
    if not has_module:
        call = "{{SV categories" + (f"|sortkey={sortkey}" if sortkey else "") + "}}"
        return result.replace(marker, call)
    if sortkey:
        call = MODULE_CALL.search(_mask(result))
        if call and not (call.group(1) and re.search(r"\|\s*sortkey\s*=", call.group(1))):
            # splice into the original text (the match was made on the masked copy)
            end = call.end() - 2
            result = result[:end] + f"|sortkey={sortkey}" + result[end:]
    return result


def add_module_call(text: str, namespaces) -> str:
    """`text` with {{SV categories}} before its first category line, or at the end."""
    ns = "|".join(re.escape(n) for n in sorted(namespaces, key=len, reverse=True))
    first = re.search(r"^\[\[\s*(?i:" + ns + r")\s*:", _mask(text), re.M)
    if first:
        return text[:first.start()] + "{{SV categories}}\n" + text[first.start():]
    return text.rstrip("\n") + "\n\n{{SV categories}}"


def plan_item(item: str, pages: list, existing: dict, layout: str, data, types, namespaces: dict,
              node_item: dict, field_override: str | None = None) -> ItemPlan:
    """What filing `item` on itself takes. `existing`: prop key -> Q-IDs the item already has."""
    per_page = {p.lang: tree_keys(p.lang, p.categories, data) for p in pages}
    page_fields = {lang: f for lang, (f, _) in per_page.items() if f}
    distinct = set().union(*page_fields.values()) if page_fields else set()
    if len(distinct) > 1 and field_override is None:
        detail = ", ".join(f"{lang}={'+'.join(sorted(f))}" for lang, f in sorted(page_fields.items()))
        return ItemPlan(item, None, [], [], {}, f"field disagreement: {detail}", False)
    field = field_override or (distinct.pop() if distinct else None)
    order = [n.key for n in leaf_themes(data)]
    themes = sorted({t for _, ts in per_page.values() for t in ts}, key=order.index)

    have_field = existing.get("sv_field", [])
    if field and have_field and node_item[field] not in have_field:
        by_item = {v: k for k, v in node_item.items()}
        current = ", ".join(by_item.get(q, q) for q in have_field)
        return ItemPlan(item, field, themes, [], {}, f"item already has field {current}; pages say {field}", False)
    add = []
    if field and node_item[field] not in have_field:
        add.append(("sv_field", node_item[field]))
    add += [("sv_theme", node_item[t]) for t in themes if node_item[t] not in existing.get("sv_theme", [])]

    edits = {}
    names_of = {n.key: n.names for n in nodes(data)}
    for p in pages:
        fields, ts = per_page[p.lang]
        remove = [names_of[k][p.lang] for k in sorted(fields | ts)]
        new = rewrite(p.text, remove, namespaces[p.lang], calls_module(p.text))
        if not calls_module(new):
            new = add_module_call(new, namespaces[p.lang])
        if new != p.text:
            edits[p.lang] = (p.title, new, p.revid)
    recheck = (bool(field) and field_override is None and node_item[field] not in have_field
               and not fits(field, layout, data, types))
    return ItemPlan(item, field, themes, add, edits, None, recheck)


# --- execution ---

ROOT = Path(__file__).resolve().parent.parent
TAG = "seminaverbi-mcp"
MAIN_PAGE_ITEM = "Q4"


class Report(NamedTuple):
    planned: int
    added: int
    edited: int
    stopped: list      # (item, reason)
    unsafe: list       # (lang, title, tree categories the preview lost)
    failed: list       # (lang or "repo", title or item, error)
    unconnected: list  # (lang, title)

    @property
    def exit_code(self) -> int:
        return 1 if self.stopped or self.unsafe or self.failed else 0


def layout_for(instance_of: list, types) -> str:
    """Same rule as Module:SV/Infobox layoutFor: the first mapped instance-of value, else work."""
    return next((types["types"][q] for q in instance_of if q in types["types"]), "work")


def _summary(lang: str) -> str:
    return json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))["file_summary"]


def run(repo, clients: dict, data, types, props: dict, apply: bool, only_item: str | None = None,
        classify=None, record=None, out=print, fields: dict | None = None, only_pages: set | None = None) -> Report:
    """`fields`: item -> field key the author decided; it replaces the pages' field and skips Jev.
    `only_pages`: (lang, title) the run may save; the item's other articles still give their facts."""
    author_fields = fields or {}
    node_item = {n.key: None for n in nodes(data)}
    node_item.update(_node_items(data))
    names_of = {n.key: n.names for n in nodes(data)}
    groups, unconnected = {}, []
    namespaces_of = {}
    for lang, client in clients.items():
        titles = client.articles()
        linked = repo.items_for(lang, titles)  # only the repository answers wbgetentities
        for title in titles:
            item = linked.get(title)
            if not item:
                unconnected.append((lang, title))
            elif item != MAIN_PAGE_ITEM and (only_item is None or item == only_item):
                groups.setdefault(item, []).append((lang, title))
    planned = added = edited = 0
    stopped, unsafe, failed = [], [], []
    for item in sorted(groups, key=lambda q: int(q[1:]) if q[1:].isdigit() else q):
        pages, namespaces = [], {}
        for lang, title in groups[item]:
            client = clients[lang]
            rev = client.revision(title)
            if rev is None:  # deleted since it was listed
                failed.append((lang, title, "page missing"))
                out(f"{item}: FAILED {lang}:{title}: page missing")
                continue
            revid, text = rev
            pages.append(PageFacts(lang, title, revid, text, client.page_categories(title)))
            if lang not in namespaces_of:
                namespaces_of[lang] = client.category_namespaces()
            namespaces[lang] = namespaces_of[lang]
        if not pages:
            continue
        existing = {key: repo.item_values(item, props[key]) for key in ("sv_field", "sv_theme")}
        instance = repo.item_values(item, props["instance_of"])
        layout = layout_for(instance, types)
        plan = plan_item(item, pages, existing, layout, data, types, namespaces, node_item,
                         field_override=author_fields.get(item))
        if plan.recheck and plan.stop is None:
            if classify is None:
                plan = plan._replace(stop=f"field {plan.field} does not fit {layout}; Jev not available")
            else:
                typed = next((q for q in instance if q in types["types"]), instance[0] if instance else None)
                label = (repo.label(typed) if typed else None) or layout
                first = min(pages, key=lambda p: LANGS.index(p.lang))
                decision = classify(f"Type: {label}.\n\n{first.text}")
                if apply and record:
                    record(decision._replace(node=item))
                if decision.outcome == "applied":
                    plan = plan_item(item, pages, existing, layout, data, types, namespaces, node_item,
                                     field_override=decision.choice)
                else:
                    plan = plan._replace(stop=f"field for the author: pages say {plan.field}, Jev suggests "
                                              f"{decision.choice} ({decision.outcome}, confidence "
                                              f"{decision.confidence:.2f}) for a {label}")
        if plan.stop:
            stopped.append((item, plan.stop))
            out(f"{item}: STOP {plan.stop}")
            continue
        if not plan.add and not plan.edits:
            continue
        planned += 1
        out(f"{item}: field {plan.field}; themes {', '.join(plan.themes) or '-'}; "
            f"add {len(plan.add)}; edit {', '.join(f'{lang}:{e[0]}' for lang, e in plan.edits.items()) or '-'}")
        if not apply:
            continue
        try:
            for key, value in plan.add:
                repo.add_item_claim(item, props[key], value, tags=TAG, summary=_summary("en"))
                added += 1
        except SyncError as exc:
            failed.append(("repo", item, str(exc)))
            continue
        by_lang = {p.lang: p for p in pages}
        for lang, (title, text, revid) in plan.edits.items():
            if only_pages is not None and (lang, title) not in only_pages:
                continue
            page = by_lang[lang]
            fields, themes = tree_keys(lang, page.categories, data)
            keep = {names_of[k][lang] for k in themes | fields if k == plan.field or k in themes}
            if plan.field:
                keep.add(names_of[plan.field][lang])
            status = guarded_save(clients[lang], title, text, revid, keep, _summary(lang),
                                  lambda message: out(f"{item}: {message}"))
            edited += status.saved
            if status == "unsafe":
                unsafe.append((lang, title, status.detail))
            elif status == "failed":
                failed.append((lang, title, status.detail))
    return Report(planned, added, edited, stopped, unsafe, failed, unconnected)


class SaveStatus(str):
    """"saved", "unsafe" or "failed"; `detail`: the lost tree categories (unsafe) or the error (failed);
    `saved`: an edit was made (an unsafe page may have been saved: a merge found after the save);
    `revid`: the revision the save created, when the API named it."""
    detail = None
    saved = False
    revid = None

    @classmethod
    def of(cls, status: str, detail=None, saved: bool = False, revid: int | None = None) -> "SaveStatus":
        out = cls(status)
        out.detail, out.saved, out.revid = detail, saved, revid
        return out


def guarded_save(client, title: str, text: str, revid: int, keep: set, summary: str, out=print) -> SaveStatus:
    """Saves `text` over revision `revid` of `title` only if its preview keeps every category of `keep`
    and nobody changed the page since `revid`; then verifies the saved revision. Messages go to `out`."""
    lang = client.lang
    try:
        lost = sorted(keep - client.preview_categories(title, text))
    except SyncError as exc:
        out(f"FAILED {lang}:{title}: preview: {exc}")
        return SaveStatus.of("failed", str(exc))
    if lost:
        out(f"UNSAFE {lang}:{title} would lose {', '.join(lost)}; not saved")
        return SaveStatus.of("unsafe", lost)
    now = client.revision(title)
    if now is None or now[0] != revid:
        # MediaWiki would merge a change elsewhere on the page into text nobody previewed
        out(f"FAILED {lang}:{title}: changed since it was read; run again")
        return SaveStatus.of("failed", "changed since it was read; run again")
    try:
        result = client.edit(title, text, summary, tags=TAG, baserevid=revid) or {}
    except SyncError as exc:
        out(f"FAILED {lang}:{title}: {exc}")
        return SaveStatus.of("failed", str(exc))
    try:
        # MediaWiki may have merged a concurrent edit (its oldrevid does not tell): verify every save,
        # on the revision it created when the API names it
        saved = result.get("newrevid")
        now = client.revision_categories(saved) if saved else client.page_categories(title)
    except SyncError as exc:
        out(f"FAILED {lang}:{title}: {exc}")
        return SaveStatus.of("failed", str(exc), saved=True, revid=saved)
    lost = sorted(keep - now)
    if lost:
        out(f"UNSAFE {lang}:{title} saved without {', '.join(lost)} (a concurrent edit merged?); check the page")
        return SaveStatus.of("unsafe", lost, saved=True, revid=saved)
    return SaveStatus.of("saved", saved=True, revid=saved)


def _node_items(data) -> dict:
    out = {}

    def walk(items):
        for d in items:
            if "item" in d:
                out[d["key"]] = d["item"]
            walk(d.get("children", []))
    walk(data["fields"])
    walk(data["themes"])
    return out


def main(argv=None, repo=None, clients=None, classify=None, data=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.file_items")
    parser.add_argument("--wiki", default=",".join(LANGS))
    parser.add_argument("--item")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--field", action="append", default=[], metavar="QID=FIELD",
                        help="the author's field for an item (repeatable); settles a stop")
    args = parser.parse_args(argv)
    fields = {}
    for spec in args.field:
        item, sep, key = spec.partition("=")
        if not sep or not item or not key:
            parser.error(f"--field takes QID=FIELD, got {spec!r}")
        fields[item] = key
    langs = args.wiki.split(",")
    unknown = [lang for lang in langs if lang not in LANGS]
    if unknown:
        parser.error(f"unknown wiki {', '.join(unknown)}; choose from {', '.join(LANGS)}")
    data = data or load(ROOT)
    types = json.loads((ROOT / "config" / "infobox_types.json").read_text(encoding="utf-8"))
    props = json.loads((ROOT / "config" / "wikibase.json").read_text(encoding="utf-8"))
    try:
        if clients is None:
            clients = {lang: WikiClient(lang) for lang in langs}
            repo = repo_client(clients, WikiClient)
            if args.apply:
                user, password = os.environ["SV_EDITOR_USER"], os.environ["SV_EDITOR_PASSWORD"]
                for client in {id(c): c for c in [*clients.values(), repo]}.values():
                    client.login(user, password)
        if classify is None:
            holder = {}

            def classify(text):
                holder.setdefault("client", jev.make_client())
                return jev.classify(holder["client"], text, data)
        unknown_fields = sorted(set(fields.values()) - set(_node_items({'fields': data['fields'], 'themes': []})))
        if unknown_fields:
            parser.error(f"unknown field {', '.join(unknown_fields)}")
        report = run(repo, clients, data, types, props, args.apply, args.item, classify, jev.record,
                     fields=fields)
    except (SyncError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    verb = "" if args.apply else " (dry run: nothing written)"
    print(f"items to file {report.planned}; statements added {report.added}; pages edited {report.edited}{verb}")
    print(f"stopped {len(report.stopped)}; unsafe {len(report.unsafe)}; failed {len(report.failed)}; "
          f"unconnected articles {len(report.unconnected)}")
    for lang, title in report.unconnected:
        print(f"  unconnected: {lang}:{title}")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
