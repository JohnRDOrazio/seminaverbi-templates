"""python -m tools.categories check --wiki <code> [--create] [--dry-run]

Compares a wiki's category pages with config/categories.json. With --create it creates the missing
ones (never touching an existing page) and attaches every tree category page on that wiki to the
Wikibase item of its counterparts on the other wikis, creating the item when none has one.
Credentials: SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NamedTuple

from sync.categories import load, nodes
from sync.pages import LANGS
from sync.wiki import SyncError, WikiClient

ROOT = Path(__file__).resolve().parent.parent
TAG = "seminaverbi-mcp"
SUMMARY = "Category from the seminaverbi-templates category tree"
ITEM_DESCRIPTION = {
    "en": "Semina Verbi category", "it": "categoria di Semina Verbi", "es": "categoría de Semina Verbi",
    "fr": "catégorie de Semina Verbi", "de": "Semina-Verbi-Kategorie", "pt": "categoria de Semina Verbi",
}


class Report(NamedTuple):
    missing: list        # node keys with no page
    redirect: list       # node keys whose page is a redirect
    wrong_parent: list   # (node key, the page's actual categories)
    untracked: list      # categories used by articles that are not in the tree (information only)


class CreateResult(NamedTuple):
    report: Report
    created: list        # titles created (or that would be, on a dry run)
    linked: list         # (title, item) sitelinks set or items created
    conflicts: list      # (node key, the distinct items its pages are attached to)
    failed: list         # (title, error)
    taken: list          # (node key, item, page it already links on this wiki): left alone


def _site(lang: str) -> str:
    return f"{lang}wiki"


def _title(name: str) -> str:
    return "Category:" + name


def check(lang: str, data: dict, client) -> Report:
    tree = nodes(data)
    by_key = {n.key: n for n in tree}
    info = client.page_info([_title(n.names[lang]) for n in tree])
    missing, redirect, wrong = [], [], []
    for n in tree:
        page = info.get(_title(n.names[lang]))
        if page is None or not page.exists:
            missing.append(n.key)
        elif page.redirect:
            redirect.append(n.key)
        elif n.parent_key and by_key[n.parent_key].names[lang] not in page.categories:
            wrong.append((n.key, list(page.categories)))
    tree_names = {n.names[lang] for n in tree}
    untracked = sorted(client.article_categories() - tree_names)
    return Report(missing, redirect, wrong, untracked)


def page_text(node, parent, lang: str) -> str:
    if parent is None:
        return "{{SV category tree}}"
    link = f"[[Category:{parent.names[lang]}]]"
    definition = node.definition.get(lang) or node.definition.get("en")
    return f"{definition}\n\n{link}" if definition else link


def create(lang: str, data: dict, client, repo, dry_run: bool, replace_missing: bool = False) -> CreateResult:
    """replace_missing: an item that already links this wiki to a page that does not exist here (e.g. a
    Wikipedia title imported from Wikidata) gets that link replaced; a link to a real page never is."""
    report = check(lang, data, client)
    tree = nodes(data)
    by_key = {n.key: n for n in tree}
    created, linked, conflicts, failed, taken = [], [], [], [], []
    missing = set(report.missing)
    for n in tree:
        if n.key not in missing:
            continue
        title = _title(n.names[lang])
        parent = by_key[n.parent_key] if n.parent_key else None
        if not dry_run:
            try:
                client.edit(title, page_text(n, parent, lang), SUMMARY, tags=TAG, createonly=True)
            except (SyncError, OSError) as exc:
                failed.append((title, str(exc)))
                continue
        created.append(title)

    skip = set(report.redirect) | {t for t, _ in failed}
    for n in tree:
        title = _title(n.names[lang])
        if n.key in skip or title in skip or (n.key in missing and title not in created):
            continue
        try:
            own = repo.item_for(_site(lang), title)
            others = {repo.item_for(_site(other), _title(n.names[other])) for other in LANGS if other != lang}
            distinct = sorted(({own} | others) - {None})
            if len(distinct) > 1:
                conflicts.append((n.key, distinct))
            elif own:
                continue
            elif (distinct and (current := repo.sitelink(distinct[0], _site(lang))) not in (None, title)
                  and not (replace_missing and client.page_info([current]).get(current) is None)):
                # wbsetsitelink would silently move the item's existing link for this wiki
                taken.append((n.key, distinct[0], current))
            elif dry_run:
                linked.append((title, distinct[0] if distinct else "(new item)"))
            elif distinct:
                repo.set_sitelink(distinct[0], _site(lang), title, tags=TAG)
                linked.append((title, distinct[0]))
            else:
                item = repo.new_item(dict(n.names), dict(ITEM_DESCRIPTION), {_site(lang): title}, tags=TAG)
                linked.append((title, item))
        except (SyncError, OSError) as exc:
            failed.append((title, str(exc)))
    return CreateResult(report, created, linked, conflicts, failed, taken)


def _print_report(lang: str, data: dict, report: Report) -> None:
    by_key = {n.key: n for n in nodes(data)}

    def name(key):
        return by_key[key].names[lang]

    for key in report.missing:
        print(f"[{lang}] missing: Category:{name(key)}")
    for key in report.redirect:
        print(f"[{lang}] redirect (not touched): Category:{name(key)}")
    for key, actual in report.wrong_parent:
        print(f"[{lang}] wrong parent: Category:{name(key)} is in {actual or 'no category'}")
    if report.untracked:
        print(f"[{lang}] used by articles, not in the tree: {', '.join(report.untracked)}")


def main(argv=None, client_factory=WikiClient, data=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.categories")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--wiki", required=True)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-missing", action="store_true",
                        help="replace an item's link for this wiki when its target page does not exist here")
    args = parser.parse_args(argv)
    if args.wiki not in LANGS:
        # checked before any login, so a typo never sends the bot password to another host
        parser.error(f"unknown wiki {args.wiki}; choose from {', '.join(LANGS)}")
    user, password = os.environ.get("SV_EDITOR_USER"), os.environ.get("SV_EDITOR_PASSWORD")
    if not user or not password:
        print("Set SV_EDITOR_USER and SV_EDITOR_PASSWORD (a bot password).", file=sys.stderr)
        return 2
    data = data or load(ROOT)
    try:
        client = client_factory(args.wiki)
        client.login(user, password)
        if not args.create:
            report = check(args.wiki, data, client)
            _print_report(args.wiki, data, report)
            return 1 if report.missing or report.redirect or report.wrong_parent else 0
        repo = client if args.wiki == "en" else client_factory("en")
        if repo is not client:
            repo.login(user, password)
        result = create(args.wiki, data, client, repo, args.dry_run, args.replace_missing)
    except (SyncError, OSError) as exc:
        print(f"[{args.wiki}] ERROR: {exc}", file=sys.stderr)
        return 1
    verb = "would create" if args.dry_run else "created"
    created = set(result.created)
    still_missing = [k for k in result.report.missing
                     if _title({n.key: n for n in nodes(data)}[k].names[args.wiki]) not in created]
    _print_report(args.wiki, data, result.report._replace(missing=still_missing))
    for title in result.created:
        print(f"[{args.wiki}] {verb}: {title}")
    for title, item in result.linked:
        print(f"[{args.wiki}] {'would link' if args.dry_run else 'linked'}: {title} -> {item}")
    for key, items in result.conflicts:
        print(f"[{args.wiki}] CONFLICT: {key} is on different items {', '.join(items)}; nothing linked")
    for key, item, current in result.taken:
        print(f"[{args.wiki}] CONFLICT: {key}: {item} already links {_site(args.wiki)} to {current}; nothing changed")
    for title, error in result.failed:
        print(f"[{args.wiki}] ERROR: {title}: {error}", file=sys.stderr)
    problems = (result.conflicts or result.taken or result.failed or result.report.redirect
                or result.report.wrong_parent)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
