"""Read-only sources for the article pipeline: Wikidata SPARQL and Wikipedia text."""
from __future__ import annotations

import re
import time
from urllib.parse import quote

import mwparserfromhell
import requests

from sync.wiki import USER_AGENT, SyncError, WikiClient, _retry_wait
from sync.wikidata import _OwnHeaders

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
SPARQL_RETRIES = 3
_ENTITY = "http://www.wikidata.org/entity/"
_HEADING = re.compile(r"^(=+)[^=].*\1\s*$")


class Sparql:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def select(self, query: str) -> list[dict[str, str]]:
        """Run a SELECT query; bindings become {var: value}, entity URIs become their QID."""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
        retries = 0
        while True:
            r = self.session.get(SPARQL_ENDPOINT, params={"query": query, "format": "json"},
                                 headers=headers, timeout=60)
            if r.status_code not in (429, 503):
                break
            if retries >= SPARQL_RETRIES:
                raise SyncError(f"sparql: HTTP {r.status_code} after {SPARQL_RETRIES} retries")
            time.sleep(_retry_wait(r.headers.get("Retry-After"), retries))
            retries += 1
        r.raise_for_status()
        rows = r.json()["results"]["bindings"]
        return [{var: _plain(cell["value"]) for var, cell in row.items()} for row in rows]


def _plain(value: str) -> str:
    return value[len(_ENTITY):] if value.startswith(_ENTITY) else value


class Wikipedia:
    def __init__(self, lang: str, session: requests.Session | None = None):
        self.lang = lang
        # WikiClient's request loop (HTTP 429 back-off, error -> SyncError), pointed at Wikipedia
        self._wiki = WikiClient(lang, session=_OwnHeaders(session or requests.Session()))
        self._wiki.api = f"https://{lang}.wikipedia.org/w/api.php"

    def url(self, title: str) -> str:
        return f"https://{self.lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='/:()')}"

    def lead(self, title: str) -> str:
        """Plain-text introduction of the article ('' when there is none); redirects are followed."""
        data = self._wiki._call("GET", action="query", prop="extracts", exintro=1, explaintext=1,
                                redirects=1, titles=title)
        pages = data.get("query", {}).get("pages", [])
        return (pages[0].get("extract") or "").strip() if pages else ""

    def sections(self, title: str) -> list[tuple[str, str]]:
        """(heading, plain text) for each numbered section; subsection text is left to its own entry."""
        listing = self._wiki._call("GET", action="parse", page=title, prop="sections", redirects=1)
        out = []
        for sec in listing.get("parse", {}).get("sections", []):
            if not str(sec.get("index", "")).isdigit():  # transcluded sections ("T-1") have no own wikitext
                continue
            data = self._wiki._call("GET", action="parse", page=title, prop="wikitext",
                                    section=sec["index"], redirects=1)
            out.append((sec["line"], _plain_text(data["parse"]["wikitext"])))
        return out


def _plain_text(wikitext: str) -> str:
    lines = wikitext.splitlines()
    if lines and _HEADING.match(lines[0].strip()):
        lines = lines[1:]
    for i, line in enumerate(lines):  # cut at a subsection heading
        if _HEADING.match(line.strip()):
            lines = lines[:i]
            break
    return mwparserfromhell.parse("\n".join(lines)).strip_code().strip()
