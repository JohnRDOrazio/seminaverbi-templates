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
    def __init__(self, lang: str, session: requests.Session | None = None, project: str = "wikipedia"):
        self.lang, self.project = lang, project
        # WikiClient's request loop (HTTP 429 back-off, error -> SyncError), pointed at Wikipedia
        self._wiki = WikiClient(lang, session=_OwnHeaders(session or requests.Session()))
        self._wiki.api = f"https://{lang}.{project}.org/w/api.php"

    def url(self, title: str) -> str:
        return f"https://{self.lang}.{self.project}.org/wiki/{quote(title.replace(' ', '_'), safe='/:()')}"

    def lead(self, title: str) -> str:
        """Plain-text introduction of the article ('' when there is none); redirects are followed."""
        data = self._wiki._call("GET", action="query", prop="extracts", exintro=1, explaintext=1,
                                redirects=1, titles=title)
        pages = data.get("query", {}).get("pages", [])
        return (pages[0].get("extract") or "").strip() if pages else ""

    def links(self, title: str) -> list[str]:
        """Titles of the articles (main namespace) the page links to, in page order; redirects are followed."""
        params = dict(action="query", prop="links", plnamespace=0, pllimit="max", redirects=1, titles=title)
        out: list[str] = []
        while True:
            data = self._wiki._call("GET", **params)
            for page in data.get("query", {}).get("pages", []):
                out.extend(link["title"] for link in page.get("links", []))
            if "continue" not in data:
                return list(dict.fromkeys(out))
            params.update(data["continue"])

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

    def season(self, title: str) -> list[tuple[str, str]]:
        """A season or episode-list page in one request: [(heading, plain text)], the lead under "", the
        summaries of its {{Episode list}} templates as ("Episode: <title>", summary); [] for a missing page."""
        try:
            data = self._wiki._call("GET", action="parse", page=title, prop="wikitext", redirects=1)
        except SyncError as exc:
            if "missingtitle" in str(exc) or "nosuchpage" in str(exc):
                return []
            raise
        return _season_sections(data.get("parse", {}).get("wikitext", ""))


def _plain_text(wikitext: str) -> str:
    lines = wikitext.splitlines()
    if lines and _HEADING.match(lines[0].strip()):
        lines = lines[1:]
    for i, line in enumerate(lines):  # cut at a subsection heading
        if _HEADING.match(line.strip()):
            lines = lines[:i]
            break
    return mwparserfromhell.parse("\n".join(lines)).strip_code().strip()


_EPISODE = re.compile(r"^episode[ _]+list", re.I)  # MediaWiki reads "_" in a name as a space
_SUMMARY_PARAMS = ("ShortSummary", "Summary")


def _episodes(code) -> list[tuple[str, str]]:
    """("Episode: <title>", plain summary) for each {{Episode list…}} template, removed from `code`."""
    out = []
    for tpl in code.filter_templates(recursive=True):
        if not _EPISODE.match(str(tpl.name).strip()):
            continue
        title = next((str(tpl.get(p).value) for p in ("Title", "RTitle") if tpl.has(p)), "")
        summary = next((str(tpl.get(p).value) for p in _SUMMARY_PARAMS if tpl.has(p)), "")
        title = mwparserfromhell.parse(title).strip_code().strip()
        summary = mwparserfromhell.parse(summary).strip_code().strip()
        if summary:
            out.append((f"Episode: {title}".rstrip(": "), summary))
    return out


def _season_sections(wikitext: str) -> list[tuple[str, str]]:
    """The lead (heading ""), each section's own text and the episode summaries, in page order; empty ones skipped."""
    code = mwparserfromhell.parse(wikitext)
    out = []
    for section in code.get_sections(include_lead=True, flat=True):
        headings = section.filter_headings(recursive=False)
        heading = headings[0].title.strip_code().strip() if headings else ""
        episodes = _episodes(section)
        for tpl in section.filter_templates(recursive=False):
            if episodes and any(_EPISODE.match(str(t.name).strip())
                                for t in mwparserfromhell.parse(str(tpl)).filter_templates(recursive=True)):
                section.remove(tpl)
        for h in headings:
            section.remove(h)
        text = section.strip_code().strip()
        if text:
            out.append((heading, text))
        out.extend(episodes)
    return out
