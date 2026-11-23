"""python -m pipeline <command> [--db PATH] [--domain film]

Stages: 0 candidates, 1 covered, 2 triage, 3 quick, 4 dossier, 5 adjudicate, 6 publish (see the README).
Commands that write to the wiki (publish, run --publish, votes) log in as the editor bot with
SV_EDITOR_USER / SV_EDITOR_PASSWORD (run --publish logs in before any stage); the Jev client is only made
for quick, adjudicate and calibrate. `skip QID --reason TEXT` drops a row that breaks a stage.

`press discover|filter|link|route|enrich|run [--dry-run]` runs the Catholic-press source (pipeline/press/): filter
and link ask Jev (TYPESAFE_API_KEY), enrich and run write the enrichment page as the editor bot; `--dry-run` writes
neither the candidates queue nor the wiki.
"""
import argparse
import os
import sqlite3
import sys

from typesafe_sdk import TypeSafeError

from pipeline import calibration, load_config, stages
from pipeline.domains import DOMAINS
from pipeline.domains.common import domain_config
from pipeline.publish import approved_report, publish, sync_votes
from pipeline.press.fetch import load_press_config
from pipeline.press import stages as press_stages
from pipeline.press.enrich import ENRICH_TITLE, enrichment, publish_enrichment
from pipeline.press.notes import press_notes
from pipeline.press.route import release_waiting, route
from pipeline.press.store import PressStore
from pipeline.queue import Queue
from sync import categories
from sync.wiki import SyncError, WikiClient

DEFAULT_DB = "data/pipeline.sqlite"
LANGS = stages.LANGS


class CliError(Exception):
    pass


# ---- services (replaced in tests) ----

def _sparql():
    from pipeline.sources import Sparql
    return Sparql()


def _wikidata():
    from sync.wikidata import WikidataClient
    return WikidataClient()


def _wikipedia(lang, project="wikipedia"):
    from pipeline.sources import Wikipedia
    return Wikipedia(lang, project=project)


def _wikis() -> dict:
    return {lang: WikiClient(lang) for lang in LANGS}


def _judge(queue):
    from pipeline.judge import Judge
    from tools import jev
    return Judge(jev.make_client(), queue, data=_tree())


def _tree():
    from pipeline.judge import ROOT
    return categories.load(ROOT)


def _login_editor() -> WikiClient:
    try:
        user, password = os.environ["SV_EDITOR_USER"], os.environ["SV_EDITOR_PASSWORD"]
    except KeyError as exc:
        raise CliError(f"{exc.args[0]} is not set; this command writes to the wiki and needs the editor bot "
                       "(run it through ~/.config/seminaverbi/runeditor.py)") from None
    en = WikiClient("en")
    en.login(user, password)
    return en


def _editor() -> WikiClient:
    return _login_editor()


def _press_client(pcfg: dict, store):
    import requests
    from pipeline.press.fetch import PoliteClient
    return PoliteClient(requests.Session(), pcfg["user_agent"], pcfg["delay_seconds"], store)


def _press_judge(queue):
    from pipeline.press.judge import PressJudge
    from tools import jev
    return PressJudge(jev.make_client(), queue)


# ---- commands ----

def _show(label: str, counts: dict) -> None:
    print(f"{label}: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing pending"))


def _triage_stage(domain, cfg):
    def fn(row):
        outcome, reason = domain.triage(row, cfg)
        return outcome, {"reason": reason}
    return fn


def _stage_functions(domain, queue, cfg) -> dict:
    """Stages 1-5 as run_stage callables; the Jev client and the wikis are made when first needed."""
    lazy: dict = {}
    cfg = domain_config(cfg, getattr(domain, "__name__", "").rsplit(".", 1)[-1])  # idempotent; direct callers too

    def wikis():
        return lazy.setdefault("wikis", _wikis())

    def judge():
        return lazy.setdefault("judge", _judge(queue))

    def wiki_for(lang, project="wikipedia"):
        return _wikipedia(lang, project)

    def dossier(row):
        outcome, data = domain.dossier_stage(row, cfg, wiki_for, lazy.setdefault("wikidata", _wikidata()))
        store = PressStore.of(queue)
        if not store.mentions(qid=row.qid):
            return outcome, data
        if "outlets" not in lazy:
            try:
                lazy["outlets"] = {o["id"]: o["name"] for o in load_press_config()["outlets"]}
            except (OSError, ValueError, KeyError):
                lazy["outlets"] = {}
        notes = press_notes(store, row.qid, lazy["outlets"])
        if not notes:
            return outcome, data
        if outcome == "drop" and data.get("reason") == "empty dossier":  # press-only work: the press is its dossier
            data = {k: v for k, v in data.items() if k != "reason"}
            outcome = "pass"
            data["languages"] = {}
            for n in notes:
                data["languages"][n["lang"]] = data["languages"].get(n["lang"], 0) + 1
        elif outcome == "drop":
            return outcome, data
        data["notes"] = notes + data.get("notes", [])
        return outcome, data

    return {
        1: lambda row: (getattr(domain, "covered", None) or stages.covered)(row, wikis()["en"], wikis()),
        2: _triage_stage(domain, cfg),
        3: lambda row: stages.quick(row, judge(), wiki_for),
        4: dossier,
        5: lambda row: stages.adjudicate(row, judge(), _tree()),
    }


