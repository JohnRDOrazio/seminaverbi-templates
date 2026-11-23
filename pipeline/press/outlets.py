"""Discovery: turn each outlet's feeds and sitemaps into new article URLs in the press store."""
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

DROPPED_PARAMS = ("utm_", "fbclid", "gclid")
DATE_TAGS = ("publication_date", "pubDate", "date", "published", "updated", "lastmod")
MAX_BODY = 10 * 1024 * 1024
ITEM_TAGS = ("item", "entry", "url", "sitemap")
WALK_PAGE = 25  # posts per page of a walked feed
WALK_MAX_PAGE = 400  # the largest page asked, when more than WALK_PAGE posts changed in the same second
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


def canonical(url: str) -> str:
    p = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.startswith(DROPPED_PARAMS[0]) and k not in DROPPED_PARAMS[1:]]
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), urlencode(query), ""))


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _iso(text: str | None) -> str | None:
    text = (text or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


def _base(el, base: str) -> str:
    return urljoin(base, el.get(XML_BASE).strip()) if el.get(XML_BASE) else base


def _resolve(ref: str, base: str) -> str:
    """A relative reference resolved against `base`; an absolute one unchanged."""
    return ref if urlsplit(ref).scheme or not base else urljoin(base, ref)


def _link(item, base: str = "") -> str | None:
    for child in item:
        if _local(child.tag) not in ("link", "loc"):
            continue
        if child.get("href") is not None:
            if child.get("rel") in (None, "alternate"):
                return _resolve(child.get("href").strip(), _base(child, base))
        elif (child.text or "").strip():
            return _resolve(child.text.strip(), _base(child, base))
    return None


def _item_date(item) -> str | None:
    found = {_local(el.tag): el.text for el in item.iter() if el is not item}
    for tag in DATE_TAGS:
        if tag in found and _iso(found[tag]):
            return _iso(found[tag])
    return None


def _root(xml_text: str):
    """The root element; ValueError if not XML, too large, or with a DTD."""
    if len(xml_text) > MAX_BODY:
        raise ValueError("body too large")
    if re.search(r"<!(DOCTYPE|ENTITY)", xml_text, re.IGNORECASE):
        raise ValueError("DTD or entity declarations are refused")
    try:
        return ET.fromstring(xml_text.lstrip("﻿").strip())
    except ET.ParseError as e:
        raise ValueError(str(e)) from e


def _parse(xml_text: str, base: str = "") -> tuple[str, list[tuple[str, str | None]]]:
    """(kind, entries); kind is 'index' for a sitemap index, else 'entries'. ValueError if not XML. Relative links
    resolve against `base` (the response's URL) and any xml:base on the way down."""
    root = _root(xml_text)
    out = []
    stack = [(root, base)]
    while stack:  # document order, each element with its xml:base
        el, b = stack.pop()
        b = _base(el, b)
        if _local(el.tag) in ITEM_TAGS:
            url = _link(el, b)
            if url:
                out.append((url, _item_date(el)))
        stack.extend((child, b) for child in reversed(el))
    return ("index" if _local(root.tag) == "sitemapindex" else "entries"), out


def entry_urls(xml_text: str, base: str = "") -> list[tuple[str, str | None]]:
    """(url, ISO date or None) of an RSS, Atom, urlset or news sitemap, or the child sitemaps of an index."""
    return _parse(xml_text, base)[1]


def _has_lang(url: str, lang: str) -> bool:
    return re.search(rf"(?<![a-z]){lang}(?![a-z])", urlsplit(url).path.lower()) is not None


def _lang_of(url: str, langs: list[str], default: str) -> str:
    return next((lang for lang in langs if _has_lang(url, lang)), default)


def _wanted(url: str, date: str | None, outlet: dict) -> bool:
    paths = outlet.get("culture_paths") or []
    if paths and not any(p in urlsplit(url).path for p in paths):
        return False
    since = outlet.get("since")
    return not (since and date and date < since)


def _fetch(client, outlet: dict, url: str, conditional: bool = True):
    """("ok", response), ("unchanged", None) for a 304 or a robots.txt refusal, or ("failed", None) after
    reporting; Refused propagates."""
    oid = outlet["id"]
    try:
        r = client.get(oid, url, conditional=conditional)
    except (requests.RequestException, TimeoutError, ConnectionError, OSError) as e:
        print(f"press: {oid} {url}: {e}", file=sys.stderr)
        return "failed", None
    if r is None:
        return "unchanged", None
    if not 200 <= r.status_code < 300:
        print(f"press: {oid} {url}: HTTP {r.status_code}", file=sys.stderr)
        return "failed", None
    return "ok", r


def _walk_page(xml_text: str, base: str) -> tuple[list[tuple[str, str | None, datetime]], bool]:
    """((url, date, when last changed) of an Atom page's entries, whether the feed has more). ValueError when it is
    not an Atom feed or an entry has no link or no dated change."""
    root = _root(xml_text)
    if _local(root.tag) != "feed":
        raise ValueError("not an Atom feed")
    items = []
    for el in root:
        if _local(el.tag) == "entry":
            url = _link(el, base)
            changed = next((c.text for c in el if _local(c.tag) == "updated"), None)
            when = datetime.fromisoformat((changed or "").strip())
            if not url or when.tzinfo is None:
                raise ValueError("an entry with no link or no dated change")
            items.append((url, _item_date(el), when))
    return items, any(_local(el.tag) == "link" and el.get("rel") == "next" for el in root)


def _walk_url(url: str, size: int, before: datetime | None) -> str:
    """The feed's page of `size` entries, the last changed first; `before`: those changed up to then. The cut is a
    second later than `before`, so it overlaps the page above whether or not the server includes the boundary."""
    q = {"orderby": "updated", "max-results": size}
    if before is not None:
        q["updated-max"] = (before + timedelta(seconds=1)).isoformat(timespec="seconds")
    return f"{url}{'&' if urlsplit(url).query else '?'}{urlencode(q)}"


class _Walk:
    """One feed whose whole archive is read, page after page over successive runs, then only what is new.

    The feed is read newest change first and cut by date, never by offset, so posts added or removed meanwhile shift
    nothing. State kept (PressStore.walk): `mark`, the newest change seen when the last complete pass from the top
    began; `cursor`, the date down to which the archive has been read; `done`. `new()` reads from the top down to
    the mark (a post that is new, edited, dated back or given the label later is there, since that changes it);
    `archive()` reads on from the cursor. The cursor and the mark move only when every entry above them has been
    taken, so a run stopped part-way reads the page it was in again, and the store drops the posts it knows."""

    def __init__(self, store, client, outlet: dict, url: str):
        self.store, self.client, self.outlet, self.url = store, client, outlet, url
        self.state = store.walk(url)

    def _save(self, **changes) -> None:
        self.state = self.state | changes
        self.store.set_walk(self.url, self.state)

    def _pages(self, before: datetime | None, conditional: bool = False):
        """Yields (page URL, response, items, oldest change, whether it is the last page), going down from `before`
        (None: the top); ends early, with nothing more, when a page fails."""
        size = WALK_PAGE
        while True:
            url = _walk_url(self.url, size, before)
            _, r = _fetch(self.client, self.outlet, url, conditional and before is None)
            if r is None:
                return
            try:
                items, more = _walk_page(r.text, getattr(r, "url", None) or url)
            except ValueError as e:
                print(f"press: {self.outlet['id']} {url}: not a feed that can be walked ({e})", file=sys.stderr)
                return
            oldest = min((when for _, _, when in items), default=None)
            yield url, r, items, oldest, not more or oldest is None
            if not more or oldest is None:
                return
            if before is None or oldest < before:
                before, size = oldest, WALK_PAGE
            elif size >= WALK_MAX_PAGE:
                print(f"press: {self.outlet['id']} {url}: more posts changed in the same second than a page holds",
                      file=sys.stderr)
                return
            else:
                size = min(size * 2, WALK_MAX_PAGE)

    def new(self):
        """(url, date) of the posts new or changed since the last pass; nothing before the walk has begun. The top
        page's validators are kept only when the pass is complete, so a 304 hides no unread page."""
        if not self.state:
            return
        mark, first = datetime.fromisoformat(self.state["mark"]), None
        for url, r, items, oldest, last in self._pages(None, conditional=True):
            first = first or (url, r, max((when for _, _, when in items), default=mark))
            for item_url, date, _ in items:
                yield item_url, date
            if last or oldest < mark:
                self._save(mark=max(mark, first[2]).isoformat())
                self.client.remember(*first[:2])
                return

    def archive(self):
        """(url, date) of the posts not reached yet, from where the walk stopped."""
        if self.state.get("done"):
            return
        cursor = self.state.get("cursor")
        for _, _, items, oldest, last in self._pages(datetime.fromisoformat(cursor) if cursor else None):
            for item_url, date, _ in items:
                yield item_url, date
            if not items and not self.state:
                return  # an empty feed: begin again next run
            changes = {"done": last}
            if oldest is not None:
                changes["cursor"] = oldest.isoformat()
            if not self.state:  # what changes from now on is newer than the first page
                changes["mark"] = max(when for _, _, when in items).isoformat()
            self._save(**changes)


def _walks(client, outlet: dict, store):
    """(url, date, lang) from the outlet's walked feeds: what is new in each first, then their archives."""
    walks = [(_Walk(store, client, outlet, e["url"]), e.get("lang") or (outlet.get("langs") or ["en"])[0])
             for e in outlet.get("entries", []) if isinstance(e, dict) and e.get("walk")]
    for read in (_Walk.new, _Walk.archive):
        for walk, lang in walks:
            for url, date in read(walk):
                yield url, date, lang


def _candidates(client, outlet: dict, store=None):
    """Lazily yield (url, date, lang) for one outlet; stops quietly when the outlet is refused.

    The feeds' validators (ETag, Last-Modified) are stored only when the stream is read to its end, and not for
    a feed that failed. A sitemap index keeps none: it is fetched in full every run, since a child can hold new
    articles while the index is unchanged."""
    from pipeline.press.fetch import Refused
    langs = outlet.get("langs") or ["en"]
    pending = []
    for e in outlet.get("entries", []):
        if isinstance(e, dict) and e.get("walk"):
            continue
        url, lang = (e["url"], e.get("lang")) if isinstance(e, dict) else (e, None)
        pending.append((url, lang or (langs[0] if len(langs) == 1 else None), None))
    seen = set()
    read: dict[str, object] = {}  # url -> its 2xx response
    parent: dict[str, str | None] = {}
    failed: set[str] = set()

    def fail(url):
        while url is not None and url not in failed:
            failed.add(url)
            url = parent.get(url)

    try:
        while pending:
            url, lang, up = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            parent[url] = up
            state, r = _fetch(client, outlet, url)
            if state == "failed":
                fail(url)
            if r is None:
                continue
            try:
                kind, items = _parse(r.text, getattr(r, "url", None) or url)
            except ValueError:
                print(f"press: {outlet['id']} {url}: not a feed or sitemap", file=sys.stderr)
                fail(url)
                continue
            if kind == "index":
                for child, _ in items:
                    if lang or len(langs) == 1:
                        pending.append((child, lang or langs[0], url))
                        continue
                    child_lang = next((x for x in langs if _has_lang(child, x)), None)
                    if child_lang:
                        pending.append((child, child_lang, url))
                continue
            read[url] = r
            for item_url, date in items:
                yield item_url, date, lang or _lang_of(item_url, langs, langs[0])
        yield from _walks(client, outlet, store)
    except Refused as e:
        print(f"press: {outlet['id']} refused: {e.reason}", file=sys.stderr)
        return
    for url, r in read.items():
        if url not in failed:
            client.remember(url, r)


def discover(store, client, outlets: list[dict], limit: int) -> dict[str, int]:
    """New articles per outlet, taken round-robin across outlets until `limit` in all, and at most `max_per_run` of
    an outlet that has one."""
    counts = {o["id"]: 0 for o in outlets}
    streams = {o["id"]: (o, _candidates(client, o, store)) for o in outlets}
    total = 0
    while streams and total < limit:
        for oid in list(streams):
            outlet, stream = streams[oid]
            if counts[oid] >= outlet.get("max_per_run", limit):
                del streams[oid]
                continue
            for url, date, lang in stream:
                if not _wanted(url, date, outlet):
                    continue
                if store.add_article(canonical(url), oid, lang, published=date):
                    counts[oid] += 1
                    total += 1
                    break
            else:
                del streams[oid]
            if total >= limit:
                break
    return counts
