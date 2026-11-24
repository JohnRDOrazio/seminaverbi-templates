"""Routing the kept mentions: to the enrichment list (SeminaVerbi already covers the item), into the queue as a
candidate of the mention's domain (source `press:<outlet>`), or to `waiting` (its domain has no module yet; released
later with the same coverage decision). A mention whose article is neutral on the item or
only critical of it never reaches the queue (route `dropped`), but still goes to the enrichment list of a covered work.

A mention keeps its route once set, so routing twice adds neither a second candidate nor a second enrichment entry.
The queue is keyed by QID alone: a mention of a QID that is already a row of another domain is decided and recorded
under that row's domain, and its evidence goes to that row.
"""
from __future__ import annotations

import sys
from dataclasses import replace

import requests

from sync.wiki import SyncError

from pipeline import domains, stages
from pipeline.domains import common
from pipeline.queue import Row

WAITING = "waiting:"
# a lookup still failing after the clients' own retries (Wikidata, WDQS): that mention stays unrouted, the run goes on
LOOKUP_ERRORS = (requests.RequestException, OSError, SyncError)


def lookup_failed(mention, e) -> None:
    print(f"press: {mention.qid} in {mention.url}: lookup failed ({e}), left unrouted for the next run",
          file=sys.stderr)


def _outlet(store, url: str) -> str | None:
    """The outlet the article's evidence counts for."""
    found = store.article(url)
    return found.evidence_outlet if found else None


def _labels(entity: dict) -> dict:
    return {l: entity["labels"][l]["value"] for l in common.LANGS if entity.get("labels", {}).get(l, {}).get("value")}


def _sitelinks(entity: dict) -> dict:
    return {site: title for site, title in common.entity_sitelinks(entity).items()
            if site.endswith("wiki") and site not in common.NON_WIKIPEDIA}


def _provisional(mention, entity: dict) -> Row:
    return Row(mention.qid, mention.domain, [], _labels(entity), _sitelinks(entity), False)


def _evidence(store, qid: str, routing=None) -> dict[str, float]:
    """{outlet: the highest probability of the item's supporting mentions that count for it now}; supporting: routed as
    candidates (or `routing`, the one being routed) and not judged neutral or critical."""
    press: dict[str, float] = {}
    for m in store.mentions(qid=qid):
        art = store.article(m.url)
        if art is None or not m.supports or (m.route != "candidate" and (routing is None or m.url != routing.url)):
            continue
        press[art.evidence_outlet] = max(m.p, press.get(art.evidence_outlet, 0.0))
    return press


def _sources(old: list[str], press: dict[str, float]) -> list[str]:
    """The row's sources, the press ones as `press` has them; the old ones stay on a row that would have none left (what
    found it; a row with no source is not held to the sitelink floor)."""
    now = [f"press:{o}" for o in press]
    kept = [s for s in old if not s.startswith("press:") or s in now]
    return kept + [s for s in now if s not in kept] or old


def _reconcile(store, queue, qid: str, routing=None) -> None:
    """Make the row's press evidence what its supporting mentions say (see _evidence). Its evidence is corrected whatever
    its status; its outcomes only when it has none: it is triaged again when it gained or lost the exemption from the
    sitelink floor (never a benchmark row), and else its dossier is built again."""
    row = queue.row(qid)
    if row is None:
        return
    stage0 = row.data.get(0, {})
    old, press = stage0.get("press") or {}, _evidence(store, qid, routing)
    sources = _sources(row.sources, press)
    if press == old and sources == row.sources and routing is None:
        return
    redo = None
    if row.status is None and (press != old or routing is not None):
        was, triage = common.press_exempt(row), row.outcomes.get(2)
        now = common.press_exempt(replace(row, data={**row.data, 0: {**stage0, "press": press}}))
        if triage == "drop" and now and (routing is not None or not was):
            redo = 2  # dropped (under the sitelink floor) before this evidence
        elif triage == "pass" and was and not now and not row.calibration:
            redo = 2  # it may have passed by the exemption alone
        elif 4 in row.outcomes:  # the notes are not those the dossier was built with
            redo = 4
    queue.set_press(qid, press, sources, redo)


def reconcile(store, queue) -> None:
    """Every row's press evidence, after articles were read: a copy found changes the outlet an earlier article counts for,
    and no mention need be routed for it."""
    for qid in dict.fromkeys([*queue.press_qids(), *(m.qid for m in store.mentions(route="candidate"))]):
        _reconcile(store, queue, qid)


