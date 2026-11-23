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
SKIPPED_BLOCKS = {"nav", "footer", "aside"}  # end the paragraph; the others are left out of it
ITALIC = {"em", "i"}
WIKI_HOST = re.compile(r"^(?:([a-z]{2,3}(?:-[a-z]+)?)\.)(?:m\.)?wikipedia\.org$")
QUOTED = re.compile(r"«([^»]+)»|“([^”]+)”|\"([^\"]+)\"")
# schema.org types of a page that is one article (Article and its subtypes) or one programme (KTO's video pages)
ARTICLE_TYPES = {"Article", "NewsArticle", "AnalysisNewsArticle", "BackgroundNewsArticle", "OpinionNewsArticle",
                 "ReportageNewsArticle", "ReviewNewsArticle", "BlogPosting", "LiveBlogPosting", "Report",
                 "ScholarlyArticle", "TechArticle", "SatiricalArticle", "VideoObject"}
COLLECTION_TYPES = {"CollectionPage", "SearchResultsPage", "MediaGallery", "ImageGallery", "VideoGallery"}


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
    article: bool = True  # False for a section or index page (see is_article)


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
            if tag in SKIPPED_BLOCKS:
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
            found = walk(json.loads(blob, strict=False))
        except (ValueError, RecursionError):
            continue
        if found:
            return found
    return None


SCHEMA_IRI = re.compile(r"^https?://(?:www\.)?schema\.org/(?:#)?(.+)$", re.IGNORECASE)
LISTING = ("hasPart", "itemListElement", "blogPost", "blogPosts")  # what a page lists, as against what it is


def _many(value) -> list:
    return value if isinstance(value, list) else [value]


def _prefixes(context) -> dict[str, str]:
    """The prefixes a JSON-LD @context declares."""
    found: dict[str, str] = {}
    for part in _many(context):
        for term, iri in part.items() if isinstance(part, dict) else ():
            iri = iri.get("@id") if isinstance(iri, dict) else iri
            if isinstance(iri, str):
                found[term] = iri
    return found


def _type_names(node: dict, prefixes: dict[str, str]) -> set[str]:
    """The node's types, schema.org ones by their bare name ("schema:X" and full IRIs too); others unchanged."""
    found = set()
    for kind in _many(node.get("@type")):
        if not isinstance(kind, str) or not kind.strip():
            continue
        kind = kind.strip()
        prefix, colon, name = kind.partition(":")
        if colon and not name.startswith("//") and prefix in prefixes:
            iri = prefixes[prefix]
            kind = iri + ("" if iri.endswith(("/", "#")) else "/") + name
        m = SCHEMA_IRI.match(kind)
        found.add(m.group(1) if m else kind)
    return found


def _ld_nodes(blobs: list[str]) -> list[tuple[dict, set[str]]] | None:
    """(node, type names) of the page's JSON-LD: each block's top-level objects and the members of their @graph,
    not nested values. Control characters inside strings are accepted (an outlet's block had a raw carriage
    return); None when a block is still unreadable, since it may be the article's own."""
    found = []
    for blob in blobs:
        if not blob.strip():
            continue
        try:
            data = json.loads(blob, strict=False)
        except (ValueError, RecursionError):
            return None
        for top in _many(data):
            if not isinstance(top, dict):
                continue
            outer = {"schema": "https://schema.org/"} | _prefixes(top.get("@context"))
            found.append((top, _type_names(top, outer)))
            for node in _many(top.get("@graph")):
                if isinstance(node, dict):
                    found.append((node, _type_names(node, outer | _prefixes(node.get("@context")))))
    return found


def _iris(value, base: str) -> set[str]:
    """The absolute IRIs a value (a string, or an object by its @id and url, or a list of them) refers to."""
    found = set()
    for v in _many(value):
        for ref in (v.get("@id"), v.get("url")) if isinstance(v, dict) else (v,):
            if isinstance(ref, str) and ref.strip():
                try:
                    found.add(urljoin(base, ref.strip()))
                except ValueError:
                    pass
    return found


def _address(iri: str) -> str:
    """An IRI as a page address, for comparison: no scheme, www., fragment or final slash, percent-decoded."""
    iri = re.sub(r"^https?://(?:www\.)?", "", iri.partition("#")[0], flags=re.IGNORECASE)
    return unquote(iri).rstrip("/")


def _describes(node: dict, urls: set[str], base: str) -> bool:
    """The node is the page itself (its @id without fragment, or url) or has it as its mainEntityOfPage."""
    own = _iris(node.get("url"), base) | {i for i in _iris(node.get("@id"), base) if "#" not in i}
    return not urls.isdisjoint(_address(i) for i in own | _iris(node.get("mainEntityOfPage"), base))


def _listed(nodes: list[tuple[dict, set[str]]], base: str) -> set[str]:
    """The IRIs the nodes list (LISTING, or several mainEntity values), breadcrumbs aside: they name the page."""
    found: set[str] = set()
    stack = [n for n, types in nodes if "BreadcrumbList" not in types]
    while stack:
        node = stack.pop()
        for key in LISTING + (("mainEntity",) if isinstance(node.get("mainEntity"), list) else ()):
            for value in _many(node.get(key)):
                found |= _iris(value, base)
                if isinstance(value, dict):
                    found |= _iris(value.get("item"), base)
                    stack.append(value)
        main = node.get("mainEntity")
        if isinstance(main, dict) and "BreadcrumbList" not in _many(main.get("@type")):
            stack.append(main)
    return found


