"""The press stages as the command line runs them: discover, filter, link, a dry-run preview of routing, and the
enrichment page's links.

An article is fetched when a stage needs it and kept in memory (as a parsed Page, never stored) for that run only,
so `press run` fetches each article once for filter and link. Article stages in press_articles: `found` (from
discovery) -> `filtered` (outcome `keep`, `drop` below culture_drop, or `skip` when the article is gone or robots.txt
forbids its path) -> `linked`. A timeout, a 5xx, a refused outlet or a robots.txt answering 401/403 leaves the article
where it was, for the next run.
"""
from __future__ import annotations

import sys

import requests
from typesafe_sdk import TypeSafeError

from sync.wiki import SyncError

from pipeline import domains
from pipeline import stages as pipeline_stages
from pipeline.press.extract import Page, extract, opening
from pipeline.press.fetch import Refused
from pipeline.press.link import link_article
from pipeline.press.outlets import discover
from pipeline.press.route import LOOKUP_ERRORS, WAITING, _provisional, decide, lookup_failed, owner

GONE = (404, 410)
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


class Pages:
    """Articles fetched in this run, parsed; `get` answers (page, None), (None, reason to skip for good) or
    (None, None) for a failure worth trying again next run."""

    def __init__(self, client):
        self.client, self._pages = client, {}

    def get(self, art) -> tuple[Page | None, str | None]:
        if art.url in self._pages:
            return self._pages[art.url], None
        try:
            r = self.client.get(art.outlet, art.url)
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
        page = extract(r.text, art.url, art.lang)
        self._pages[art.url] = page
        return page, None


def _set_meta(store, url: str, page: Page) -> None:
    """Fill the article's title and date from its page where discovery had none; its language is the page's."""
    store.db.execute("UPDATE press_articles SET title=COALESCE(title, ?), published=COALESCE(published, ?), "
                     "lang=COALESCE(?, lang) WHERE url=?", (page.title or None, page.published, page.lang or None, url))
    store.db.commit()


def run_filter(store, pages: Pages, judge, pcfg: dict) -> dict[str, int]:
    """Jev's culture question on each found article's title and opening. At most max_articles_per_run articles are
    attempted (fetched or judged, failures included; a refused outlet's articles are not asked, so not counted):
    the never-attempted first, then the oldest attempt, so failures cannot starve the new articles."""
    counts: dict[str, int] = {}
    attempted = 0
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
        try:
            p = judge.culture(art.url, opening(page))
        except ARTICLE_ERRORS as e:
            _error(art, e, counts)
            continue
        outcome = "drop" if p < pcfg["culture_drop"] else "keep"
        store.set_article(art.url, "filtered", outcome, {"culture": p})
        counts[outcome] = counts.get(outcome, 0) + 1
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
        culture = (art.data.get("culture") if art else None)
        c = f" culture={culture:.2f}" if isinstance(culture, (int, float)) else ""
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
