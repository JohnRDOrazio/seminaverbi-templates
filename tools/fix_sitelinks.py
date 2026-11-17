"""python -m tools.fix_sitelinks [--apply]

One-off repair (2026-11-17). tools.categories linked the category items under '<lang>wiki', which in the
shared sites table is Wikipedia; the SeminaVerbi wikis are 'en'…'pt'. For every item SV editor bot has
edited, this moves each tree category link to the SeminaVerbi site ID, removes the '<lang>wiki' links
the bot added, and restores the Wikipedia links the bot overwrote on items it did not create (their
value before the bot's first edit). Without --apply it only prints the plan.
Credentials: SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple

from sync.categories import load, nodes
from sync.pages import LANGS
from sync.wiki import SyncError, WikiClient

ROOT = Path(__file__).resolve().parent.parent
BOT = "SV editor bot"
TAG = "seminaverbi-mcp"
SUMMARY = "Category sitelinks: SeminaVerbi site IDs (en…pt), not Wikipedia's (enwiki…ptwiki)"
CATEGORY_NS = {"en": "Category", "it": "Categoria", "es": "Categoría", "fr": "Catégorie", "de": "Kategorie",
               "pt": "Categoria"}


class Plan(NamedTuple):
    sitelinks: dict   # wbeditentity 'sitelinks' payload; empty = nothing to do
    conflicts: list   # (lang, the SeminaVerbi link the item has, the tree title): left alone


def plan_item(current: dict[str, str], before: dict[str, str] | None, titles: dict[str, str]) -> Plan:
    """`current`/`before`: site -> title of the item now and before the bot's first edit (None: the bot
    created it). `titles`: lang -> the tree category's title on that wiki."""
    out, conflicts = {}, []
    for lang in LANGS:
        wiki = f"{lang}wiki"
        have = current.get(lang)
        if have is None:
            out[lang] = {"site": lang, "title": titles[lang]}
        elif have != titles[lang]:
            conflicts.append((lang, have, titles[lang]))
        if before is None:
            if wiki in current:
                out[wiki] = {"site": wiki, "title": "", "remove": ""}
        elif current.get(wiki) != before.get(wiki):
            out[wiki] = ({"site": wiki, "title": before[wiki]} if wiki in before
                         else {"site": wiki, "title": "", "remove": ""})
    return Plan(out, conflicts)


def tree_titles(data: dict) -> dict[str, dict[str, str]]:
    """node key -> lang -> category title."""
    return {n.key: {lang: f"{CATEGORY_NS[lang]}:{n.names[lang]}" for lang in LANGS} for n in nodes(data)}


def node_of(sitelinks: dict[str, str], titles: dict[str, dict[str, str]]) -> str | None:
    """The tree node whose title one of the item's links (SeminaVerbi or <lang>wiki) names; None if not
    exactly one."""
    found = {key for key, t in titles.items() for lang in LANGS
             for site in (lang, f"{lang}wiki") if sitelinks.get(site) == t[lang]}
    return found.pop() if len(found) == 1 else None


def bot_items(repo: WikiClient) -> dict[str, dict]:
    """item -> {'created': bool, 'first_parent': revid of the revision before the bot's first edit}."""
    items, cont = {}, {}
    while True:
        data = repo._call("GET", action="query", list="usercontribs", ucuser=BOT, ucnamespace="120",
                          uclimit="500", ucprop="title|ids|flags", **cont)
        for c in repo._dig(data, "query", "usercontribs"):
            q = c["title"].split(":")[-1]
            entry = items.setdefault(q, {"created": False, "first": None, "first_parent": None})
            entry["created"] |= bool(c.get("new"))
            if entry["first"] is None or c["revid"] < entry["first"]:
                entry["first"], entry["first_parent"] = c["revid"], c["parentid"]
        if "continue" not in data:
            return items
        cont = data["continue"]


def sitelinks_of(repo: WikiClient, item: str) -> dict[str, str]:
    entities = repo._dig(repo._call("GET", action="wbgetentities", ids=item, props="sitelinks"), "entities")
    return {s: v["title"] for s, v in repo._dig(entities, item).get("sitelinks", {}).items()}


def sitelinks_at(repo: WikiClient, revid: int) -> dict[str, str]:
    data = repo._call("GET", action="query", prop="revisions", revids=str(revid), rvprop="content", rvslots="main")
    content = json.loads(repo._dig(data, "query", "pages", 0, "revisions", 0, "slots", "main", "content"))
    return {s: v["title"] for s, v in content.get("sitelinks", {}).items()}


def main(argv=None, repo: WikiClient | None = None, data: dict | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.fix_sitelinks")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    data = data or load(ROOT)
    titles = tree_titles(data)
    repo = repo or WikiClient("en")
    if args.apply:
        repo.login(os.environ["SV_EDITOR_USER"], os.environ["SV_EDITOR_PASSWORD"])
    failed, clients = 0, {}
    for item, info in sorted(bot_items(repo).items(), key=lambda kv: int(kv[0][1:])):
        try:
            current = sitelinks_of(repo, item)
            before = None if info["created"] else sitelinks_at(repo, info["first_parent"])
            key = node_of(current, titles) or (node_of(before, titles) if before else None)
            if key is None:
                print(f"{item}: SKIP no single tree category in {current}")
                failed += 1
                continue
            plan = plan_item(current, before, titles[key])
            for lang, have, want in plan.conflicts:
                client = clients.setdefault(lang, WikiClient(lang))
                if not client.exists(have) or client.is_redirect(have):
                    # a stale link to a page since renamed (the clients could not follow the move)
                    plan.sitelinks[lang] = {"site": lang, "title": want}
                    continue
                print(f"{item} ({key}): CONFLICT {lang} is {have}, tree says {want}; left alone")
                failed += 1
            if not plan.sitelinks:
                continue
            print(f"{item} ({key}): " + ", ".join(
                f"-{s}" if "remove" in v else f"{s}={v['title']}" for s, v in plan.sitelinks.items()))
            if args.apply:
                repo._call("POST", action="wbeditentity", id=item, bot="1", tags=TAG, summary=SUMMARY,
                           data=json.dumps({"sitelinks": plan.sitelinks}, ensure_ascii=False),
                           token=repo._token("csrf"))
        except (SyncError, KeyError, ValueError) as e:
            print(f"{item}: FAILED {e}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
