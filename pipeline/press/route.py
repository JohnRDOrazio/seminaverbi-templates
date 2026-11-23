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


def _add_candidate(queue, mention, outlet: str, entity: dict, domain: str, stage0: dict | None = None) -> None:
    """Add the mention's item to the queue under `domain`, or its source and press probability to the row it has.
    `stage0`: the domain's own stage-0 data for a new row (e.g. a literature series' volumes)."""
    existing = queue.row(mention.qid)
    press = dict(existing.data.get(0, {}).get("press", {})) if existing else {}
    press[outlet] = max(mention.p, press.get(outlet, 0.0))
    data = {"press": press}
    sitelinks = _sitelinks(entity)
    if existing is None:  # what triage reads of a row the domain's own queries did not make
        data |= {"count": len(sitelinks), "instance_of": common.qids(entity, "P31")} | (stage0 or {})
    queue.add(mention.qid, domain, f"press:{outlet}", _labels(entity), sitelinks, data=data)


def decide(mention, domain: str, entity: dict, repo, wikis, sparql=None) -> tuple[str, dict]:
    """("enrichment" or "candidate", stage-0 data) for the mention's item under `domain`, with the coverage check
    stage 1 runs: the domain's own `covered` when it has one (literature: a series covered through a volume),
    else stages.covered. A domain module may give `press_stage0(qid, entity, sparql) -> dict`, the stage-0 data
    that check needs for an item none of its queries added (literature: a series' volumes; music and art can
    give their parts the same way); it is asked only when `sparql` is given."""
    module = domains.DOMAINS.get(domain)  # None: a domain not loaded here (tests), stages.covered alone
    hook = getattr(module, "press_stage0", None)
    stage0 = hook(mention.qid, entity, sparql) if hook and sparql is not None else {}
    row = _provisional(mention, entity)
    row.domain = domain
    row.data = {0: stage0}
    covered = getattr(module, "covered", None) or stages.covered
    outcome = covered(row, repo, wikis)[0]
    return ("enrichment" if outcome in ("drop", "translation") else "candidate"), stage0


def route(store, queue, repo, wikis, wikidata, sparql=None) -> dict[str, int]:
    """Route every unrouted mention; the count of each route taken. `sparql` feeds the domains' press_stage0."""
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
        else:
            kind, stage0 = decide(mention, mention.domain, entity, repo, wikis, sparql)
            if kind == "candidate":
                _add_candidate(queue, mention, outlet, entity, mention.domain, stage0)
        store.set_route(mention.url, mention.qid, kind)
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def release_waiting(store, queue, domain: str, repo, wikis, wikidata, sparql=None) -> dict[str, int]:
    """Route the `waiting:<domain>` mentions once `domain` has a module (called by `candidates`), with route()'s
    coverage decision (`decide`): a work SeminaVerbi already covers goes to the enrichment list (never proposed
    again), any other becomes a candidate of `domain`. The count of each route taken."""
    counts: dict[str, int] = {}
    for mention in store.mentions(route="waiting"):
        outlet = _outlet(store, mention.url)
        if mention.domain != WAITING + domain or outlet is None:
            continue
        entity = wikidata.entity(mention.qid)
        kind, stage0 = decide(mention, domain, entity, repo, wikis, sparql)
        if kind == "candidate":
            _add_candidate(queue, mention, outlet, entity, domain, stage0)
        store.set_route(mention.url, mention.qid, kind)
        counts[kind] = counts.get(kind, 0) + 1
    return counts
