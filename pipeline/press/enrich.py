"""The enrichment page: Catholic-press articles about works SeminaVerbi already covers, for editors.

The bot owns the block between the markers; whatever follows the end marker (editors' `{{SV press done|url}}` and
notes) is kept as written, and an entry whose URL is closed there is left out of the block.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from pipeline.press.credit import outlet_name
from pipeline.press.notes import MAX_PASSAGES
from pipeline.publish import _write

ENRICH_TITLE = "Semina Verbi:Press for existing articles"
INTRO = ("Articles of the Catholic press on works SeminaVerbi already covers. Work an article into the entry, "
         "then add <code><nowiki>{{SV press done|<url>}}</nowiki></code> below to close it.")
BEGIN = "<!-- press:begin -->"
END = "<!-- press:end -->"
_DONE = re.compile(r"\{\{\s*(?:(?i:template)\s*:\s*)?[Ss]V[ _]+press[ _]+done\s*\|\s*(?:1\s*=\s*)?([^|}\s]+)\s*(?:\|[^}]*)?\}\}")


_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_LITERAL = re.compile(r"<!--.*?(?:-->|\Z)|<(nowiki|pre)\b[^>]*?(?<!/)>.*?(?:</\1\s*>|\Z)", re.DOTALL | re.IGNORECASE)


def _unescape(m: re.Match) -> str:
    c = chr(int(m.group(1), 16))
    return c if c in _UNRESERVED else "%" + m.group(1).upper()


def _norm(url: str) -> str:
    """A URL as compared: escapes of unreserved characters decoded (others kept, as upper-case hex), http as
    https, no trailing slash."""
    url = _ESCAPE.sub(_unescape, url.strip()).rstrip("/")
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _tail(old_text: str | None) -> str | None:
    """The editors' part of the old page: after the first end marker that follows the first begin marker; the whole
    text when the markers are not both there; None when there is no old text."""
    if not old_text or not old_text.strip():
        return None
    i = old_text.find(BEGIN)
    j = old_text.find(END, i + len(BEGIN)) if i >= 0 else -1
    if j >= 0:
        return old_text[j + len(END):]
    return "\n\n" + old_text


def done_urls(text: str | None) -> set[str]:
    """The (normalised) URLs closed with `{{SV press done|url}}` in the editors' part of the page text; calls
    inside comments, `<nowiki>` or `<pre>` are examples, not calls."""
    return {_norm(u) for u in _DONE.findall(_LITERAL.sub("", _tail(text) or ""))}


_ENT = {"[": "&#91;", "]": "&#93;", "{": "&#123;", "}": "&#125;", "|": "&#124;", "<": "&#60;", ">": "&#62;"}


def _text(s: str) -> str:
    """External text made inert in wikitext: one line, no markup characters, no bot markers."""
    s = " ".join(str(s).replace("press:begin", "press&#58;begin").replace("press:end", "press&#58;end").split())
    return "".join(_ENT.get(c, c) for c in s)


def _quote(s: str) -> str:
    """A passage in quotes inside nowiki; `<` is an entity, so neither `</nowiki>` nor `<!--` survives."""
    s = " ".join(str(s).replace("press:begin", "press&#58;begin").replace("press:end", "press&#58;end").split())
    return '"<nowiki>' + s.replace("&", "&amp;").replace("<", "&lt;") + '</nowiki>"'


def _url(url: str) -> str:
    return quote(url, safe=":/?#@!$&'()*+,;=%~-._")


def _item(label: str, titles: dict | None) -> str:
    links = [f"[[:{_text(lang)}:{_text(title)}]]" for lang, title in sorted((titles or {}).items())]
    return ", ".join(links) if links else _text(label)


def enrichment(store, outlets: dict[str, str], old_text: str | None, titles: dict | None = None) -> str:
    """The page text. `titles` maps a QID to {lang: page title} for the wikis that have the item."""
    done = done_urls(old_text)
    entries: dict[str, list] = {}
    for m in store.mentions(route="enrichment"):
        art = store.article(m.url)
        if _norm(m.url) in done or art is None:
            continue
        entries.setdefault(m.qid, []).append((art.published or "", m, art))
    # newest article first within an item; items by their newest article
    groups = sorted(entries.items(), key=lambda kv: max(e[0] for e in kv[1]), reverse=True)
    lines = []
    for qid, items in groups:
        lines.append(f"* {_item(items[0][1].label, (titles or {}).get(qid))}")
        for published, m, art in sorted(items, key=lambda e: e[0], reverse=True):
            name = _text(outlet_name(art, outlets))
            date = f", {_text(published)}" if published else ""
            quotes = " ".join(_quote(p) for p in m.passages[:MAX_PASSAGES])
            lines.append(f"** {name}{date}, [{_url(m.url)} {_text(art.title or m.url)}] ({_text(art.lang)}{', critical' if m.critical else ''})" + (f": {quotes}" if quotes else ""))
    block = "\n".join(lines)
    return f"{INTRO}\n\n{BEGIN}\n{block}\n{END}" + (_tail(old_text) or "\n")


def publish_enrichment(store, en, outlets: dict[str, str], titles: dict | None = None) -> bool:
    """Write the page when its text changed; True when written."""
    return _write(en, ENRICH_TITLE, lambda old: enrichment(store, outlets, old, titles))
