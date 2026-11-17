"""Jev (TypeSafe System One) judgments for the SeminaVerbi category tree.

    python -m tools.jev place <node-key> <candidate-key>... [--record]
    python -m tools.jev classify <article-text-file>
    python -m tools.jev themes <article-text-file>

Jev answers typed questions with calibrated probabilities; the policy below decides in code whether an
answer is applied or goes to the author. The API key comes only from TYPESAFE_API_KEY and is never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import NamedTuple

from typesafe_sdk import Choice, Noul, TypeSafeError

from sync.categories import field_children, leaf_themes, load, nodes

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "config" / "category_decisions.json"
MODEL = "jev-1.14.0"

# Policy: tunable here, recorded with every decision.
CHOICE_PROBABILITY = 0.80
CHOICE_CONFIDENCE = 0.6
THEME_APPLY = 0.75
THEME_ASK = 0.40
BEAM_TIE = 0.15  # top two options this close: explore both branches


class Decision(NamedTuple):
    question: str
    node: str | None
    options: dict
    probabilities: dict
    confidence: float
    choice: str
    outcome: str  # "applied" | "author" ("no" for a themes entry with no theme at all)


def make_client():
    from typesafe_sdk import TypeSafeClient
    return TypeSafeClient()


def confident(probability: float, confidence: float) -> bool:
    return probability >= CHOICE_PROBABILITY and confidence >= CHOICE_CONFIDENCE


def _path(data, key) -> str:
    parents = {n.key: n.parent_key for n in nodes(data)}
    names = {n.key: n.names["en"] for n in nodes(data)}
    out = []
    while key and key != "root":
        out.append(names[key])
        key = parents[key]
    return " > ".join(reversed(out))


def _option(data, n) -> dict:
    return {"name": n.names["en"], "path": _path(data, n.key), "definition": n.definition.get("en", "")}


def place(client, node, candidates, data) -> Decision:
    """Which of `candidates` should be the parent of `node`."""
    criteria = {c.key: _option(data, c) for c in candidates}
    state = {"node": {"name": node.names["en"], "definition": node.definition.get("en", "")},
             "candidates": [dict(key=c.key, **_option(data, c)) for c in candidates]}
    q = Choice(instructions="In a classification of all expressions of human culture, under which parent "
                            "category does `node` belong? Judge by the definitions of `node` and of each "
                            "candidate, not by the names alone.", criteria=criteria)
    a = client.system_one(state=state, questions={"place": q}, model=MODEL).choices["place"]
    p = a.probabilities[a.choice]
    return Decision("place", node.key, criteria, dict(a.probabilities), a.confidence, a.choice,
                    "applied" if confident(p, a.confidence) else "author")


def _ask_level(client, text, data, parent_key):
    children = field_children(data, parent_key)
    criteria = {c.key: _option(data, c) for c in children}
    q = Choice(instructions="`article` is about one work or one person. Which category describes the work's "
                            "own medium or form, or the person's main domain of activity? Not its subject: a "
                            "film about a saint is a film.", criteria=criteria)
    a = client.system_one(state={"article": text}, questions={"level": q}, model=MODEL).choices["level"]
    return criteria, a


def classify(client, text: str, data) -> Decision:
    """Exactly one field node for an article: descend the tree while each step is confident."""
    results = []  # (score, key, all_steps_confident, criteria, answer)

    def descend(parent_key, score, ok, picked=(None, None)):
        """`picked`: the (criteria, answer) of the level that chose `parent_key`, its evidence."""
        if parent_key is not None and not field_children(data, parent_key):
            results.append((score, parent_key, ok, *picked))
            return
        criteria, a = _ask_level(client, text, data, parent_key)
        ranked = sorted(a.probabilities.items(), key=lambda kv: kv[1], reverse=True)
        best, best_p = ranked[0]
        if confident(best_p, a.confidence):
            descend(best, score * best_p, ok, (criteria, a))
            return
        # explore both branches only when it can add evidence: both tied options have children to ask about
        tie = (len(ranked) > 1 and best_p - ranked[1][1] <= BEAM_TIE
               and all(field_children(data, k) for k, _ in ranked[:2]))
        if tie:
            for key, p in ranked[:2]:
                descend(key, score * p, False, (criteria, a))
        elif parent_key is None:
            results.append((score * best_p, best, False, criteria, a))
        else:
            results.append((score * best_p, parent_key, ok, *picked))  # file at the confident parent

    descend(None, 1.0, True)
    score, key, ok, criteria, a = max(results, key=lambda r: r[0])
    return Decision("classify", None, criteria or {}, dict(a.probabilities) if a else {},
                    a.confidence if a else 1.0, key, "applied" if ok else "author")


def themes(client, text: str, data) -> dict:
    """theme key -> (probability, "applied" | "author" | "no"), one Noul per leaf theme, one request."""
    questions = {
        t.key: Noul(instructions=f"Does `article` genuinely explore the theme of {t.names['en']} "
                                 f"({t.definition.get('en', '')}), not merely mention it?")
        for t in leaf_themes(data)
    }
    answers = client.system_one(state={"article": text}, questions=questions, model=MODEL).nouls
    out = {}
    for key in questions:
        p = float(answers[key].noul)
        out[key] = (p, "applied" if p >= THEME_APPLY else "author" if p >= THEME_ASK else "no")
    return out


def record(decision: Decision, path: Path = LOG) -> dict:
    return record_all([decision], path)[0]


def record_all(decisions: list, path: Path = LOG) -> list[dict]:
    """Append the decisions to the log in one read and one write."""
    if not decisions:
        return []
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    today = date.today().isoformat()
    added = []
    for decision in decisions:
        prefix = f"{today}-{decision.node or 'article'}"
        n = sum(1 for e in entries if e["id"].startswith(prefix + "-")) + 1
        entry = {"id": f"{prefix}-{n}", "date": today, "model": MODEL, **decision._asdict(),
                 "thresholds": {"choice_probability": CHOICE_PROBABILITY, "choice_confidence": CHOICE_CONFIDENCE,
                                "theme_apply": THEME_APPLY, "theme_ask": THEME_ASK, "beam_tie": BEAM_TIE}}
        entries.append(entry)
        added.append(entry)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added


def _print_decision(d: Decision) -> None:
    probs = ", ".join(f"{k} {v:.2f}" for k, v in sorted(d.probabilities.items(), key=lambda kv: -kv[1]))
    print(f"{d.question} {d.node or ''}: {d.choice} ({d.outcome}; confidence {d.confidence:.2f}; {probs})")


def main(argv=None, client=None, data=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.jev")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("place")
    p.add_argument("node")
    p.add_argument("candidates", nargs="+")
    p.add_argument("--record", action="store_true")
    for name in ("classify", "themes"):
        sp = sub.add_parser(name)
        sp.add_argument("file")
        sp.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    data = data or load(ROOT)
    by_key = {n.key: n for n in nodes(data)}
    try:
        client = client or make_client()
        if args.command == "place":
            unknown = [k for k in [args.node, *args.candidates] if k not in by_key]
            if unknown:
                parser.error(f"unknown node key(s): {', '.join(unknown)}")
            d = place(client, by_key[args.node], [by_key[k] for k in args.candidates], data)
            _print_decision(d)
            if args.record:
                print(f"recorded {record(d)['id']}")
        elif args.command == "classify":
            d = classify(client, Path(args.file).read_text(encoding="utf-8"), data)
            _print_decision(d)
            if args.record:
                print(f"recorded {record(d._replace(node=Path(args.file).stem), LOG)['id']}")
        else:
            result = themes(client, Path(args.file).read_text(encoding="utf-8"), data)
            for key, (prob, outcome) in sorted(result.items(), key=lambda kv: -kv[1][0]):
                print(f"{key}: {prob:.2f} {outcome}")
            if args.record:
                outcomes = {o for _, o in result.values()}
                outcome = "applied" if "applied" in outcomes else "author" if "author" in outcomes else "no"
                d = Decision("themes", Path(args.file).stem, {}, {k: p for k, (p, _) in result.items()}, None,
                             ",".join(sorted(k for k, (_, o) in result.items() if o == "applied")), outcome)
                print(f"recorded {record(d, LOG)['id']}")
    except Exception as exc:  # TypeSafe, network, file or anything unexpected: no traceback, nothing recorded
        print(f"Jev request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