def _add_candidate(store, queue, mention, outlet: str, entity: dict, domain: str, stage0: dict | None = None) -> None:
    """Add the mention's item to the queue under `domain`, or its source and press probability to the row it has.
    `stage0`: the domain's own stage-0 data for a new row (e.g. a literature series' volumes)."""
    sitelinks, data = _sitelinks(entity), None
    if queue.row(mention.qid) is None:  # what triage reads of a row the domain's own queries did not make
        data = {"count": len(sitelinks), "instance_of": common.qids(entity, "P31")} | (stage0 or {})
    queue.add(mention.qid, domain, f"press:{outlet}", _labels(entity), sitelinks, data=data)
    _reconcile(store, queue, mention.qid, routing=mention)


def owner(queue, qid: str, domain: str) -> str:
    """The domain a mention of `qid` is routed under: the domain of the QID's queue row when it has one, else
    `domain`."""
    row = queue.row(qid) if queue is not None else None
    return row.domain if row is not None else domain


def _route(store, queue, mention, outlet: str, domain: str, entity: dict, repo, wikis, sparql) -> str | None:
    """Route one mention under `domain`, or under the domain of the row its QID already has (the mention's stored
    domain follows it); the route taken, or None (reported) when that domain has no module here."""
    under = owner(queue, mention.qid, domain)
    if under not in domains.DOMAINS:
        print(f"press: {mention.qid} in {mention.url}: already a {under} candidate, a domain not loaded here; "
              "not routed", file=sys.stderr)
        return None
    kind, stage0 = decide(mention, under, entity, repo, wikis, sparql)
    if under != domain:
        print(f"press: {mention.qid} in {mention.url}: already a {under} candidate, routed as {under}",
              file=sys.stderr)
    if kind == "candidate":
        _add_candidate(store, queue, mention, outlet, entity, under, stage0)
    # the domain and the route together, once the candidate is in: an interruption leaves the mention as it was
    store.set_route(mention.url, mention.qid, kind, under if under != domain else None)
    return kind


def decide(mention, domain: str, entity: dict, repo, wikis, sparql=None) -> tuple[str, dict]:
    """("enrichment", "candidate" or, for a neutral or only critical mention of a work not covered, "dropped"; stage-0
    data) for the mention's item under `domain`, with the coverage check
    stage 1 runs: the domain's own `covered` when it has one (literature: a series covered through a volume),
    else stages.covered. A domain module may give `press_stage0(qid, entity, sparql) -> dict`, the stage-0 data
    that check needs for an item none of its queries added (literature: a series' volumes; music and art: a
    whole's parts); it is asked only when `sparql` is given."""
    module = domains.DOMAINS.get(domain)  # None: a domain not loaded here (tests), stages.covered alone
    hook = getattr(module, "press_stage0", None)
    stage0 = hook(mention.qid, entity, sparql) if hook and sparql is not None else {}
    row = _provisional(mention, entity)
    row.domain = domain
    row.data = {0: stage0}
    covered = getattr(module, "covered", None) or stages.covered
    outcome = covered(row, repo, wikis)[0]
    if outcome in ("drop", "translation"):
        return "enrichment", stage0
    return ("candidate" if mention.supports else "dropped"), stage0


def _route_one(store, queue, mention, outlet: str, repo, wikis, wikidata, sparql) -> str | None:
    if mention.domain.startswith(WAITING):
        store.set_route(mention.url, mention.qid, "waiting")
        return "waiting"
    if mention.domain not in domains.DOMAINS:
        print(f"press: {mention.qid} in {mention.url}: unknown domain {mention.domain!r}, not routed",
              file=sys.stderr)
        return None
    return _route(store, queue, mention, outlet, mention.domain, wikidata.entity(mention.qid), repo, wikis, sparql)


def route(store, queue, repo, wikis, wikidata, sparql=None) -> dict[str, int]:
    """Route every unrouted mention; the count of each route taken, and `errors`: the mentions whose lookup still
    failed after the clients' retries (reported, left unrouted for the next run). `sparql` feeds the domains'
    press_stage0."""
    counts: dict[str, int] = {}
    for mention in store.unrouted():
        outlet = _outlet(store, mention.url)
        if outlet is None:
            print(f"press: mention of {mention.qid} in {mention.url}: no stored article, not routed", file=sys.stderr)
            continue
        try:
            kind = _route_one(store, queue, mention, outlet, repo, wikis, wikidata, sparql)
        except LOOKUP_ERRORS as e:
            lookup_failed(mention, e)
            kind = "errors"
        if kind is not None:
            counts[kind] = counts.get(kind, 0) + 1
    reconcile(store, queue)
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
        kind = _route(store, queue, mention, outlet, domain, wikidata.entity(mention.qid), repo, wikis, sparql)
        if kind is not None:
            counts[kind] = counts.get(kind, 0) + 1
    return counts

