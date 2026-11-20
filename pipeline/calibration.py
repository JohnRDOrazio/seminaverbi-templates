"""The calibration report: what Jev says about the articles SeminaVerbi already has, beside what the author filed."""
import json
from pathlib import Path

from pipeline import load_config
from pipeline.publish import display_name
from pipeline.stages import THEME_KEEP, _local_item, direct
from sync import categories

ROOT = Path(__file__).resolve().parent.parent


def _item_keys(tree: dict) -> dict[str, str]:
    """Local Wikibase item -> category tree key, for every field and theme that has an item."""
    out: dict[str, str] = {}

    def walk(items):
        for d in items:
            if "item" in d:
                out[d["item"]] = d["key"]
            walk(d.get("children", []))
    walk(tree["fields"])
    walk(tree["themes"])
    return out


def _ancestors(tree: dict) -> dict[str, set[str]]:
    """Field key -> the keys of the fields above it."""
    out: dict[str, set[str]] = {}

    def walk(items, above):
        for d in items:
            out[d["key"]] = set(above)
            walk(d.get("children", []), above | {d["key"]})
    walk(tree["fields"], set())
    return out


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _field_verdict(jev_field, item_field: set[str], ancestors) -> str:
    if jev_field and item_field == {jev_field}:
        return "agree"
    if jev_field and len(item_field) == 1 and item_field <= ancestors.get(jev_field, set()):
        return "agree, deeper"
    return "DISAGREE"


def _kept(result: dict) -> list[dict]:
    """The saved themes that meet today's cutoff (a result saved under an older one keeps its themes)."""
    return [t for t in result.get("themes", []) if t["p"] >= THEME_KEEP]


def _verdict(row, result: dict, quick_drop: float) -> str:
    """What the pipeline would do with this work if it were a new candidate."""
    quick = (row.data.get(3) or {}).get("p")
    if quick is not None and quick < quick_drop:
        return f"would be dropped: quick check {quick:.2f} < {quick_drop:.2f}"
    if not _kept(result):
        if direct(result.get("connection", {})):
            return "would be proposed (direct connection)"
        return f"would be dropped: no theme at {THEME_KEEP:.2f}"
    return "would be proposed"


def _row_block(row, wiki, props, keys, ancestors, quick_drop) -> str:
    lines = [f"### {_cell(display_name(row))} ({row.qid})", "", "| | Jev | SeminaVerbi item |", "| --- | --- | --- |"]
    result = row.data.get(5)
    if not result:
        return "\n".join(lines + ["| result | not judged (no stage 5 outcome) | |"])
    item = _local_item(row, wiki)
    item_field: set[str] = set()
    item_themes: set[str] = set()
    if item:
        item_field = {keys.get(q, q) for q in wiki.item_values(item, props["sv_field"])}
        item_themes = {keys.get(q, q) for q in wiki.item_values(item, props["sv_theme"])}
    lines.append(f"| verdict | {_verdict(row, result, quick_drop)} | |")
    conn = result["connection"]
    p = conn.get("probabilities", {}).get(conn["choice"])
    lines.append(f"| connection | {conn['choice']}" + (f" ({p:.2f})" if p is not None else "") + " | |")
    jev_field = result.get("field", {}).get("choice")
    verdict = _field_verdict(jev_field, item_field, ancestors)
    lines.append(f"| field | {_cell(jev_field)} | {_cell(', '.join(sorted(item_field)) or 'none')} ({verdict}) |")
    jev_themes = {t["key"]: t["p"] for t in _kept(result)}
    scores = result.get("theme_scores", {})
    shown = ", ".join(f"{k} ({v:.2f})" for k, v in sorted(jev_themes.items(), key=lambda kv: -kv[1])) or "none"
    missed = [k + (f" ({scores[k]:.2f})" if k in scores else "") for k in sorted(item_themes - set(jev_themes))]
    extra = sorted(set(jev_themes) - item_themes)
    note = "agree" if not missed and not extra else "; ".join(
        ([f"not found by Jev: {', '.join(missed)}"] if missed else [])
        + ([f"only Jev: {', '.join(extra)}"] if extra else []))
    lines.append(f"| themes | {_cell(shown)} | {_cell(', '.join(sorted(item_themes)) or 'none')} ({_cell(note)}) |")
    if not item:
        lines.append("| item | | no local item found |")
    return "\n".join(lines)


def report(queue, wikis, tree=None, unmapped=()) -> str:
    """Markdown: one table per calibration article, then the pages that could not be mapped to Wikidata."""
    tree = tree or categories.load(ROOT)
    props = json.loads((ROOT / "config" / "wikibase.json").read_text(encoding="utf-8"))
    keys = _item_keys(tree)
    ancestors = _ancestors(tree)
    quick_drop = load_config()["quick_drop"]
    wiki = wikis["en"]
    rows = sorted((r for r in queue.calibration_rows()), key=lambda r: r.qid)
    blocks = ["# Calibration report", ""]
    blocks += [_row_block(r, wiki, props, keys, ancestors, quick_drop) + "\n" for r in rows]
    if not rows:
        blocks.append("No calibration rows.\n")
    blocks.append("## Pages not mapped to a Wikidata item\n")
    blocks += [f"- {_cell(u)}" for u in unmapped] or ["none"]
    return "\n".join(blocks) + "\n"
