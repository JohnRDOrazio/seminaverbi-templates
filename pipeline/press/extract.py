"""Article extraction: title, date, language, paragraphs and the spans that name works.

Pure parsing of one HTML page with the standard library parser: nothing is fetched,
executed or resolved. Also cuts the short windows Jev reads (at most ~150 words) and the
short passages we may keep (at most 25 words). Malformed markup gives a best-effort Page.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse

SKIPPED = {"nav", "footer", "aside", "script", "style", "noscript", "template"}
ITALIC = {"em", "i"}
WIKI_HOST = re.compile(r"^(?:([a-z]{2,3}(?:-[a-z]+)?)\.)(?:m\.)?wikipedia\.org$")
QUOTED = re.compile(r"«([^»]+)»|“([^”]+)”|\"([^\"]+)\"")


@dataclass
class Span:
    text: str
    paragraph: int
    kind: Literal["italic", "quoted", "wikilink"]
    wiki: tuple[str, str] | None = None


@dataclass
class Page:
    title: str
    published: str | None
    lang: str
    canonical: str
    paragraphs: list[str] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.split())


def wiki_target(href: str) -> tuple[str, str] | None:
    """(lang, title) for a link to <lang>.wikipedia.org/wiki/<title>, else None."""
    try:
        u = urlparse(href.strip())
    except ValueError:
        return None
    m = WIKI_HOST.match((u.hostname or "").lower())
    if not m or u.scheme not in ("http", "https", "") or not u.path.startswith("/wiki/"):
        return None
    title = unquote(u.path[len("/wiki/"):]).replace("_", " ").strip()
    return (m.group(1), title) if title else None


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_tag = ""
        self.time_datetime: str | None = None
        self.jsonld: list[str] = []
        self.html_lang = ""
        self.canonical_href = ""
        self.skip = 0
        self.article = 0
        self.seen_article = False
        self.in_title = False
        self.in_jsonld = False
        # raw paragraphs: (text, in_article, [(kind, text, wiki)])
        self.raw: list[tuple[str, bool, list[tuple[str, str, tuple[str, str] | None]]]] = []
        self.buf: list[str] | None = None
        self.buf_article = False
        self.buf_spans: list[tuple[str, str, tuple[str, str] | None]] = []
        self.open_inline: list[list] = []  # [tag, kind, wiki, start_index_in_text_parts]
        self.parts: list[str] = []

    def _flush(self) -> None:
        if self.buf is not None:
            self.raw.append(("".join(self.buf), self.buf_article, self.buf_spans))
        self.buf = None
        self.buf_spans = []
        self.open_inline = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.html_lang = a["lang"]
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical_href = self.canonical_href or a.get("href", "")
        elif tag == "time" and a.get("datetime") and self.time_datetime is None:
            self.time_datetime = a["datetime"]
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld.append("")
        if tag in SKIPPED:
            self._flush()
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "article":
            self.article += 1
            self.seen_article = True
        elif tag == "p":
            self._flush()
            self.buf = []
            self.buf_article = self.article > 0
        elif self.buf is not None and tag in ITALIC:
            self.open_inline.append([tag, "italic", None, len(self.buf)])
        elif self.buf is not None and tag == "a":
            wiki = wiki_target(a.get("href", ""))
            self.open_inline.append([tag, "wikilink" if wiki else "", wiki, len(self.buf)])
        elif self.buf is not None and tag == "br":
            self.buf.append(" ")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag in ("meta", "link", "br"):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag == "script":
            self.in_jsonld = False
        if tag in SKIPPED:
            if self.skip:
                self.skip -= 1
            return
        if self.skip:
            return
        if tag == "article":
            self._flush()
            self.article = max(0, self.article - 1)
        elif tag == "p":
            self._flush()
        elif tag in ITALIC or tag == "a":
            self._close_inline(tag)

    def _close_inline(self, tag: str) -> None:
        for i in range(len(self.open_inline) - 1, -1, -1):
            if self.open_inline[i][0] == tag:
                _, kind, wiki, start = self.open_inline.pop(i)
                if kind and self.buf is not None:
                    text = _clean("".join(self.buf[start:]))
                    if text:
                        self.buf_spans.append((kind, text, wiki))
                return

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_tag += data
        if self.in_jsonld and self.jsonld:
            self.jsonld[-1] += data
        if self.skip:
            return
        if self.buf is not None:
            self.buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _jsonld_date(blobs: list[str]) -> str | None:
    def walk(node):
        if isinstance(node, dict):
            v = node.get("datePublished")
            if isinstance(v, str) and v.strip():
                return v.strip()
            for child in node.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(node, list):
            for child in node:
                found = walk(child)
                if found:
                    return found
        return None

    for blob in blobs:
        try:
            found = walk(json.loads(blob))
        except (ValueError, RecursionError):
            continue
        if found:
            return found
    return None


def _canonical(href: str, url: str) -> str:
    href = href.strip()
    if href:
        try:
            joined = urljoin(url, href)
            if urlparse(joined).scheme in ("http", "https"):
                return joined
        except ValueError:
            pass
    return url


def quoted_spans(paragraph: str) -> list[str]:
    """Quoted titles: «…», “…”, "…" of 1-8 words starting with a capital."""
    found = []
    for m in QUOTED.finditer(paragraph):
        inner = _clean(next(g for g in m.groups() if g is not None))
        words = inner.split()
        if 1 <= len(words) <= 8 and inner[0].isupper():
            found.append(inner)
    return found


def extract(html: str, url: str, default_lang: str) -> Page:
    parser = _Parser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - best effort on hostile markup
        parser._flush()
    use = [r for r in parser.raw if r[1]] if parser.seen_article and any(r[1] for r in parser.raw) else parser.raw

    paragraphs: list[str] = []
    spans: list[Span] = []
    for text, _in_article, raw_spans in use:
        clean = _clean(text)
        if not clean:
            continue
        idx = len(paragraphs)
        paragraphs.append(clean)
        for kind, stext, wiki in raw_spans:
            spans.append(Span(stext, idx, kind, wiki))  # type: ignore[arg-type]
        for q in quoted_spans(clean):
            spans.append(Span(q, idx, "quoted"))

    title = _clean(parser.meta.get("og:title", "")) or _clean(parser.title_tag)
    published = (parser.meta.get("article:published_time", "").strip()
                 or (parser.time_datetime or "").strip()
                 or _jsonld_date(parser.jsonld))
    lang = parser.html_lang.strip()[:2].lower() if parser.html_lang.strip() else ""
    return Page(title, published or None, lang if lang.isalpha() and len(lang) == 2 else default_lang,
                _canonical(parser.canonical_href, url), paragraphs, spans)


def window(paragraphs: list[str], index: int, words: int = 150) -> str:
    """The paragraph with the mention plus its neighbours, cut to `words` words."""
    if not 0 <= index < len(paragraphs):
        return ""
    cur = paragraphs[index].split()
    if len(cur) >= words:
        return " ".join(cur[:words])
    prev = paragraphs[index - 1].split() if index > 0 else []
    nxt = paragraphs[index + 1].split() if index + 1 < len(paragraphs) else []
    budget = words - len(cur)
    take_prev = min(len(prev), budget // 2)
    take_next = min(len(nxt), budget - take_prev)
    take_prev = min(len(prev), budget - take_next)
    head = prev[len(prev) - take_prev:] if take_prev else []
    return " ".join(head + cur + nxt[:take_next])


def passage(paragraph: str, needle: str, words: int = 25) -> str:
    """At most `words` words around `needle`, with … at cut ends."""
    toks = paragraph.split()
    n = len(needle.split())
    start = None
    low = [t.lower() for t in toks]
    target = [t.lower() for t in needle.split()]
    for i in range(len(toks) - n + 1):
        if n and low[i:i + n] == target:
            start = i
            break
    if start is None:
        for i, t in enumerate(low):  # needle inside a token (punctuation attached)
            if target and target[0] in t:
                start = i
                break
    if start is None:
        start = 0
        n = 0
    n = min(n, words)
    left = max(0, (words - n) // 2)
    lo = max(0, start - left)
    hi = min(len(toks), lo + words)
    lo = max(0, hi - words)
    if start >= hi:  # needle longer than window: anchor on it
        lo, hi = start, min(len(toks), start + words)
    text = " ".join(toks[lo:hi])
    return ("… " if lo > 0 else "") + text + (" …" if hi < len(toks) else "")


def opening(page: Page, words: int = 150) -> str:
    """Title plus first words of the body, for the culture filter."""
    body = " ".join(page.paragraphs).split()
    title = page.title.split()
    return " ".join((title + body)[:words])
