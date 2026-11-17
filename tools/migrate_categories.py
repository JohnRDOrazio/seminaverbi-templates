"""python -m tools.migrate_categories --wiki <en|it|fr> [--dry-run]

Moves the old category pages of a wiki onto the category tree, following config/category_migration.json:

- "<key>": the old category becomes that node. Same title: its text is set from the tree. Different
  title: it is moved there (the old title stays as a redirect) unless the target exists, in which case
  the old page becomes a redirect to it. Members are re-filed to the new name in both cases.
- "redirect:<key>": not the same concept; members are re-filed to that node and the old page redirects.
- "split:<key>": members are filed one by one with Jev's classify; confident answers are re-filed, the
  others are left for the author; when none is left, the old page redirects to <key>.

Everything is planned (reading only, Jev included) before the first edit, so a failure while planning
changes nothing. Only category pages and the category lines of member pages are edited.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

from sync.categories import load, nodes
from sync.wiki import SyncError, WikiClient
from tools.categories import page_text

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "config" / "category_migration.json"
TAG = "seminaverbi-mcp"
WIKIS = ("en", "it", "fr")
SUMMARY = {
    "en": "Category tree migration", "it": "Migrazione all'albero delle categorie",
    "fr": "Migration vers l'arborescence des catégories",
}


class Plan(NamedTuple):
    steps: list        # ("move", old, new) | ("edit", title, new_text, old_text)
    author: list       # (member title, old category, Decision): split members Jev was not sure about
    skipped: list      # old category titles that are missing or already redirects
    unmatched: list    # (member title, category title): category line not found in the member's text
    redirected: list   # old category titles turned into redirects (the author may delete them)
    via_template: list  # (member title, category title): filed by a template whose category line was re-filed


class Report(NamedTuple):
    author: list
    skipped: list
    unmatched: list
    redirected: list
    failed: list       # (title, error)
    via_template: list


def _name_pattern(name: str) -> str:
    first, rest = name[0], name[1:]
    head = f"[{re.escape(first.upper())}{re.escape(first.lower())}]" if first.isalpha() else re.escape(first)
    return head + r"[ _]+".join(re.escape(part) for part in rest.split(" ")) if rest else head


def _line_regex(name: str, namespaces) -> re.Pattern:
    ns = "|".join(re.escape(n) for n in sorted(namespaces, key=len, reverse=True))
    return re.compile(r"\[\[\s*(?:" + ns + r")\s*:\s*" + _name_pattern(name) + r"\s*(\|[^\]]*)?\]\]", re.IGNORECASE)


def refile_text(text: str, old_name: str, new_name: str, namespaces) -> tuple[str, bool]:
    """Replace the [[Category:old_name|sortkey]] line(s) by [[Category:new_name|sortkey]]."""
    old_re = _line_regex(old_name, namespaces)
    if not old_re.search(text):
        return text, False
    if _line_regex(new_name, namespaces).search(text):
        # already in the new category: drop the old line instead of duplicating it
        return re.sub(old_re.pattern + r"\n?", "", text, flags=re.IGNORECASE), True
    return old_re.sub(lambda m: f"[[Category:{new_name}{m.group(1) or ''}]]", text), True


def _cat(name: str) -> str:
    return "Category:" + name


def _bare(title: str) -> str:
    return title.split(":", 1)[1] if ":" in title else title


def plan(lang: str, data: dict, mapping: dict, client, jev) -> Plan:
    by_key = {n.key: n for n in nodes(data)}
    namespaces = client.category_namespaces()
    mapped = set(mapping)
    steps, author, skipped, unmatched, redirected, via_template = [], [], [], [], [], []
    texts: dict[str, str | None] = {}  # pages as they will be after the planned edits

    def current(title):
        if title not in texts:
            texts[title] = client.wikitext(title)
        return texts[title]

    def set_text(title, new):
        old = current(title)
        if old != new:
            steps.append(("edit", title, new, old))
            texts[title] = new

    def refile_templates(old_name, new_name):
        """Templates and modules that add the category themselves (e.g. an infobox) get their line re-filed."""
        fixed = False
        for source in client.search_source(old_name):
            new, changed = refile_text(current(source) or "", old_name, new_name, namespaces)
            if changed:
                set_text(source, new)
                fixed = True
        return fixed

    def refile(members, old_name, new_name, templates_fixed=False):
        for member, ns in members:
            if ns == 14 and _bare(member) in mapped:
                continue  # that category's own entry rewrites its page
            new, changed = refile_text(current(member) or "", old_name, new_name, namespaces)
            if changed:
                set_text(member, new)
            elif templates_fixed:
                via_template.append((member, _cat(old_name)))
            else:
                unmatched.append((member, _cat(old_name)))

    def node_text(node):
        parent = by_key[node.parent_key] if node.parent_key else None
        return page_text(node, parent, lang)

    for old_name, target in mapping.items():
        old_title = _cat(old_name)
        if not client.exists(old_title) or client.is_redirect(old_title):
            skipped.append(old_title)
            continue
        kind, _, key = target.rpartition(":")
        node = by_key[key]
        new_name = node.names[lang]
        new_title = _cat(new_name)
        members = client.members(old_title)
        if kind == "split":
            pending = 0
            for member, ns in members:
                if ns == 14:
                    continue
                decision = jev.classify(current(member) or "", data)
                if decision.outcome != "applied":
                    author.append((member, old_title, decision))
                    pending += 1
                    continue
                refile([(member, ns)], old_name, by_key[decision.choice].names[lang])
            if not pending:
                set_text(old_title, f"#REDIRECT [[:{new_title}]]")
                redirected.append(old_title)
        elif kind == "redirect" or (old_name != new_name and client.exists(new_title)):
            refile(members, old_name, new_name, refile_templates(old_name, new_name))
            set_text(old_title, f"#REDIRECT [[:{new_title}]]")
            redirected.append(old_title)
            if kind != "redirect":
                set_text(new_title, node_text(node))
        elif old_name == new_name:
            set_text(old_title, node_text(node))
        else:
            steps.append(("move", old_title, new_title))
            texts[new_title] = current(old_title)
            refile(members, old_name, new_name, refile_templates(old_name, new_name))
            set_text(new_title, node_text(node))
    return Plan(steps, author, skipped, unmatched, redirected, via_template)


def execute(client, p: Plan, dry_run: bool = False, lang: str = "en") -> Report:
    failed = []
    summary = SUMMARY.get(lang, SUMMARY["en"])
    if not dry_run:
        for step in p.steps:
            try:
                if step[0] == "move":
                    client.move(step[1], step[2], summary, tags=TAG)
                else:
                    client.edit(step[1], step[2], summary, tags=TAG)
            except (SyncError, OSError) as exc:
                failed.append((step[1], str(exc)))
    return Report(p.author, p.skipped, p.unmatched, p.redirected, failed, p.via_template)


class JevAdapter:
    def __init__(self, client):
        self.client = client

    def classify(self, text, data):
        from tools import jev
        return jev.classify(self.client, text, data)


def _print(p: Plan, report: Report, lang: str, dry_run: bool) -> None:
    for step in p.steps:
        if step[0] == "move":
            print(f"[{lang}] {'would move' if dry_run else 'moved'}: {step[1]} -> {step[2]}")
        else:
            print(f"[{lang}] {'would edit' if dry_run else 'edited'}: {step[1]}")
            if dry_run:
                diff = difflib.unified_diff((step[3] or "").splitlines(), step[2].splitlines(), lineterm="", n=1)
                print("\n".join("    " + line for line in list(diff)[2:]))
    for title in report.redirected:
        print(f"[{lang}] redirect left (admin may delete): {title}")
    for title in report.skipped:
        print(f"[{lang}] skipped (missing or already a redirect): {title}")
    for member, cat in report.via_template:
        print(f"[{lang}] via template: {member} gets {cat} from a template whose line was re-filed")
    for member, cat in report.unmatched:
        print(f"[{lang}] UNMATCHED: {member} is in {cat} but no category line was found (template?)")
    for member, cat, d in report.author:
        probs = ", ".join(f"{k} {v:.2f}" for k, v in sorted(d.probabilities.items(), key=lambda kv: -kv[1]))
        print(f"[{lang}] AUTHOR: {member} (in {cat}): Jev suggests {d.choice} ({probs}); left unchanged")
    for title, error in report.failed:
        print(f"[{lang}] ERROR: {title}: {error}", file=sys.stderr)


def main(argv=None, client_factory=WikiClient, jev=None, data=None, mapping=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.migrate_categories")
    parser.add_argument("--wiki", required=True, choices=WIKIS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    user, password = os.environ.get("SV_EDITOR_USER"), os.environ.get("SV_EDITOR_PASSWORD")
    if not user or not password:
        print("Set SV_EDITOR_USER and SV_EDITOR_PASSWORD (a bot password).", file=sys.stderr)
        return 2
    data = data or load(ROOT)
    mapping = mapping or json.loads(MAP.read_text(encoding="utf-8"))
    try:
        client = client_factory(args.wiki)
        client.login(user, password)
        if jev is None:
            from tools.jev import make_client
            jev = JevAdapter(make_client())
        p = plan(args.wiki, data, mapping.get(args.wiki, {}), client, jev)
    except Exception as exc:  # planning reads only: nothing has been changed
        print(f"[{args.wiki}] ERROR while planning (nothing changed): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    report = execute(client, p, args.dry_run, args.wiki)
    _print(p, report, args.wiki, args.dry_run)
    return 1 if report.failed or report.unmatched or report.author else 0


if __name__ == "__main__":
    sys.exit(main())