def _ld_types(blobs: list[str], urls: set[str], base: str) -> set[str] | None:
    """The types the page's JSON-LD gives the page at `urls` (its address, and its canonical one), None when a block
    is unreadable. An article, or a collection, that describes this page decides; otherwise every node counts but
    those another node lists."""
    nodes = _ld_nodes(blobs)
    if nodes is None:
        return None
    urls = {_address(u) for u in urls}
    own = [types for node, types in nodes if _describes(node, urls, base)]
    for deciding in (ARTICLE_TYPES, COLLECTION_TYPES):
        for types in own:
            if types & deciding:
                return types & deciding
    listed = _listed(nodes, base)
    found: set[str] = set()
    for node, types in nodes:
        names = _iris(node, base) | _iris(node.get("mainEntityOfPage"), base)
        if names.isdisjoint(listed):
            found |= types
    return found


def is_article(ld_types: set[str] | None, og_type: str) -> bool:
    """Whether the page is one article rather than a section or index page. Its JSON-LD decides when it has any
    (an article type, see _ld_types), else its og:type when it has one ("article", "video.*"); a page with
    neither signal, or with an unreadable JSON-LD block (`ld_types` None), counts as an article. Measured on one
    article and one section page of each outlet (2026-11-23): every article names an article type (L'Osservatore
    Romano's says og:type "website"), no section does but KTO's home page, which its feed does not list."""
    if ld_types is None:
        return True
    if ld_types:
        return not ARTICLE_TYPES.isdisjoint(ld_types)
    og_type = og_type.strip().lower()
    if og_type:
        return og_type == "article" or og_type.startswith("video.")
    return True


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
    canonical = _canonical(parser.canonical_href, url)
    return Page(title, published or None, lang if lang.isalpha() and len(lang) == 2 else default_lang,
                canonical, paragraphs, spans,
                is_article(_ld_types(parser.jsonld, {url, canonical}, canonical), parser.meta.get("og:type", "")))


def _norm(token: str) -> str:
    return re.sub(r"^\W+|\W+$", "", token).casefold()


def locate(tokens: list[str], needle: str) -> tuple[int, int] | None:
    """(first token, token count) of `needle` among whitespace `tokens`, casefolded: first as whole words with
    punctuation around them ignored ("*The Mission*," matches "The Mission"), then inside tokens ("dell'Infinito"
    holds "Infinito"); None when the needle is not there."""
    target = [t for t in (_norm(x) for x in needle.split()) if t]
    n = len(target)
    if n:
        low = [_norm(t) for t in tokens]
        for i in range(len(tokens) - n + 1):
            if low[i:i + n] == target:
                return i, n
    flat = " ".join(needle.split()).casefold()
    if not flat:
        return None
    at = " ".join(tokens).casefold().find(flat)
    if at < 0:
        return None
    ends, pos = [], 0
    for t in tokens:
        pos += len(t.casefold())
        ends.append(pos)
        pos += 1
    first = next(i for i, end in enumerate(ends) if end > at)
    last = next(i for i, end in enumerate(ends) if end >= at + len(flat))
    return first, last - first + 1


def window(paragraphs: list[str], index: int, words: int = 150, needle: str | None = None) -> str:
    """The paragraph with the mention plus its neighbours, cut to `words` words; a paragraph longer than that is
    cut around `needle` (centred on it), or to its first words when the needle is not given or not found."""
    if not 0 <= index < len(paragraphs):
        return ""
    cur = paragraphs[index].split()
    if len(cur) >= words:
        found = locate(cur, needle) if needle else None
        if found is None:
            return " ".join(cur[:words])
        start, n = found
        lo = max(0, start + n // 2 - words // 2)
        hi = min(len(cur), lo + words)
        return " ".join(cur[max(0, hi - words):hi])
    prev = paragraphs[index - 1].split() if index > 0 else []
    nxt = paragraphs[index + 1].split() if index + 1 < len(paragraphs) else []
    budget = words - len(cur)
    take_prev = min(len(prev), budget // 2)
    take_next = min(len(nxt), budget - take_prev)
    take_prev = min(len(prev), budget - take_next)
    head = prev[len(prev) - take_prev:] if take_prev else []
    return " ".join(head + cur + nxt[:take_next])


def passage(paragraph: str, needle: str, words: int = 25) -> str:
    """At most `words` words around `needle`, with … at cut ends; the paragraph's first words when the needle is
    not in it."""
    toks = paragraph.split()
    found = locate(toks, needle)
    start, n = found if found else (0, 0)
    n = min(n, words)
    left = max(0, (words - n) // 2)
    lo = max(0, start - left)
    hi = min(len(toks), lo + words)
    lo = max(0, hi - words)
    if start >= hi:  # needle longer than window: anchor on it
        lo, hi = start, min(len(toks), start + words)
    cut = toks[lo:hi]
    # The ellipsis is attached to the neighbouring word, never a token of its own.
    if lo > 0:
        cut[0] = "…" + cut[0]
    if hi < len(toks):
        cut[-1] = cut[-1] + "…"
    return " ".join(cut)


def opening(page: Page, words: int = 150) -> str:
    """Title plus first words of the body, for the culture filter."""
    body = " ".join(page.paragraphs).split()
    title = page.title.split()
    return " ".join((title + body)[:words])