NAMES = {1: "covered", 2: "triage", 3: "quick", 4: "dossier", 5: "adjudicate"}


def cmd_candidates(args, queue, cfg):
    domain = DOMAINS[args.domain]
    added = domain.candidates(queue, _sparql(), _wikipedia("en"), cfg, _wikidata())
    print(f"candidates: {added} added")
    press = PressStore.of(queue)
    waiting = f"waiting:{args.domain}"
    if not any(m.domain == waiting for m in press.mentions(route="waiting")):
        return
    wikis = _wikis()
    released = release_waiting(press, queue, args.domain, wikis["en"], wikis, _wikidata())
    if released:
        print(f"candidates: press mentions released from waiting: {released.get('candidate', 0)} as candidates, "
              f"{released.get('enrichment', 0)} to the enrichment list")


def _limit(stage: int, cfg) -> int | None:
    """At most max_new_per_run rows enter stage 3 (the first Jev stage) per command."""
    return cfg["max_new_per_run"] if stage == 3 else None


def cmd_stage(args, queue, cfg):
    stage = next(n for n, name in NAMES.items() if name == args.command)
    fn = _stage_functions(DOMAINS[args.domain], queue, cfg)[stage]
    _show(args.command, stages.run_stage(queue, stage, fn, limit=_limit(stage, cfg), domain=args.domain))


def cmd_publish(args, queue, cfg, editor=None):
    print(f"publish: {publish(queue, editor or _editor(), cfg, domain=args.domain)} pages written")


def cmd_run(args, queue, cfg):
    editor = _editor() if args.publish else None  # missing credentials fail before any stage runs
    cmd_candidates(args, queue, cfg)
    fns = _stage_functions(DOMAINS[args.domain], queue, cfg)
    for stage in (1, 2, 3, 4, 5):
        _show(NAMES[stage], stages.run_stage(queue, stage, fns[stage], limit=_limit(stage, cfg),
                                             domain=args.domain))
    if args.publish:
        cmd_publish(args, queue, cfg, editor)


def cmd_calibrate(args, queue, cfg):
    domain = DOMAINS[args.domain]
    wikis = _wikis()
    qids, unmapped = domain.calibration_qids(wikis, _wikidata(), _tree())
    print(f"calibration: {domain.add_calibration(queue, _sparql(), qids)} rows added")
    fns = _stage_functions(domain, queue, cfg)
    for stage in (1, 2, 3, 4, 5):
        _show(NAMES[stage], stages.run_stage(queue, stage, fns[stage], only=lambda r: r.calibration,
                                             domain=args.domain))
    print(calibration.report(queue, wikis, tree=_tree(), unmapped=unmapped))


def cmd_skip(args, queue, cfg):
    """Drop a row that breaks a stage (a poison row) at its next stage without an outcome."""
    row = queue.row(args.qid)
    if row is None:
        raise CliError(f"{args.qid} is not in the queue")
    stage = next((n for n in NAMES if n not in row.outcomes), None)
    if stage is None:
        raise CliError(f"{args.qid} has an outcome at every stage; use redo, or decline its proposal page")
    queue.set_outcome(row.qid, stage, "drop", {"reason": args.reason, "skipped": True})
    print(f"skip: {row.qid} dropped at stage {stage} ({NAMES[stage]})")


def cmd_votes(args, queue, cfg):
    result = sync_votes(queue, _editor(), cfg)
    print(f"votes: approved {len(result['approved'])}, declined {len(result['declined'])}")


def cmd_approved(args, queue, cfg):
    print(approved_report(queue) or "nothing approved")


def cmd_redo(args, queue, cfg):
    queue.redo(args.stage, args.qid)
    print(f"redo: stages {args.stage}+ cleared" + (f" for {args.qid}" if args.qid else ""))


PRESS_ACTIONS = ("discover", "filter", "link", "route", "enrich", "run")


