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
        self.sent_headers = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.sent_headers.append(dict(headers or {}))
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


def test_user_agent_is_sent_without_touching_the_callers_session():
    wd = Wikidata(links={("enwiki", "A"): "Q1"})
    wd.headers["User-Agent"] = "callers-agent"
    WikidataClient(session=wd).by_sitelink("enwiki", ["A"])
    assert wd.headers == {"User-Agent": "callers-agent"}  # the session the caller gave is untouched
    assert wd.sent_headers and all(h["User-Agent"] == USER_AGENT for h in wd.sent_headers)


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
        def get(self, url, params=None, timeout=None, headers=None):
            return Response({"error": {"code": "x", "info": "y"}})
    with pytest.raises(SyncError, match="wikidata"):
        WikidataClient(session=Broken()).entity("Q1")


class Flaky(Wikidata):
    """A Wikidata that first fails with each of `failures` (an exception raised, or an API error code answered)."""

    def __init__(self, failures, **kwargs):
        super().__init__(**kwargs)
        self.failures = list(failures)

    def get(self, url, params=None, timeout=None, headers=None):
        if self.failures:
            failure = self.failures.pop(0)
            self.calls.append((url, dict(params)))
            if isinstance(failure, Exception):
                raise failure
            return Response({"error": {"code": failure, "info": "Search is currently too busy."}})
        return super().get(url, params=params, timeout=timeout, headers=headers)


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


def test_search_retries_when_search_is_too_busy(waits):
    wd = Flaky(["cirrussearch-too-busy-error", "cirrussearch-too-busy-error"], search={("Ordet", "it"): ["Q1"]})
    assert WikidataClient(session=wd).search("Ordet", "it") == ["Q1"]
    assert len(wd.calls) == 3
    assert waits == [1.0, 2.0]  # sync.wiki's bounded backoff


def test_entity_retries_a_dropped_connection(waits):
    import requests
    wd = Flaky([requests.exceptions.ConnectionError("Remote end closed connection without response")],
               entities={"Q1": {"id": "Q1", "claims": {}}})
    assert WikidataClient(session=wd).entity("Q1")["id"] == "Q1"
    assert len(wd.calls) == 2


def test_a_busy_search_gives_up_after_the_retries(waits):
    from sync.wiki import RETRIES
    wd = Flaky(["cirrussearch-too-busy-error"] * (RETRIES + 1))
    with pytest.raises(SyncError, match="cirrussearch-too-busy-error"):
        WikidataClient(session=wd).search("Ordet", "it")
    assert len(wd.calls) == RETRIES + 1


def test_other_api_errors_are_not_retried(waits):
    wd = Flaky(["no-such-entity"])
    with pytest.raises(SyncError, match="no-such-entity"):
        WikidataClient(session=wd).entity("Q1")
    assert len(wd.calls) == 1 and waits == []
