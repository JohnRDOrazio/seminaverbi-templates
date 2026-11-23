"""The enrichment page: Catholic-press articles about works SeminaVerbi already covers, for editors.

The bot owns the block between the markers; whatever follows the end marker (editors' `{{SV press done|url}}` and
notes) is kept as written, and an entry whose URL is closed there is left out of the block.
"""
from __future__ import annotations

import re

from pipeline.press.notes import MAX_PASSAGES
from pipeline.publish import _write

ENRICH_TITLE = "Semina Verbi:Press for existing articles"
INTRO = ("Articles of the Catholic press on works SeminaVerbi already covers. Work an article into the entry, "
         "then add <code><nowiki>{{SV press done|<url>}}</nowiki></code> below to close it.")
BEGIN = "<!-- press:begin -->"
END = "<!-- press:end -->"
_DONE = re.compile(r"\{\{\s*SV press done\s*\|\s*([^|}\s]+)\s*\}\}")


def done_urls(text: str | None) -> set[str]:
    """The URLs closed with `{{SV press done|url}}` in the page text."""
    return set(_DONE.findall(text or ""))


def _tail(old_text: str | None) -> str:
    if old_text and BEGIN in old_text and END in old_text:
        return old_text.split(END, 1)[1]
    return "\n\n" + old_text if old_text and old_text.strip() else "\n"


def _item(label: str, titles: dict | None) -> str:
    links = [f"[[:{lang}:{title}]]" for lang, title in sorted((titles or {}).items())]
    return ", ".join(links) if links else label


def enrichment(store, outlets: dict[str, str], old_text: str | None, titles: dict | None = None) -> str:
    """The page text. `titles` maps a QID to {lang: page title} for the wikis that have the item."""
    done = done_urls(old_text)
    entries: dict[str, list] = {}
    for m in store.mentions(route="enrichment"):
        art = store.article(m.url)
        if m.url in done or art is None:
            continue
        entries.setdefault(m.qid, []).append((art.published or "", m, art))
    # newest article first within an item; items by their newest article
    groups = sorted(entries.items(), key=lambda kv: max(e[0] for e in kv[1]), reverse=True)
    lines = []
    for qid, items in groups:
        lines.append(f"* {_item(items[0][1].label, (titles or {}).get(qid))}")
        for published, m, art in sorted(items, key=lambda e: e[0], reverse=True):
            name = outlets.get(art.outlet, art.outlet)
            date = f", {published}" if published else ""
            quotes = " ".join(f'"{p}"' for p in m.passages[:MAX_PASSAGES])
            lines.append(f"** {name}{date}, [{m.url} {art.title or m.url}] ({art.lang})" + (f": {quotes}" if quotes else ""))
    block = "\n".join(lines)
    return f"{INTRO}\n\n{BEGIN}\n{block}\n{END}" + _tail(old_text)


def publish_enrichment(store, en, outlets: dict[str, str], titles: dict | None = None) -> bool:
    """Write the page when its text changed; True when written."""
    return _write(en, ENRICH_TITLE, lambda old: enrichment(store, outlets, old, titles))
