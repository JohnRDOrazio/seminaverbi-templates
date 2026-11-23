"""The press stages as the command line runs them: discover, filter, link, a dry-run preview of routing, and the
enrichment page's links.

An article is fetched when a stage needs it and kept in memory (as a parsed Page, never stored) for that run only,
so `press run` fetches each article once for filter and link. Article stages in press_articles: `found` (from
discovery) -> `filtered` (outcome `keep`, `drop` below culture_drop or angle_drop, or `skip` when the article is gone, robots.txt
forbids its path or the page is a section or index page, not an article) -> `linked`. A timeout, a 5xx, a refused
outlet or a robots.txt answering 401/403 leaves the article where it was, for the next run.
"""
from __future__ import annotations

import sys
from urllib.parse import quote, urlsplit

import requests
from typesafe_sdk import TypeSafeError

from sync.wiki import SyncError

from pipeline import domains
from pipeline import stages as pipeline_stages
from pipeline.press.credit import credit_date
from pipeline.press.extract import Page, extract, extract_entry, mention_texts, opening
from pipeline.press.fetch import Refused
from pipeline.press.link import link_article
from pipeline.press.outlets import discover
from pipeline.press.route import LOOKUP_ERRORS, WAITING, _provisional, decide, lookup_failed, owner

GONE = (404, 410)
ANGLE_WINDOWS = 3  # texts around mentions asked when the opening does not show the angle
NOT_ARTICLE = "not an article"
# one article's failure (SyncError: Wikidata or WDQS still failing after the retries); the run goes on
ARTICLE_ERRORS = (requests.RequestException, OSError, TypeSafeError, SyncError)


def _error(art, e, counts: dict) -> None:
    """Report an article whose Jev or Wikidata call failed; it stays at its stage for the next run."""
    print(f"press: {art.outlet} {art.url}: {e}", file=sys.stderr)
    counts["errors"] = counts.get("errors", 0) + 1
SV_LANGS = pipeline_stages.LANGS


def run_discover(store, client, pcfg: dict) -> dict[str, int]:
    """New articles per outlet (round-robin, at most max_articles_per_run in all)."""
    return discover(store, client, pcfg["outlets"], pcfg["max_articles_per_run"])


def _text(r) -> str:
    """The page's text; HTML served with no charset is read as UTF-8 when its bytes are UTF-8 (requests would take
    it for Latin-1)."""
    if "charset" not in r.headers.get("Content-Type", "").lower():
        try:
            return r.content.decode("utf-8-sig")
        except (AttributeError, UnicodeDecodeError):
            pass
    return r.text


class Pages:
    """Articles fetched in this run, parsed; `get` answers (page, None), (None, reason to skip for good) or
    (None, None) for a failure worth trying again next run. An outlet with an `article_feed` (a URL with {path}) is
    read there, from the article's own entry: its pages are drawn by script and hold no text."""

    def __init__(self, client, outlets=()):
        self.client, self._pages = client, {}
        self._feeds = {o["id"]: o["article_feed"] for o in outlets if o.get("article_feed")}

    def get(self, art) -> tuple[Page | None, str | None]:
        if art.url in self._pages:
            return self._pages[art.url], None
        feed = self._feeds.get(art.outlet)
        url = feed.replace("{path}", quote(urlsplit(art.url).path, safe="/%")) if feed else art.url
        try:
            r = self.client.get(art.outlet, url)
        except Refused:
            return None, None  # the outlet is refused for the run: in client.refused, reported once
        except (requests.RequestException, OSError) as e:  # timeouts and connection errors included
            print(f"press: {art.outlet} {art.url}: {e}", file=sys.stderr)
            return None, None
        if r is None:  # a robots.txt answering 401/403 blocks the outlet for this run only: try again next run
            return None, (None if art.outlet in self.client._blocked else "robots.txt")
        if not 200 <= r.status_code < 300:
            print(f"press: {art.outlet} {art.url}: HTTP {r.status_code}", file=sys.stderr)
            return None, (f"HTTP {r.status_code}" if r.status_code in GONE else None)
        try:
            page = extract_entry(r.text, art.url, art.lang) if feed else extract(_text(r), art.url, art.lang)
        except ValueError as e:
            print(f"press: {art.outlet} {url}: {e}", file=sys.stderr)
            return None, None
        if page is None:
            return None, "not in the feed"
        if not page.article:  # a section or index page the outlet's sitemap lists among its articles
            print(f"press: {art.outlet} {art.url}: not an article, skipped", file=sys.stderr)
            return None, NOT_ARTICLE
        self._pages[art.url] = page
        return page, None


def _set_meta(store, url: str, page: Page) -> None:
    """Fill the article's title and date from its page where discovery had none; its language is the page's."""
    store.db.execute("UPDATE press_articles SET title=COALESCE(title, ?), published=COALESCE(published, ?), "
                     "lang=COALESCE(?, lang) WHERE url=?", (page.title or None, page.published, page.lang or None, url))
    store.db.commit()


def _set_credit(store, art, page: Page, outlets: dict[str, dict]) -> None:
    """Attribute a republished article to the outlet it says it first appeared in, with that date."""
    outlet = outlets.get(art.outlet, {})
    first = outlets.get(outlet.get("republishes"))
    published = credit_date(page.paragraphs, first["name"]) if first else None
    if published:
        store.set_credit(art.url, f"{outlet['author']}, {first['name']}", published, first["id"])


