"""Linking an article's mentions to Wikidata items, and routing each item to a domain by its class.

A span (italic or quoted title, Wikipedia link) becomes a list of Wikidata hits: a link through its sitelink, any
other span through Wikidata search in the page's language, then in English. Hits whose class routes to no domain
are discarded; one routed hit is taken, several go to Jev's `pick`, taken only above the configured cut. Jev then
judges, on the paragraphs around the mention (at most 150 words), whether the article discusses the item
substantively; a kept mention is stored with up to three passages of at most 25 words.

Class membership comes only from the domains' `owns()` and `common.owns()`, backed by the cached subclass closure:
this module runs no SPARQL of its own.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pipeline import domains
from pipeline.domains import common
from pipeline.press.extract import Page, Span, passage, window
from pipeline.press.store import Mention

MAX_PASSAGES = 3
PERSON = "Q5"


def class_route(instance_of: list[str], sparql, cfg: dict, cache: Path | None = None) -> str | None:
    """The domain of an item of these P31 classes: the first module of DOMAINS (in order) that owns it, else
    "waiting:<name>" for the first `waiting_classes` entry of a domain with no module yet, else None."""
    if not instance_of:
        return None
    for name, module in domains.DOMAINS.items():
        if module.owns(instance_of, sparql, cache):
            return name
    for name, classes in cfg.get("waiting_classes", {}).items():
        if name not in domains.DOMAINS and common.owns(instance_of, classes, sparql, cache):
            return f"waiting:{name}"
    return None


def _text(entity: dict, key: str, lang: str) -> str:
    values = entity.get(key, {})
    for l in (lang, "en", *sorted(values)):
        value = values.get(l, {}).get("value")
        if value:
            return value
    return ""


def _option(qid: str, entity: dict, lang: str) -> str:
    label, description = _text(entity, "labels", lang) or qid, _text(entity, "descriptions", lang)
    return f"{label} — {description}" if description else label


class _Linker:
    def __init__(self, page: Page, outlet: str, url: str, judge, wikidata, sparql, cfg: dict, cache: Path | None,
                 routes: dict | None = None):
        self.page, self.outlet, self.url = page, outlet, url
        self.judge, self.wikidata, self.sparql, self.cfg, self.cache = judge, wikidata, sparql, cfg, cache
        self._items: dict[str, tuple[str | None, list[str]]] = {}
        self._classes: dict[frozenset, str | None] = {} if routes is None else routes

    def route_of(self, qid: str) -> tuple[str | None, list[str]]:
        """(domain or None, P31) of the item, asked once per article; class_route asked once per set of classes
        in the run (the `routes` memo)."""
        if qid not in self._items:
            instance_of = common.qids(self.wikidata.entity(qid), "P31")
            key = frozenset(instance_of)
            if key not in self._classes:
                self._classes[key] = class_route(instance_of, self.sparql, self.cfg, self.cache)
            self._items[qid] = (self._classes[key], instance_of)
        return self._items[qid]

    def _routed(self, qids: list[str]) -> list[str]:
        return [q for q in dict.fromkeys(qids) if self.route_of(q)[0] is not None]

    def hits(self, span: Span) -> list[str]:
        """The routed Wikidata hits of the span."""
        if span.kind == "wikilink" and span.wiki:
            lang, title = span.wiki
            qid = self.wikidata.by_sitelink(f"{lang}wiki", [title]).get(title)
            return self._routed([qid] if qid else [])
        langs = list(dict.fromkeys((self.page.lang, "en")))
        for lang in langs:
            routed = self._routed(self.wikidata.search(span.text, lang))
            if routed:
                return routed
        return []

    def resolve(self, span: Span, context: str) -> str | None:
        """The one item the span names, or None (no routed hit, or an ambiguity Jev does not settle)."""
        hits = self.hits(span)
        if len(hits) == 1:
            return hits[0]
        if not hits:
            return None
        options = {q: _option(q, self.wikidata.entity(q), self.page.lang) for q in sorted(hits)}
        qid, p, confidence = self.judge.pick(self.url, context, span.text, options)
        if qid in options and p >= self.cfg["pick_p"] and confidence >= self.cfg["pick_confidence"]:
            return qid
        print(f"press: {self.outlet} {self.url}: {span.text!r} ambiguous ({qid}, p={p:.2f}, "
              f"confidence={confidence:.2f}), dropped", file=sys.stderr)
        return None

    def passages(self, span: Span) -> list[str]:
        """Up to three passages from the paragraphs naming the label, the mention's own paragraph first."""
        needle = span.text.casefold()
        indexes = [span.paragraph] + [i for i in range(len(self.page.paragraphs)) if i != span.paragraph]
        out = []
        for i in indexes:
            if 0 <= i < len(self.page.paragraphs) and needle in self.page.paragraphs[i].casefold():
                out.append(passage(self.page.paragraphs[i], span.text))
                if len(out) == MAX_PASSAGES:
                    break
        return out


def link_article(page: Page, outlet: str, store, judge, wikidata, sparql, cfg: dict, *, url: str,
                 cache: Path | None = None, routes: dict | None = None) -> list[Mention]:
    """Link the page's spans to Wikidata items and store the mentions the article discusses substantively
    (route None: routing is a later stage). `url` is the article's stored key in press_articles (never the page's
    own canonical link): every mention and every Jev question of the article uses it. `cache` is the class
    closure cache (default: common.CLOSURE_CACHE); `routes` is the run's memo of class_route by set of P31
    classes, shared across the run's articles (default: one for this article)."""
    linker = _Linker(page, outlet, url, judge, wikidata, sparql, cfg, cache, routes)
    seen_spans: set[tuple[str, tuple[str, str] | None]] = set()
    judged: set[str] = set()
    kept: list[Mention] = []
    for span in page.spans:
        key = (span.text.casefold(), span.wiki)
        if key in seen_spans or not span.text.strip():
            continue
        seen_spans.add(key)
        context = window(page.paragraphs, span.paragraph, needle=span.text)
        if not context:  # a span outside the paragraphs: nothing for Jev to read
            continue
        qid = linker.resolve(span, context)
        if qid is None or qid in judged:
            continue
        judged.add(qid)
        p = judge.substantive(url, span.text, context)
        if p < cfg["mention_keep"]:
            continue
        domain, instance_of = linker.route_of(qid)
        mention = Mention(url, qid, span.text, "person" if PERSON in instance_of else "work", domain, p,
                          linker.passages(span))
        store.add_mention(mention)
        kept.append(mention)
    return kept
