"""Minimal MediaWiki Action API client for the sync."""
from __future__ import annotations

import requests

from sync.plan import PageState

USER_AGENT = "seminaverbi-templates-sync/1.0 (https://github.com/JohnRDOrazio/seminaverbi-templates)"


class SyncError(Exception):
    pass


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

    def edit(self, title: str, text: str, summary: str) -> None:
        result = self._dig(self._call("POST", action="edit", title=title, text=text, summary=summary,
                                      bot="1", token=self._token("csrf")), "edit")
        if result.get("result") != "Success":
            raise SyncError(f"{self.lang}: edit of {title} failed: {result}")
