import pytest

from pipeline.sources import Sparql, Wikipedia
from sync.wiki import SyncError


class Resp:
    def __init__(self, data=None, status=200, headers=None):
        self._data, self.status_code, self.headers = data or {}, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


def bindings(*rows):
    return Resp({"results": {"bindings": list(rows)}})


def test_select_flattens_bindings():
    row = {"item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q42"}, "n": {"value": "7"}}
    s = FakeSession(bindings(row))
    assert Sparql(s).select("SELECT * WHERE {}") == [{"item": "Q42", "n": "7"}]
    url, kw = s.calls[0]
    assert kw["params"]["format"] == "json" and kw["params"]["query"] == "SELECT * WHERE {}"
    assert kw["headers"]["Accept"] == "application/sparql-results+json"
    assert kw["timeout"] == 60


def test_select_retries_on_429_then_succeeds():
    s = FakeSession(Resp(status=429, headers={"Retry-After": "0"}),
                    bindings({"n": {"value": "1"}}))
    assert Sparql(s).select("q") == [{"n": "1"}]
    assert len(s.calls) == 2


def test_select_retries_503_and_gives_up_after_three():
    s = FakeSession(*[Resp(status=503) for _ in range(4)])
    with pytest.raises(SyncError):
        Sparql(s).select("q")
    assert len(s.calls) == 4


def test_lead_follows_redirects():
    s = FakeSession(Resp({"query": {"pages": [{"title": "T", "extract": "Intro."}]}}))
    assert Wikipedia("en", s).lead("T") == "Intro."
    url, kw = s.calls[0]
    assert url == "https://en.wikipedia.org/w/api.php"
    p = kw["params"]
    assert p["redirects"] == 1 and p["prop"] == "extracts" and p["exintro"] == 1 and p["explaintext"] == 1


def test_lead_missing_page_is_empty():
    s = FakeSession(Resp({"query": {"pages": [{"title": "T", "missing": True}]}}))
    assert Wikipedia("en", s).lead("T") == ""


def test_sections_strips_wikitext():
    s = FakeSession(
        Resp({"parse": {"title": "T", "sections": [
            {"line": "Plot", "index": "1", "toclevel": 1}, {"line": "Cast", "index": "2", "toclevel": 1}]}}),
        Resp({"parse": {"wikitext": "== Plot ==\nHe '''met''' [[Paul|Saul]]."}}),
        Resp({"parse": {"wikitext": "== Cast ==\n{{cite web|x=y}}Anna"}}),
    )
    out = Wikipedia("en", s).sections("T")
    assert out == [("Plot", "He met Saul."), ("Cast", "Anna")]
    assert s.calls[1][1]["params"]["section"] == "1"
    assert s.calls[1][1]["params"]["prop"] == "wikitext"


def test_url():
    assert Wikipedia("it").url("Il Padrino") == "https://it.wikipedia.org/wiki/Il_Padrino"


def test_user_agent_has_no_email():
    s = FakeSession(bindings(), Resp({"query": {"pages": []}}))
    Sparql(s).select("q")
    Wikipedia("en", s).lead("T")
    for _, kw in s.calls:
        assert "User-Agent" in kw["headers"] and "@" not in kw["headers"]["User-Agent"]
    assert "@" not in str(s.headers)
