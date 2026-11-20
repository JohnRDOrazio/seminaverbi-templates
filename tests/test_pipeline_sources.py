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
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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


def test_select_retries_a_read_timeout_then_succeeds():
    import requests
    s = FakeSession(requests.exceptions.ReadTimeout("slow"), bindings({"n": {"value": "1"}}))
    assert Sparql(s).select("q") == [{"n": "1"}]
    assert len(s.calls) == 2


def test_select_gives_up_after_three_read_timeouts():
    import requests
    s = FakeSession(*[requests.exceptions.ReadTimeout("slow") for _ in range(4)])
    with pytest.raises(requests.exceptions.ReadTimeout):
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


def test_links_follow_continuation_and_redirects():
    first = Resp({"continue": {"plcontinue": "x|1", "continue": "||"},
                  "query": {"pages": [{"title": "T", "links": [{"ns": 0, "title": "A"}, {"ns": 0, "title": "B"}]}]}})
    second = Resp({"query": {"pages": [{"title": "T", "links": [{"ns": 0, "title": "C"}]}]}})
    session = FakeSession(first, second)
    assert Wikipedia("en", session=session).links("T") == ["A", "B", "C"]
    params = session.calls[0][1]["params"]
    assert params["prop"] == "links" and params["plnamespace"] == 0 and params["redirects"] == 1
    assert session.calls[1][1]["params"]["plcontinue"] == "x|1"


SEASON_WIKITEXT = """Intro of the season.
== Cast ==
* Anna Torv
== Episodes ==
{{Episode table |episodes=
{{Episode list
 |EpisodeNumber=1 |Title=Pilot
 |ShortSummary=A flight lands, its '''crew''' dead. Olivia asks [[Walter Bishop|Walter]] for help.
}}
{{Episode list/sublist|Fringe season 1
 |EpisodeNumber=2 |Title=The Same Old Story
 |ShortSummary=A woman gives birth to a child that ages rapidly.
}}
}}
=== Notes ===
x
== La redenzione ==
Walter asks forgiveness.
== References ==
{{reflist}}
"""


def test_season_reads_episode_summaries_and_story_sections_in_one_request():
    s = FakeSession(Resp({"parse": {"wikitext": SEASON_WIKITEXT}}))
    out = Wikipedia("en", s).season("Fringe season 1")
    assert out == [("", "Intro of the season."), ("Cast", "Anna Torv"),
                   ("Episode: Pilot", "A flight lands, its crew dead. Olivia asks Walter for help."),
                   ("Episode: The Same Old Story", "A woman gives birth to a child that ages rapidly."),
                   ("Notes", "x"), ("La redenzione", "Walter asks forgiveness.")]
    assert len(s.calls) == 1 and s.calls[0][1]["params"]["prop"] == "wikitext"


def test_season_of_a_missing_page_is_empty():
    s = FakeSession(Resp({"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}))
    assert Wikipedia("en", s).season("Nope") == []


@pytest.mark.parametrize("name", ["Episode_list", "Episode_list/sublist|S1", "episode  list"])
def test_season_accepts_underscores_in_the_episode_template_name(name):
    wikitext = f"== Episodes ==\n{{{{{name}\n |Title=Pilot\n |ShortSummary=Walter asks forgiveness.\n}}}}\n"
    out = Wikipedia("en", FakeSession(Resp({"parse": {"wikitext": wikitext}}))).season("S")
    assert out == [("Episode: Pilot", "Walter asks forgiveness.")]


def test_section_links_reads_one_section():
    sections = Resp({"parse": {"sections": [{"index": "1", "line": "Storia"},
                                            {"index": "2", "line": "<b>I personaggi di Illustrissimi</b>"},
                                            {"index": "3", "line": "I personaggi di Illustrissimi"}]}})
    links = Resp({"parse": {"links": [{"ns": 0, "title": "Charles Dickens"}, {"ns": 0, "title": "Pinocchio (romanzo)"},
                                      {"ns": 14, "title": "Categoria:X"}, {"ns": 0, "title": "Charles Dickens"}]}})
    resolved = Resp({"query": {"redirects": [{"from": "Pinocchio (romanzo)", "to": "Le avventure di Pinocchio"}]}})
    session = FakeSession(sections, links, resolved)
    out = Wikipedia("it", session=session).section_links("Illustrissimi", "I personaggi di Illustrissimi")
    assert out == ["Charles Dickens", "Le avventure di Pinocchio"]
    assert session.calls[1][1]["params"]["section"] == "2" and session.calls[1][1]["params"]["prop"] == "links"
    assert session.calls[2][1]["params"]["redirects"] == 1


def test_section_links_without_the_section_is_empty():
    session = FakeSession(Resp({"parse": {"sections": [{"index": "1", "line": "Storia"}]}}))
    assert Wikipedia("it", session=session).section_links("Illustrissimi", "Nope") == []
    assert len(session.calls) == 1
