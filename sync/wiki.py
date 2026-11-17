"""Minimal MediaWiki Action API client for the sync."""
from __future__ import annotations

import json
from typing import NamedTuple

import requests

from sync.plan import PageState

USER_AGENT = "seminaverbi-templates-sync/1.0 (https://github.com/JohnRDOrazio/seminaverbi-templates)"


class SyncError(Exception):
    pass


class PageInfo(NamedTuple):
    exists: bool
    redirect: bool
    categories: list  # non-hidden category names, without the namespace prefix
    item: str | None  # the Wikibase item the page is connected to


def _bare(category_title: str) -> str:
    """'Categoria:Musica' -> 'Musica' (the namespace name differs per wiki language)."""
    return category_title.split(":", 1)[1] if ":" in category_title else category_title


class WikiClient:
    def __init__(self, lang: str, session: requests.Session | None = None):
        self.lang = lang
        self.api = f"https://{lang}.seminaverbi.bibleget.io/w/api.php"
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _call(self, method: str, **params) -> dict:
        params.update(format="json", formatversion="2")
        if method == "GET":
            r = self.session.get(self.api, params=params, timeout=60)
        else:
            r = self.session.post(self.api, data=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise SyncError(f"{self.lang}: {data['error'].get('code')}: {data['error'].get('info')}")
        return data

    def _dig(self, data, *keys):
        """data[k1][k2]..., raising SyncError (not KeyError) when the API answered something unexpected."""
        for key in keys:
            try:
                data = data[key]
            except (KeyError, IndexError, TypeError):
                raise SyncError(f"{self.lang}: unexpected API response: missing {key!r}") from None
        return data

    def _token(self, kind: str) -> str:
        return self._dig(self._call("GET", action="query", meta="tokens", type=kind),
                         "query", "tokens", f"{kind}token")

    def login(self, user: str, password: str) -> None:
        result = self._dig(self._call("POST", action="login", lgname=user, lgpassword=password,
                                      lgtoken=self._token("login")), "login")
        if result.get("result") != "Success":
            raise SyncError(f"{self.lang}: login failed: {result.get('reason', result.get('result'))}")

    def fetch(self, titles: list[str]) -> dict[str, PageState | None]:
        out: dict[str, PageState | None] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            q = self._dig(self._call("GET", action="query", prop="revisions", rvprop="content|user",
                                     rvslots="main", titles="|".join(batch)), "query")
            back = {self._dig(n, "to"): self._dig(n, "from") for n in q.get("normalized", [])}
            for page in q.get("pages", []):
                title = back.get(self._dig(page, "title"), page["title"])
                if page.get("missing") or "revisions" not in page:
                    out[title] = None
                else:
                    rev = self._dig(page, "revisions", 0)
                    out[title] = PageState(self._dig(rev, "slots", "main", "content"), rev.get("user", ""))
            for t in batch:
                out.setdefault(t, None)
        return out

    def edit(self, title: str, text: str, summary: str, tags: str | None = None,
             createonly: bool = False) -> None:
        params = dict(action="edit", title=title, text=text, summary=summary, bot="1")
        if tags:
            params["tags"] = tags
        if createonly:
            params["createonly"] = "1"
        result = self._dig(self._call("POST", token=self._token("csrf"), **params), "edit")
        if result.get("result") != "Success":
            raise SyncError(f"{self.lang}: edit of {title} failed: {result}")

    def page_info(self, titles: list[str]) -> dict[str, PageInfo | None]:
        """Existence, redirect flag, visible categories and Wikibase item of each title (None if missing)."""
        out: dict[str, PageInfo | None] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            q = self._dig(self._call("GET", action="query", prop="info|categories|pageprops",
                                     ppprop="wikibase_item", clshow="!hidden", cllimit="max",
                                     titles="|".join(batch)), "query")
            back = {self._dig(n, "to"): self._dig(n, "from") for n in q.get("normalized", [])}
            for page in q.get("pages", []):
                title = back.get(self._dig(page, "title"), page["title"])
                if page.get("missing") or page.get("invalid"):
                    out[title] = None
                    continue
                cats = [_bare(self._dig(c, "title")) for c in page.get("categories", [])]
                out[title] = PageInfo(True, bool(page.get("redirect")), cats,
                                      page.get("pageprops", {}).get("wikibase_item"))
            for t in batch:
                out.setdefault(t, None)
        return out

    def article_categories(self) -> set[str]:
        """Visible categories used by main-namespace articles (redirects excluded)."""
        params = dict(action="query", generator="allpages", gapnamespace="0", gapfilterredir="nonredirects",
                      gaplimit="max", prop="categories", clshow="!hidden", cllimit="max")
        found: set[str] = set()
        while True:
            data = self._call("GET", **params)
            for page in data.get("query", {}).get("pages", []):
                found.update(_bare(self._dig(c, "title")) for c in page.get("categories", []))
            if "continue" not in data:
                return found
            params.update(data["continue"])

    def item_for(self, site: str, title: str) -> str | None:
        """The Wikibase item a page on `site` is linked to, or None."""
        entities = self._dig(self._call("GET", action="wbgetentities", sites=site, titles=title,
                                        normalize="1", props="info"), "entities")
        for key, entity in entities.items():
            if "missing" not in entity and key.startswith("Q"):
                return key
        return None

    def set_sitelink(self, item: str, site: str, title: str, tags: str | None = None) -> None:
        params = dict(action="wbsetsitelink", id=item, linksite=site, linktitle=title, bot="1")
        if tags:
            params["tags"] = tags
        data = self._call("POST", token=self._token("csrf"), **params)
        if not data.get("success"):
            raise SyncError(f"{self.lang}: linking {site}:{title} to {item} failed: {data}")

    def new_item(self, labels: dict[str, str], descriptions: dict[str, str], sitelinks: dict[str, str],
                 tags: str | None = None) -> str:
        entity = {
            "labels": {lang: {"language": lang, "value": v} for lang, v in labels.items()},
            "descriptions": {lang: {"language": lang, "value": v} for lang, v in descriptions.items()},
            "sitelinks": {site: {"site": site, "title": t} for site, t in sitelinks.items()},
        }
        params = dict(action="wbeditentity", new="item", data=json.dumps(entity, ensure_ascii=False), bot="1")
        if tags:
            params["tags"] = tags
        return self._dig(self._call("POST", token=self._token("csrf"), **params), "entity", "id")
