"""The calibration report: what Jev says about the articles SeminaVerbi already has, beside what the author filed."""
import json
from pathlib import Path

from pipeline.stages import _local_item
from sync import categories

ROOT = Path(__file__).resolve().parent.parent
THEME_SHOWN = 0.40


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


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _row_block(row, wiki, props, keys) -> str:
    title = row.labels.get("en") or row.qid
    lines = [f"### {_cell(title)} ({row.qid})", "", "| | Jev | SeminaVerbi item |", "| --- | --- | --- |"]
    result = row.data.get(5)
    if not result:
        return "\n".join(lines + ["| result | not judged (no stage 5 outcome) | |"])
    item = _local_item(row, wiki)
    item_field: set[str] = set()
    item_themes: set[str] = set()
    if item:
        item_field = {keys.get(q, q) for q in wiki.item_values(item, props["sv_field"])}
        item_themes = {keys.get(q, q) for q in wiki.item_values(item, props["sv_theme"])}
    conn = result["connection"]
    p = conn.get("probabilities", {}).get(conn["choice"])
    lines.append(f"| connection | {conn['choice']}" + (f" ({p:.2f})" if p is not None else "") + " | |")
    jev_field = result.get("field", {}).get("choice")
    verdict = "agree" if jev_field and item_field == {jev_field} else "DISAGREE"
    lines.append(f"| field | {_cell(jev_field)} | {_cell(', '.join(sorted(item_field)) or 'none')} ({verdict}) |")
    jev_themes = {t["key"]: t["p"] for t in result.get("themes", []) if t["p"] >= THEME_SHOWN}
    shown = ", ".join(f"{k} ({v:.2f})" for k, v in sorted(jev_themes.items(), key=lambda kv: -kv[1])) or "none"
    missed = sorted(item_themes - set(jev_themes))
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
    wiki = wikis["en"]
    rows = sorted((r for r in queue.calibration_rows()), key=lambda r: r.qid)
    blocks = ["# Calibration report", ""]
    blocks += [_row_block(r, wiki, props, keys) + "\n" for r in rows]
    if not rows:
        blocks.append("No calibration rows.\n")
    blocks.append("## Pages not mapped to a Wikidata item\n")
    blocks += [f"- {_cell(u)}" for u in unmapped] or ["none"]
    return "\n".join(blocks) + "\n"