def _counts(counts: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"


def _refused(client) -> None:
    refused = getattr(client, "refused", {}) if client is not None else {}
    print("press refused: " + (", ".join(f"{o} ({r})" for o, r in sorted(refused.items())) or "none"))


def cmd_press(args, queue, cfg):
    """The press stages; `run` = discover, filter, link, route, enrich. `--dry-run`: discover into a throwaway
    store and list the URLs; route (and run) only print each mention's would-be route; enrich prints the page."""
    pcfg, action, dry = load_press_config(), args.action, args.dry_run
    if action in ("filter", "link", "run") and not os.environ.get("TYPESAFE_API_KEY"):
        raise CliError("TYPESAFE_API_KEY is not set; press filter and link ask Jev")
    editor = _login_editor() if action in ("enrich", "run") and not dry else None  # before any stage runs
    store = PressStore.of(queue)
    outlets = {o["id"]: o["name"] for o in pcfg["outlets"]}
    client = None
    if action in ("discover", "filter", "link", "run"):
        if action == "discover" and dry:  # a throwaway store: no articles, no feed caches kept
            store = PressStore(sqlite3.connect(":memory:"))
        client = _press_client(pcfg, store)
    pages = press_stages.Pages(client) if client is not None else None
    judge = _press_judge(queue) if action in ("filter", "link", "run") else None
    lazy: dict = {}

    def wikidata():
        return lazy.setdefault("wikidata", _wikidata())

    def wikis():
        return lazy.setdefault("wikis", _wikis())

    if action in ("discover", "run"):
        print("press discover: " + _counts(press_stages.run_discover(store, client, pcfg)))
        if action == "discover" and dry:
            for oid in outlets:
                for art in store.articles("found", outlet=oid):
                    print(f"  {oid} {art.lang} {art.url}")
    if action in ("filter", "run"):
        print("press filter: " + _counts(press_stages.run_filter(store, pages, judge, pcfg)))
    if action in ("link", "run"):
        print("press link: " + _counts(press_stages.run_link(store, pages, judge, wikidata(), _sparql(), pcfg)))
    if action in ("route", "run"):
        if dry:
            print("\n".join(press_stages.preview(store, wikis()["en"], wikis(), wikidata())))
        else:
            print("press route: " + _counts(route(store, queue, wikis()["en"], wikis(), wikidata())))
    if action in ("enrich", "run") and not dry:
        titles = press_stages.titles(store, wikis()["en"], wikidata())
        written = publish_enrichment(store, editor, outlets, titles)
        print("press enrich: " + ("page written" if written else "page unchanged"))
    elif action == "enrich":
        current = wikis()["en"].revision(ENRICH_TITLE)
        print(enrichment(store, outlets, current[1] if current else None,
                         press_stages.titles(store, wikis()["en"], wikidata())))
    if client is not None:
        _refused(client)


def build_parser() -> argparse.ArgumentParser:
    # the common options are accepted before or after the command
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--db")
    common.add_argument("--domain", choices=sorted(DOMAINS))
    # no set_defaults here: it would change the shared actions, and the subcommand's default would then
    # overwrite a value given before the command; parse_args fills the defaults afterwards
    parser = argparse.ArgumentParser(prog="python -m pipeline", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidates", "covered", "triage", "quick", "dossier", "adjudicate", "publish", "calibrate",
                 "votes", "approved"):
        sub.add_parser(name, parents=[common])
    run = sub.add_parser("run", parents=[common])
    run.add_argument("--publish", action="store_true")
    redo = sub.add_parser("redo", parents=[common])
    redo.add_argument("stage", type=int)
    redo.add_argument("--qid")
    skip = sub.add_parser("skip", parents=[common])
    skip.add_argument("qid")
    skip.add_argument("--reason", required=True)
    press = sub.add_parser("press", parents=[common])
    press.add_argument("action", choices=PRESS_ACTIONS)
    press.add_argument("--dry-run", action="store_true")
    return parser


DEFAULTS = {"db": DEFAULT_DB, "domain": "film"}


def parse_args(argv=None, parser=None) -> argparse.Namespace:
    """The arguments, the common options defaulted after parsing (given before or after the command)."""
    args = (parser or build_parser()).parse_args(argv)
    for name, value in DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    return args


COMMANDS = {"candidates": cmd_candidates, "publish": cmd_publish, "run": cmd_run, "calibrate": cmd_calibrate,
            "votes": cmd_votes, "approved": cmd_approved, "redo": cmd_redo, "skip": cmd_skip, "press": cmd_press,
            **{n: cmd_stage for n in NAMES.values()}}


def main(argv=None) -> int:
    parser = build_parser()
    args = parse_args(argv, parser)
    if args.command == "redo" and args.stage < 1:
        parser.error("redo 0 is refused: stage 0 holds the data the later stages need; run candidates again instead")
    cfg = domain_config(load_config(), args.domain)  # the domain's own sitelink floor, for every command
    try:
        return COMMANDS[args.command](args, Queue(args.db), cfg) or 0
    except (CliError, SyncError, TypeSafeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