def _angle(page: Page, url: str, judge, cut: float) -> dict[str, float]:
    """The article's best angle score, and its opening's: the title and opening first, then, below `cut`, at most
    ANGLE_WINDOWS texts around its mentions, until one reaches it."""
    first = best = judge.angle(url, opening(page))
    for text in mention_texts(page, ANGLE_WINDOWS) if best < cut else ():
        best = max(best, judge.angle(url, text))
        if best >= cut:
            break
    return {"angle": best, "angle_opening": first}


def run_filter(store, pages: Pages, judge, pcfg: dict) -> dict[str, int]:
    """Jev's culture question on each found article's title and opening, then, for those it keeps, the faith or
    values angle (below angle_drop: dropped, reason "angle", counted apart). At most max_articles_per_run articles are
    attempted (fetched or judged, failures included; a refused outlet's articles are not asked, so not counted):
    the never-attempted first, then the oldest attempt, so failures cannot starve the new articles."""
    counts: dict[str, int] = {}
    attempted = 0
    outlets = {o["id"]: o for o in pcfg.get("outlets", ())}
    for art in store.articles("found", tried_last=True):
        if attempted >= pcfg["max_articles_per_run"]:
            break
        if art.outlet in pages.client.refused:
            continue  # refused for the run (or its robots.txt refused us): nothing asked, tried again next run
        attempted += 1
        store.attempted(art.url)
        page, skip = pages.get(art)
        if page is None:
            if skip:
                store.set_article(art.url, "filtered", "skip", {"reason": skip})
                counts["skip"] = counts.get("skip", 0) + 1
            continue
        _set_meta(store, art.url, page)
        _set_credit(store, art, page, outlets)
        try:
            data: dict = {"culture": judge.culture(art.url, opening(page))}
            outcome = "drop" if data["culture"] < pcfg["culture_drop"] else "keep"
            if outcome == "keep":
                data |= _angle(page, art.url, judge, pcfg["angle_drop"])
        except ARTICLE_ERRORS as e:
            _error(art, e, counts)
            continue
        label = outcome
        if "angle" in data and data["angle"] < pcfg["angle_drop"]:
            outcome, label, data["reason"] = "drop", "drop (angle)", "angle"
        store.set_article(art.url, "filtered", outcome, data)
        counts[label] = counts.get(label, 0) + 1
    return counts


def run_link(store, pages: Pages, judge, wikidata, sparql, pcfg: dict) -> dict[str, int]:
    """Link the mentions of every kept article; keyed by the stored article URL."""
    counts: dict[str, int] = {"articles": 0, "mentions": 0}
    routes: dict = {}  # class_route by set of P31 classes, for the whole run
    for art in store.articles("filtered"):
        if art.outcome != "keep":
            continue
        page, skip = pages.get(art)
        if page is None:
            if skip:
                store.set_article(art.url, "linked", "skip", art.data | {"reason": skip})
            continue
        try:
            kept = link_article(page, art.outlet, store, judge, wikidata, sparql, pcfg, url=art.url, routes=routes)
        except ARTICLE_ERRORS as e:
            _error(art, e, counts)
            continue
        store.set_article(art.url, "linked", "keep", art.data | {"mentions": len(kept)})
        counts["articles"] += 1
        counts["mentions"] += len(kept)
    return counts


def would_route(store, repo, wikis, wikidata, sparql=None, queue=None) -> list[tuple[object, str]]:
    """(mention, route it would take) for every unrouted mention, by route()'s own decision (under the domain of
    the QID's row in `queue`, when it has one); reads the wikis, writes nothing. A mention whose lookup still fails
    after the clients' retries is reported and left out."""
    out = []
    for m in store.unrouted():
        if m.domain.startswith(WAITING):
            kind = "waiting"
        elif m.domain not in domains.DOMAINS:
            kind = "unknown"
        else:
            under = owner(queue, m.qid, m.domain)
            try:
                kind = (decide(m, under, wikidata.entity(m.qid), repo, wikis, sparql)[0]
                        if under in domains.DOMAINS else "unknown")
            except LOOKUP_ERRORS as e:
                lookup_failed(m, e)
                continue
        out.append((m, kind))
    return out


def preview(store, repo, wikis, wikidata, sparql=None, queue=None) -> list[str]:
    """The dry-run report: each mention's would-be route with its probabilities, then the share that hits an
    existing SeminaVerbi article."""
    routed = would_route(store, repo, wikis, wikidata, sparql, queue)
    lines = []
    for m, kind in routed:
        art = store.article(m.url)
        scores = {"culture": art.data.get("culture"), "angle": art.data.get("angle")} if art else {}
        scores["stance"] = m.stance
        c = "".join(f" {k}={v:.2f}" for k, v in scores.items() if isinstance(v, (int, float)))
        c += " critical" if m.critical else ""
        lines.append(f"  {kind:<10} {m.qid} {m.label!r} ({m.domain}) p={m.p:.2f}{c} {m.url}")
    hits = sum(1 for _, kind in routed if kind == "enrichment")
    share = f"{100 * hits / len(routed):.0f}%" if routed else "-"
    lines.append(f"press dry-run: {hits}/{len(routed)} mentions ({share}) hit existing SeminaVerbi articles")
    return lines


def titles(store, repo, wikidata) -> dict[str, dict[str, str]]:
    """{qid: {lang: title}} of the SeminaVerbi pages of the enrichment items, from the local Wikibase sitelinks."""
    out: dict[str, dict[str, str]] = {}
    for m in store.mentions(route="enrichment"):
        if m.qid in out:
            continue
        item = pipeline_stages._local_item(_provisional(m, wikidata.entity(m.qid)), repo)
        links = repo.item_sitelinks(item)[1] if item else {}
        found = {lang: links[lang] for lang in SV_LANGS if links.get(lang)}
        if found:
            out[m.qid] = found
    return out
