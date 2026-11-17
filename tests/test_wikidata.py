import pytest

from sync.wiki import USER_AGENT, SyncError
from sync.wikidata import WikidataClient


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Wikidata:
    """A fake Wikidata: sitelinks (site, title) -> QID, redirects (site, title) -> target title."""

    def __init__(self, links=None, redirects=None, entities=None, search=None):
        self.links, self.redirects = links or {}, redirects or {}
        self.entities, self.search_hits = entities or {}, search or {}
        self.calls, self.headers = [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        p = params
        if p["action"] == "wbsearchentities":
            return Response({"search": [{"id": q} for q in self.search_hits.get((p["search"], p["language"]), [])]})
        if "ids" in p:
            return Response({"entities": {q: self.entities[q] for q in p["ids"].split("|")}})
        site, titles = p["sites"], p["titles"].split("|")
        out, normalized = {}, {}
        for n, title in enumerate(titles):
            if p.get("normalize") and len(titles) == 1 and (site, title) in self.redirects:
                normalized = {"n": {"from": title, "to": self.redirects[site, title]}}
                title = self.redirects[site, title]
            q = self.links.get((site, title))
            if q:
                out[q] = {"id": q, "sitelinks": {site: {"site": site, "title": title}}}
            else:
                out[str(-n - 1)] = {"site": site, "title": title, "missing": ""}
        body = {"entities": out, "success": 1}
        if normalized:
            body["normalized"] = normalized
        return Response(body)


def test_by_sitelink_batches_of_50():
    titles = [f"T{n}" for n in range(120)]
    wd = Wikidata(links={("enwiki", t): f"Q{n + 1}" for n, t in enumerate(titles)})
    got = WikidataClient(session=wd).by_sitelink("enwiki", titles)
    assert got == {t: f"Q{n + 1}" for n, t in enumerate(titles)}
    assert [len(c[1]["titles"].split("|")) for c in wd.calls] == [50, 50, 20]
    assert all(c[0] == "https://www.wikidata.org/w/api.php" for c in wd.calls)
    assert all(c[1]["redirects"] == "yes" and c[1]["sites"] == "enwiki" for c in wd.calls)


def test_by_sitelink_follows_redirects_and_records_them():
    wd = Wikidata(links={("itwiki", "Matrix (franchise)"): "Q13014087", ("itwiki", "Nek"): "Q45036"},
                  redirects={("itwiki", "Matrix (trilogia)"): "Matrix (franchise)"})
    client = WikidataClient(session=wd)
    got = client.by_sitelink("itwiki", ["Matrix (trilogia)", "Nek", "Nessuno"])
    assert got == {"Matrix (trilogia)": "Q13014087", "Nek": "Q45036"}
    assert client.resolved[("itwiki", "Matrix (trilogia)")] == "Matrix (franchise)"
    retries = [c[1] for c in wd.calls[1:]]
    assert [r["titles"] for r in retries] == ["Matrix (trilogia)", "Nessuno"]
    assert all(r["normalize"] == "1" and r["redirects"] == "yes" for r in retries)


def test_by_sitelink_is_cached():
    wd = Wikidata(links={("enwiki", "A"): "Q1"})
    client = WikidataClient(session=wd)
    assert client.by_sitelink("enwiki", ["A", "B"]) == {"A": "Q1"}
    n = len(wd.calls)
    assert client.by_sitelink("enwiki", ["A", "B"]) == {"A": "Q1"}
    assert len(wd.calls) == n


def test_user_agent():
    wd = Wikidata()
    WikidataClient(session=wd)
    assert wd.headers["User-Agent"] == USER_AGENT


def test_entity_cached_and_batched():
    ents = {f"Q{n}": {"id": f"Q{n}", "claims": {}} for n in range(1, 61)}
    wd = Wikidata(entities=ents)
    client = WikidataClient(session=wd)
    client.prefetch([f"Q{n}" for n in range(1, 61)])
    assert [len(c[1]["ids"].split("|")) for c in wd.calls] == [50, 10]
    assert client.entity("Q7") == {"id": "Q7", "claims": {}}
    assert len(wd.calls) == 2


def test_entity_follows_a_merged_item():
    wd = Wikidata(entities={"Q1": {"id": "Q2", "redirects": {"from": "Q1", "to": "Q2"}, "claims": {}}})
    assert WikidataClient(session=wd).entity("Q1")["id"] == "Q2"


def test_search():
    wd = Wikidata(search={("Holy Mother", "en"): ["Q1", "Q2"]})
    assert WikidataClient(session=wd).search("Holy Mother", "en") == ["Q1", "Q2"]
    p = wd.calls[0][1]
    assert p["type"] == "item" and p["language"] == "en"


def test_error_is_sync_error():
    class Broken(Wikidata):
        def get(self, url, params=None, timeout=None):
            return Response({"error": {"code": "x", "info": "y"}})
    with pytest.raises(SyncError, match="wikidata"):
        WikidataClient(session=Broken()).entity("Q1")
