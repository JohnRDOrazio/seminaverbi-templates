"""Discovery: turn each outlet's feeds and sitemaps into new article URLs in the press store."""
import re
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

DROPPED_PARAMS = ("utm_", "fbclid", "gclid")
DATE_TAGS = ("publication_date", "pubDate", "date", "published", "updated", "lastmod")
MAX_BODY = 10 * 1024 * 1024
ITEM_TAGS = ("item", "entry", "url", "sitemap")


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


def _link(item) -> str | None:
    for child in item:
        if _local(child.tag) not in ("link", "loc"):
            continue
        if child.get("href") is not None:
            if child.get("rel") in (None, "alternate"):
                return child.get("href").strip()
        elif (child.text or "").strip():
            return child.text.strip()
    return None


def _item_date(item) -> str | None:
    found = {_local(el.tag): el.text for el in item.iter() if el is not item}
    for tag in DATE_TAGS:
        if tag in found and _iso(found[tag]):
            return _iso(found[tag])
    return None


def _parse(xml_text: str) -> tuple[str, list[tuple[str, str | None]]]:
    """(kind, entries); kind is 'index' for a sitemap index, else 'entries'. ValueError if not XML."""
    if len(xml_text) > MAX_BODY:
        raise ValueError("body too large")
    if re.search(r"<!(DOCTYPE|ENTITY)", xml_text, re.IGNORECASE):
        raise ValueError("DTD or entity declarations are refused")
    try:
        root = ET.fromstring(xml_text.lstrip("﻿").strip())
    except ET.ParseError as e:
        raise ValueError(str(e)) from e
    out = []
    for el in root.iter():
        if _local(el.tag) in ITEM_TAGS:
            url = _link(el)
            if url:
                out.append((url, _item_date(el)))
    return ("index" if _local(root.tag) == "sitemapindex" else "entries"), out


def entry_urls(xml_text: str) -> list[tuple[str, str | None]]:
    """(url, ISO date or None) of an RSS, Atom, urlset or news sitemap, or the child sitemaps of an index."""
    return _parse(xml_text)[1]


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


def _fetch(client, outlet: dict, url: str):
    """Response text, or None after reporting; Refused propagates."""
    oid = outlet["id"]
    try:
        r = client.get(oid, url, conditional=True)
    except (requests.RequestException, TimeoutError, ConnectionError, OSError) as e:
        print(f"press: {oid} {url}: {e}", file=sys.stderr)
        return None
    if r is None:
        return None
    if not 200 <= r.status_code < 300:
        print(f"press: {oid} {url}: HTTP {r.status_code}", file=sys.stderr)
        return None
    return r.text


def _candidates(client, outlet: dict):
    """Lazily yield (url, date, lang) for one outlet; stops quietly when the outlet is refused."""
    from pipeline.press.fetch import Refused
    langs = outlet.get("langs") or ["en"]
    pending = []
    for e in outlet.get("entries", []):
        url, lang = (e["url"], e.get("lang")) if isinstance(e, dict) else (e, None)
        pending.append((url, lang or (langs[0] if len(langs) == 1 else None)))
    seen = set()
    try:
        while pending:
            url, lang = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            text = _fetch(client, outlet, url)
            if text is None:
                continue
            try:
                kind, items = _parse(text)
            except ValueError:
                print(f"press: {outlet['id']} {url}: not a feed or sitemap", file=sys.stderr)
                continue
            if kind == "index":
                for child, _ in items:
                    if lang or len(langs) == 1:
                        pending.append((child, lang or langs[0]))
                        continue
                    child_lang = next((x for x in langs if _has_lang(child, x)), None)
                    if child_lang:
                        pending.append((child, child_lang))
                continue
            for item_url, date in items:
                yield item_url, date, lang or _lang_of(item_url, langs, langs[0])
    except Refused as e:
        print(f"press: {outlet['id']} refused: {e.reason}", file=sys.stderr)


def discover(store, client, outlets: list[dict], limit: int) -> dict[str, int]:
    """New articles per outlet, taken round-robin across outlets until `limit` in all."""
    counts = {o["id"]: 0 for o in outlets}
    streams = {o["id"]: (o, _candidates(client, o)) for o in outlets}
    total = 0
    while streams and total < limit:
        for oid in list(streams):
            outlet, stream = streams[oid]
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
