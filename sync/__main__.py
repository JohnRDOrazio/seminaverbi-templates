"""python -m sync [--wiki en,it|all] [--dry-run] [--force TITLE]..."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sync.pages import LANGS, desired_pages
from sync.plan import plan
from sync.wiki import SyncError, WikiClient

ROOT = Path(__file__).resolve().parent.parent


def _sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def main(argv=None, client_factory=WikiClient, sha=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sync")
    parser.add_argument("--wiki", default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="append", default=[], metavar="TITLE")
    args = parser.parse_args(argv)

    user, password = os.environ.get("SV_SYNC_USER"), os.environ.get("SV_SYNC_PASSWORD")
    if not user or not password:
        print("Set SV_SYNC_USER and SV_SYNC_PASSWORD (a bot password).", file=sys.stderr)
        return 2
    bot_user = user.split("@", 1)[0]
    langs = LANGS if args.wiki == "all" else [w.strip() for w in args.wiki.split(",")]
    unknown = [w for w in langs if w not in LANGS]
    if unknown:
        # checked before any login, so a typo never sends the bot password to another host
        parser.error(f"unknown wiki {', '.join(unknown)}; choose from {', '.join(LANGS)} or all")
    sha = sha or _sha()
    summary = f"sync from seminaverbi-templates@{sha}"
    failed = False

    for lang in langs:
        try:
            client = client_factory(lang)
            client.login(user, password)
            desired = desired_pages(ROOT, lang, sha)
            actions = plan(desired, client.fetch(sorted(desired)), bot_user, set(args.force))
            counts = {"create": 0, "update": 0, "skip-unchanged": 0, "skip-foreign": 0}
            edit_failures = 0
            for action in actions:
                counts[action.kind] += 1
                if action.kind == "skip-foreign":
                    failed = True
                    print(f"[{lang}] SKIPPED {action.title}: last edited by someone else "
                          f"(use --force to overwrite)\n{action.diff}")
                elif action.kind in ("create", "update"):
                    if args.dry_run:
                        print(f"[{lang}] would {action.kind} {action.title}\n{action.diff}")
                    else:
                        try:
                            client.edit(action.title, desired[action.title], summary)
                        except SyncError as exc:
                            # one refused edit (protection, AbuseFilter, conflict) must not stop the rest
                            failed = True
                            edit_failures += 1
                            print(f"[{lang}] ERROR: {exc}", file=sys.stderr)
            print(f"[{lang}] create {counts['create']}, update {counts['update']}, "
                  f"unchanged {counts['skip-unchanged']}, skipped(foreign) {counts['skip-foreign']}"
                  + (f", failed {edit_failures}" if edit_failures else "")
                  + (" (dry run)" if args.dry_run else ""))
        except (SyncError, OSError) as exc:
            failed = True
            print(f"[{lang}] ERROR: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
