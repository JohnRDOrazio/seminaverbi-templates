"""Recognising the same article in two outlets, when one republishes an author's articles from the other.

The text is never kept: a fingerprint is the FINGERPRINT smallest hashes of the article's runs of SHINGLE words, taken
when the article is read, for the outlets of such a pair only. The share of hashes two articles have in common (of the
smallest of both together) estimates how much of their text is the same. Two articles are one when most of the text is
the same, or a good part of it and the title and the issue's date too. The cuts prefer a miss (an article counted
twice) to a merge of different articles (one of them lost), so an article with too little text to compare, a
subscribers-only page for example, is never merged.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

SHINGLE = 5
FINGERPRINT = 64
MIN_SHINGLES = 100  # about as many words
TEXT = 0.5  # the same article by its text alone
TITLED_TEXT = 0.25  # with the same title, and the issue the post names within DAYS of the newspaper's date
TITLE_WORDS = 3
DAYS = 3


def _words(text: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.findall(r"\w+", plain.casefold())


def fingerprint(paragraphs: list[str]) -> str | None:
    """The article's fingerprint, None when it is too short to have one."""
    words = _words(" ".join(paragraphs))
    shingles = {" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}
    if len(shingles) < MIN_SHINGLES:
        return None
    hashes = sorted(hashlib.sha1(s.encode("utf-8")).hexdigest()[:12] for s in shingles)
    return " ".join(hashes[:FINGERPRINT])


def overlap(a: str, b: str) -> float:
    """The estimated share of their text two articles have in common."""
    x, y = set(a.split()), set(b.split())
    smallest = sorted(x | y)[:FINGERPRINT]
    return sum(h in x and h in y for h in smallest) / len(smallest)


def _title(title: str | None) -> list[str]:
    """The title's words, without the site's name after it ("… - L'Osservatore Romano")."""
    return _words(re.sub(r"\s+[-–—|]\s+l['’]osservatore romano\s*$", "", title or "", flags=re.IGNORECASE))


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return abs((date.fromisoformat((a or "")[:10]) - date.fromisoformat((b or "")[:10])).days)
    except ValueError:
        return None


def same_article(post, post_print: str | None, paper, paper_print: str | None) -> bool:
    """Whether `post` (the Article of the outlet that republishes) is `paper` (the other's)."""
    if not post_print or not paper_print or post.lang != paper.lang:
        return False
    common = overlap(post_print, paper_print)
    if common >= TEXT:
        return True
    title, days = _title(post.title), _days(post.published, paper.published)
    return (common >= TITLED_TEXT and len(title) >= TITLE_WORDS and title == _title(paper.title)
            and post.counts_as == paper.outlet and days is not None and days <= DAYS)


def record(store, art, page, outlets: dict[str, dict]) -> str | None:
    """Keep the article's fingerprint when its outlet is one of a pair, and tie it to its copy in the other; the URL of
    a copy already judged, when there is one."""
    first = outlets.get(art.outlet, {}).get("republishes")
    others = [first] if first in outlets else [o["id"] for o in outlets.values() if o.get("republishes") == art.outlet]
    mine = fingerprint(page.paragraphs) if others else None
    if mine is None:
        return None
    store.set_fingerprint(art.url, mine)
    judged = None
    for other in others:
        for url, theirs in store.fingerprints(other):
            found = store.article(url) if overlap(mine, theirs) >= TITLED_TEXT else None
            if found is None:
                continue
            post, paper = (art, found) if first else (found, art)
            prints = (mine, theirs) if first else (theirs, mine)
            if same_article(post, prints[0], paper, prints[1]):
                store.set_same(post.url, paper.url, paper.outlet)
                if found.outcome in ("keep", "drop"):
                    judged = judged or url
                if first:  # a post is one article of the newspaper
                    return judged
    return judged
