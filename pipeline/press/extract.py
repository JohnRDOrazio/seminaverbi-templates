"""Article extraction: title, date, language, paragraphs and the spans that name works.

Pure parsing of one HTML page with the standard library parser: nothing is fetched,
executed or resolved. Also cuts the short windows Jev reads (at most ~150 words) and the
short passages we may keep (at most 25 words). Malformed markup gives a best-effort Page.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal, NamedTuple
from urllib.parse import unquote, urljoin, urlparse

SKIPPED = {"nav", "footer", "aside", "script", "style", "noscript", "template"}
SKIPPED_BLOCKS = {"nav", "footer", "aside"}  # end the paragraph; the others are left out of it
ITALIC = {"em", "i"}
# what ends a paragraph in a feed entry's content, where the text sits in any element or in none
BLOCKS = {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre", "table", "tr", "td", "th", "section",
          "article", "figure", "figcaption", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
MAX_FEED = 10 * 1024 * 1024
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
    def __init__(self, fragment: bool = False) -> None:
        """`fragment`: the markup is an article's body alone (a feed entry's content): all its text counts, and any
        block element or a double line break ends a paragraph."""
        super().__init__(convert_charrefs=True)
        self.fragment = fragment
        self.breaks = 0
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
        self.buf: list[str] | None = [] if fragment else None
        self.buf_article = False
        self.buf_spans: list[tuple[str, str, tuple[str, str] | None]] = []
        self.open_inline: list[list] = []  # [tag, kind, wiki, start_index_in_text_parts]
        self.parts: list[str] = []

    def _flush(self) -> None:
        if self.fragment:  # an inline element may stay open over several paragraphs: its part in each is a span
            for item in self.open_inline:
                self._span(item[1], item[2], item[3])
                item[3] = 0
            if not "".join(self.buf or []).strip():
                self.buf, self.buf_spans = [], []
                return
        if self.buf is not None:
            self.raw.append(("".join(self.buf), self.buf_article, self.buf_spans))
        self.buf = [] if self.fragment else None
        self.buf_spans = []
        if not self.fragment:
            self.open_inline = []

    def _span(self, kind: str, wiki: tuple[str, str] | None, start: int) -> None:
        if kind and self.buf is not None:
            text = _clean("".join(self.buf[start:]))
            if text:
                self.buf_spans.append((kind, text, wiki))

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
        if self.fragment and tag in BLOCKS:
            self._flush()
        elif tag == "article":
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
            self.breaks += 1
            if self.fragment and self.breaks == 2:
                self._flush()

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
        if self.fragment and tag in BLOCKS:
            self._flush()
        elif tag == "article":
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
                self._span(kind, wiki, start)
                return

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_tag += data
        if self.in_jsonld and self.jsonld:
            self.jsonld[-1] += data
        if self.skip:
            return
        if data.strip():
            self.breaks = 0
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


class _Node(NamedTuple):
    data: dict
    types: set[str]
    prefixes: dict[str, str]


def _node(data: dict, outer: dict[str, str]) -> _Node:
    prefixes = outer | _prefixes(data.get("@context"))
    return _Node(data, _type_names(data, prefixes), prefixes)


def _ld_nodes(blobs: list[str]) -> list[_Node] | None:
    """The nodes of the page's JSON-LD: each block's top-level objects and the members of their @graph, not nested
    values. Control characters inside strings are accepted (an outlet's block had a raw carriage return); None when
    a block is still unreadable, since it may be the article's own."""
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
            found.append(_node(top, {"schema": "https://schema.org/"}))
            found += [_node(n, found[-1].prefixes) for n in _many(top.get("@graph")) if isinstance(n, dict)]
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


def _primary(node: _Node):
    """The node's one mainEntity (a value, or an array of one), None for none, several, or an ItemList."""
    values = _many(node.data.get("mainEntity"))
    if len(values) != 1 or (isinstance(values[0], dict) and "ItemList" in _node(values[0], node.prefixes).types):
        return None
    return values[0]


def _listed(nodes: list[_Node], base: str) -> tuple[set[str], bool]:
    """(the IRIs the nodes list, whether an article is embedded in a listing): LISTING properties, several
    mainEntity values and ItemLists; not breadcrumbs, which name the page itself."""
    found: set[str] = set()
    embedded = False
    stack = [n for n in nodes if "BreadcrumbList" not in n.types]
    while stack:
        node = stack.pop()
        values = [v for key in LISTING for v in _many(node.data.get(key))]
        if "mainEntity" in node.data and _primary(node) is None:
            values += _many(node.data["mainEntity"])
        for value in values:
            found |= _iris(value, base)
            if isinstance(value, dict):
                found |= _iris(value.get("item"), base)
                inner = [_node(v, node.prefixes) for v in (value, value.get("item")) if isinstance(v, dict)]
                embedded = embedded or any(n.types & ARTICLE_TYPES for n in inner)
                stack += [n for n in inner if "BreadcrumbList" not in n.types]
    return found, embedded


def _own_nodes(nodes: list[_Node], urls: set[str], base: str) -> list[_Node]:
    """The nodes that describe this page (and those that name no page at all, such as a bare WebPage)."""
    return [n for n in nodes if _describes(n.data, urls, base)
            or not any(n.data.get(key) for key in ("@id", "url", "mainEntityOfPage"))]


def _main_article(nodes: list[_Node], own: list[_Node], base: str) -> bool:
    """One of the page's own nodes has an article as its one mainEntity, embedded or by reference."""
    for node in own:
        value = _primary(node)
        if isinstance(value, dict) and _node(value, node.prefixes).types & ARTICLE_TYPES:
            return True
        refs = _iris(value, base) if value is not None else set()
        if any(n.types & ARTICLE_TYPES and not refs.isdisjoint(_iris(n.data, base)) for n in nodes):
            return True
    return False


def _ld_verdict(blobs: list[str], urls: set[str], base: str) -> tuple[bool | None, set[str]]:
    """(whether the JSON-LD says the page at `urls`, its address and its canonical one, is an article; None when it
    is inconclusive, and the types it names). An article describing this page decides, then a collection describing
    it, then its main entity, then any article no other node lists; articles only as listed members say it is not
    one. An unreadable block may be the article's own: the page counts as one."""
    nodes = _ld_nodes(blobs)
    if nodes is None:
        return True, set()
    urls = {_address(u) for u in urls}
    own = _own_nodes(nodes, urls, base)
    kinds = set().union(*(n.types for n in nodes))
    if any(n.types & ARTICLE_TYPES and _describes(n.data, urls, base) for n in nodes):
        return True, kinds
    for node in own:  # an embedded main entity that itself names this page describes it
        value = _primary(node)
        if (isinstance(value, dict) and _node(value, node.prefixes).types & ARTICLE_TYPES
                and _describes(value, urls, base)):
            return True, kinds
    if any(n.types & COLLECTION_TYPES for n in own):
        return False, kinds
    if _main_article(nodes, own, base):
        return True, kinds
    listed, embedded = _listed(nodes, base)
    articles = [n for n in nodes if n.types & ARTICLE_TYPES]
    if any((_iris(n.data, base) | _iris(n.data.get("mainEntityOfPage"), base)).isdisjoint(listed) for n in articles):
        return True, kinds
    return (False if articles or embedded else None), kinds


def is_article(verdict: bool | None, ld_types: set[str], og_type: str) -> bool:
    """Whether the page is one article rather than a section or index page: a rejection is for good, so it needs
    evidence. The JSON-LD's verdict (see _ld_verdict) when it has one, else og:type when there is one ("article",
    "video.*"), else the page counts as an article; but one whose only JSON-LD is breadcrumbs, with no og:type, is
    a section (Avvenire's). Measured on one article and one section page of each outlet (2026-11-23): every article
    names an article type (L'Osservatore Romano's says og:type "website"), no section does but KTO's home page,
    which its feed does not list."""
    if verdict is not None:
        return verdict
    og_type = og_type.strip().lower()
    if og_type:
        return og_type == "article" or og_type.startswith("video.")
    return ld_types != {"BreadcrumbList"}


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


def _parsed(html: str, fragment: bool = False) -> _Parser:
    parser = _Parser(fragment)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - best effort on hostile markup
        parser._flush()
    return parser


def _body(parser: _Parser) -> tuple[list[str], list[Span]]:
    """The article's paragraphs and the spans in them."""
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
    return paragraphs, spans


def extract(html: str, url: str, default_lang: str) -> Page:
    parser = _parsed(html)
    paragraphs, spans = _body(parser)
    title = _clean(parser.meta.get("og:title", "")) or _clean(parser.title_tag)
    published = (parser.meta.get("article:published_time", "").strip()
                 or (parser.time_datetime or "").strip()
                 or _jsonld_date(parser.jsonld))
    lang = parser.html_lang.strip()[:2].lower() if parser.html_lang.strip() else ""
    canonical = _canonical(parser.canonical_href, url)
    return Page(title, published or None, lang if lang.isalpha() and len(lang) == 2 else default_lang,
                canonical, paragraphs, spans,
                is_article(*_ld_verdict(parser.jsonld, {url, canonical}, canonical), parser.meta.get("og:type", "")))


def extract_entry(xml: str, url: str, lang: str) -> Page | None:
    """The article at `url` from an Atom feed that holds its entry (or from the entry alone), None when it is not
    there. ValueError when the text is not such a feed. The whole content is the article's body."""
    if len(xml) > MAX_FEED or re.search(r"<!(DOCTYPE|ENTITY)", xml, re.IGNORECASE):
        raise ValueError("not a feed")
    try:
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", xml.lstrip("\ufeff")).strip())
    except ET.ParseError as e:
        raise ValueError(f"not a feed: {e}") from e
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag not in (atom + "feed", atom + "entry"):
        raise ValueError("not a feed")
    for item in root.iter(atom + "entry"):
        links = [link.get("href", "") for link in item.findall(atom + "link")
                 if link.get("rel", "alternate") == "alternate"]
        if _address(url) not in {_address(link) for link in links}:
            continue
        # escaped HTML; a title or content typed xhtml (its markup inline) is not read: Blogger has none
        paragraphs, spans = _body(_parsed(item.findtext(atom + "content") or "", fragment=True))
        title = _clean(item.findtext(atom + "title") or "")
        return Page(title, (item.findtext(atom + "published") or "").strip() or None, lang, url, paragraphs, spans)
    return None


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


MAX_TITLE = 30  # words of the title sent beside a window


def short_title(page: Page) -> str:
    return " ".join(page.title.split()[:MAX_TITLE])


def titled_room(page: Page, words: int = 150) -> int:
    """The words left for a window sent beside the title."""
    return words - len(short_title(page).split())


def mention_texts(page: Page, limit: int, words: int = 150) -> list[str]:
    """At most `limit` texts of the title and the window around a mention, for mentions not wholly in the opening,
    spread over the article (the first, the last, and evenly between)."""
    room = titled_room(page, words)
    ends, total = [], len(page.title.split())
    for paragraph in page.paragraphs:
        total += len(paragraph.split())
        ends.append(total)
    texts: list[str] = []
    for span in sorted(page.spans, key=lambda s: s.paragraph):
        if 0 <= span.paragraph < len(ends) and ends[span.paragraph] > words:
            text = f"{short_title(page)} {window(page.paragraphs, span.paragraph, room, span.text)}".strip()
            if text not in texts:
                texts.append(text)
    if len(texts) <= limit:
        return texts
    if limit == 1:
        return texts[:1]
    return [texts[round(i * (len(texts) - 1) / (limit - 1))] for i in range(limit)]


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
