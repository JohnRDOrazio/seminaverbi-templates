"""Routing the kept mentions: to the enrichment list (SeminaVerbi already covers the item), into the queue as a
candidate of the mention's domain (source `press:<outlet>`), or to `waiting` (its domain has no module yet; released
later with the same coverage decision).

A mention keeps its route once set, so routing twice adds neither a second candidate nor a second enrichment entry.
"""
from __future__ import annotations

import sys

from pipeline import domains, stages
from pipeline.domains import common
from pipeline.queue import Row

WAITING = "waiting:"


def _outlet(store, url: str) -> str | None:
    found = store.db.execute("SELECT outlet FROM press_articles WHERE url=?", (url,)).fetchone()
    return found[0] if found else None


def _labels(entity: dict) -> dict:
    return {l: entity["labels"][l]["value"] for l in common.LANGS if entity.get("labels", {}).get(l, {}).get("value")}


def _sitelinks(entity: dict) -> dict:
    return {site: title for site, title in common.entity_sitelinks(entity).items()
            if site.endswith("wiki") and site not in common.NON_WIKIPEDIA}


def _provisional(mention, entity: dict) -> Row:
    return Row(mention.qid, mention.domain, [], _labels(entity), _sitelinks(entity), False)


def _add_candidate(queue, mention, outlet: str, entity: dict, domain: str) -> None:
    """Add the mention's item to the queue under `domain`, or its source and press probability to the row it has."""
    existing = queue.row(mention.qid)
    press = dict(existing.data.get(0, {}).get("press", {})) if existing else {}
    press[outlet] = max(mention.p, press.get(outlet, 0.0))
    data = {"press": press}
    sitelinks = _sitelinks(entity)
    if existing is None:  # what triage reads of a row the domain's own queries did not make
        data |= {"count": len(sitelinks), "instance_of": common.qids(entity, "P31")}
    queue.add(mention.qid, domain, f"press:{outlet}", _labels(entity), sitelinks, data=data)


def route(store, queue, repo, wikis, wikidata) -> dict[str, int]:
    """Route every unrouted mention; the count of each route taken."""
    counts: dict[str, int] = {}
    for mention in store.unrouted():
        outlet = _outlet(store, mention.url)
        if outlet is None:
            print(f"press: mention of {mention.qid} in {mention.url}: no stored article, not routed", file=sys.stderr)
            continue
        entity = wikidata.entity(mention.qid)
        if mention.domain.startswith(WAITING):
            kind = "waiting"
        elif mention.domain not in domains.DOMAINS:
            print(f"press: {mention.qid} in {mention.url}: unknown domain {mention.domain!r}, not routed",
                  file=sys.stderr)
            continue
        elif stages.covered(_provisional(mention, entity), repo, wikis)[0] in ("drop", "translation"):
            kind = "enrichment"
        else:
            _add_candidate(queue, mention, outlet, entity, mention.domain)
            kind = "candidate"
        store.set_route(mention.url, mention.qid, kind)
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def release_waiting(store, queue, domain: str, repo, wikis, wikidata) -> dict[str, int]:
    """Route the `waiting:<domain>` mentions once `domain` has a module (called by `candidates`), with route()'s
    coverage decision: a work SeminaVerbi already covers goes to the enrichment list (never proposed again), any
    other becomes a candidate of `domain`. The count of each route taken."""
    counts: dict[str, int] = {}
    for mention in store.mentions(route="waiting"):
        outlet = _outlet(store, mention.url)
        if mention.domain != WAITING + domain or outlet is None:
            continue
        entity = wikidata.entity(mention.qid)
        row = _provisional(mention, entity)
        row.domain = domain
        if stages.covered(row, repo, wikis)[0] in ("drop", "translation"):
            kind = "enrichment"
        else:
            _add_candidate(queue, mention, outlet, entity, domain)
            kind = "candidate"
        store.set_route(mention.url, mention.qid, kind)
        counts[kind] = counts.get(kind, 0) + 1
    return counts
