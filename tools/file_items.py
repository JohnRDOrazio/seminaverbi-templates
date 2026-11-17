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

import re
from typing import NamedTuple

from sync.categories import leaf_themes, nodes
from tools.migrate_categories import _line_regex

MODULE_CALL = re.compile(r"\{\{\s*SV[ _](?:infobox(?:[ _](?:work|person))?|categories)\s*(\|[^{}]*)?\}\}")


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
    return bool(MODULE_CALL.search(text))


def rewrite(text: str, remove: list, namespaces, has_module: bool) -> str:
    """`text` without the hand-written [[Category:…]] lines of `remove` (bare names). {{SV categories}}
    takes the first removed line's place unless the page already calls an SV infobox / SV categories;
    the first sort key found is kept as sortkey=."""
    matches = sorted((m for name in remove for m in _line_regex(name, namespaces).finditer(text)),
                     key=lambda m: m.start())
    if not matches:
        return text
    sortkey = next((m.group(2)[1:].strip() for m in matches if m.group(2) and m.group(2)[1:].strip()), None)
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
            elif start > 0 and text[start - 1] == "\n" and (not out or not out[-1].endswith(marker)):
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
        def add_sortkey(m):
            if m.group(1) and re.search(r"\|\s*sortkey\s*=", m.group(1)):
                return m.group(0)
            return m.group(0)[:-2] + f"|sortkey={sortkey}}}}}"
        result = MODULE_CALL.sub(add_sortkey, result, count=1)
    return result


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
        if new != p.text:
            edits[p.lang] = (p.title, new, p.revid)
    recheck = bool(field) and field_override is None and not fits(field, layout, data, types)
    return ItemPlan(item, field, themes, add, edits, None, recheck)
