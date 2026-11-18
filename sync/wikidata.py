"""Read-only Wikidata client: requests carry only titles and QIDs, with the repository's User-Agent."""
from __future__ import annotations

import requests

from sync.wiki import USER_AGENT, WikiClient

API = "https://www.wikidata.org/w/api.php"
BATCH = 50
LANGS = "en|it|es|fr|de|pt"


class _OwnHeaders:
    """A view of a session that sends this client's own headers per request and never writes to the session
    it wraps (WikiClient sets the User-Agent on the session it is given)."""

    def __init__(self, session):
        self._session = session
        self.headers = {"User-Agent": USER_AGENT}

    def get(self, url, **kwargs):
        return self._session.get(url, headers={**self.headers, **kwargs.pop("headers", {})}, **kwargs)

    def post(self, url, **kwargs):
        return self._session.post(url, headers={**self.headers, **kwargs.pop("headers", {})}, **kwargs)


class WikidataClient:
    def __init__(self, session: requests.Session | None = None):
        # WikiClient's request loop (HTTP 429 back-off, error -> SyncError), pointed at Wikidata
        self._wiki = WikiClient("wikidata", session=_OwnHeaders(session or requests.Session()))
        self._wiki.api = API
        self._links: dict[tuple[str, str], str | None] = {}
        self._entities: dict[str, dict] = {}
        self._search: dict[tuple[str, str], list[str]] = {}
        self.resolved: dict[tuple[str, str], str] = {}  # (site, title) -> the title a redirect led to

    def _get(self, **params) -> dict:
        return self._wiki._call("GET", **params)

    def by_sitelink(self, site: str, titles: list[str]) -> dict[str, str]:
        """title -> QID of the Wikidata item linked to `title` on `site` (e.g. enwiki).

        Titles go 50 per request; those not found are asked again one at a time with normalize=1, which
        makes Wikidata follow the Wikipedia redirect (it only does so for a single title)."""
        todo = list(dict.fromkeys(t for t in titles if (site, t) not in self._links))
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            entities = self._wiki._dig(self._get(action="wbgetentities", sites=site, titles="|".join(chunk),
                                                 props="sitelinks", sitefilter=site, redirects="yes"), "entities")
            found = {e["sitelinks"][site]["title"]: q for q, e in entities.items()
                     if q.startswith("Q") and "missing" not in e and site in e.get("sitelinks", {})}
            for title in chunk:
                self._links[site, title] = found.get(title)
        for title in todo:
            if self._links[site, title] is None:
                data = self._get(action="wbgetentities", sites=site, titles=title, props="sitelinks",
                                 sitefilter=site, redirects="yes", normalize="1")
                entities = self._wiki._dig(data, "entities")
                q = next((q for q, e in entities.items() if q.startswith("Q") and "missing" not in e), None)
                if q:
                    self._links[site, title] = q
                    target = entities[q].get("sitelinks", {}).get(site, {}).get("title")
                    if target and target != title:
                        self.resolved[site, title] = target
        return {t: self._links[site, t] for t in titles if self._links.get((site, t))}

    def prefetch(self, qids: list[str]) -> None:
        """Loads the entities (claims, labels, descriptions, sitelinks) not cached yet, 50 per request."""
        todo = list(dict.fromkeys(q for q in qids if q not in self._entities))
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            entities = self._wiki._dig(self._get(action="wbgetentities", ids="|".join(chunk),
                                                 props="claims|labels|descriptions|sitelinks",
                                                 languages=LANGS), "entities")
            for q in chunk:
                entity = entities.get(q) or {}
                self._entities[q] = entity
                if entity.get("id") and entity["id"] != q:  # a merged item answers with its target
                    self._entities.setdefault(entity["id"], entity)

    def entity(self, qid: str) -> dict:
        if qid not in self._entities:
            self.prefetch([qid])
        return self._entities[qid]

    def search(self, text: str, lang: str) -> list[str]:
        """QIDs whose label or alias in `lang` matches `text` (Wikidata's own ranking, at most 10)."""
        key = (text, lang)
        if key not in self._search:
            data = self._get(action="wbsearchentities", search=text, language=lang, uselang=lang, type="item",
                             limit="10")
            self._search[key] = [hit["id"] for hit in data.get("search", [])]
        return list(self._search[key])
