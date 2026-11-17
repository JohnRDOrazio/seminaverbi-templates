import pytest

from sync.wiki import PageInfo, SyncError, WikiClient


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    """Answers every request with the same JSON body."""

    def __init__(self, data):
        self.data, self.headers = data, {}

    def get(self, url, params=None, timeout=None):
        return Response(self.data)

    def post(self, url, data=None, timeout=None):
        return Response(self.data)


def test_unexpected_response_is_sync_error():
    # a KeyError would stop the whole run instead of failing just this wiki
    client = WikiClient("en", session=Session({"batchcomplete": True}))
    with pytest.raises(SyncError, match=r"en: unexpected API response: missing 'query'"):
        client.login("u", "p")
    with pytest.raises(SyncError, match="missing 'query'"):
        client.fetch(["Module:SV/Util"])


@pytest.mark.parametrize("query, key", [
    ({"normalized": [{"from": "Module:SV/Util"}], "pages": []}, "to"),
    ({"pages": [{"pageid": 1}]}, "title"),
    ({"pages": [{"title": "Module:SV/Util", "revisions": []}]}, 0),
])
def test_malformed_pages_are_sync_errors(query, key):
    client = WikiClient("it", session=Session({"query": query}))
    with pytest.raises(SyncError, match=f"it: unexpected API response: missing {key!r}"):
        client.fetch(["Module:SV/Util"])


class Recorder:
    """Records requests and answers each from a queue of JSON bodies."""

    def __init__(self, *bodies):
        self.bodies, self.calls, self.headers = list(bodies), [], {}

    def _next(self, method, params):
        self.calls.append((method, dict(params)))
        return Response(self.bodies.pop(0))

    def get(self, url, params=None, timeout=None):
        return self._next("GET", params)

    def post(self, url, data=None, timeout=None):
        return self._next("POST", data)


CSRF = {"query": {"tokens": {"csrftoken": "t+\\"}}}


def test_edit_sends_tags_and_createonly():
    s = Recorder(CSRF, {"edit": {"result": "Success"}})
    WikiClient("fr", session=s).edit("Category:X", "text", "sum", tags="seminaverbi-mcp", createonly=True)
    params = s.calls[-1][1]
    assert params["tags"] == "seminaverbi-mcp" and params["createonly"] == "1"


def test_edit_default_sends_no_tags():
    s = Recorder(CSRF, {"edit": {"result": "Success"}})
    WikiClient("fr", session=s).edit("Module:SV/Util", "text", "sum")
    assert "tags" not in s.calls[-1][1] and "createonly" not in s.calls[-1][1]


def test_page_info_maps_normalized_titles():
    s = Recorder({"query": {
        "normalized": [{"from": "Category:Musica", "to": "Categoria:Musica"}],
        "pages": [
            {"title": "Categoria:Musica", "pageid": 5, "categories": [{"title": "Categoria:Arti dello spettacolo"}],
             "pageprops": {"wikibase_item": "Q7"}},
            {"title": "Category:Nope", "missing": True},
            {"title": "Category:Old", "pageid": 6, "redirect": True},
        ]}})
    info = WikiClient("it", session=s).page_info(["Category:Musica", "Category:Nope", "Category:Old"])
    assert info["Category:Musica"] == PageInfo(True, False, ["Arti dello spettacolo"], "Q7")
    assert info["Category:Nope"] is None
    assert info["Category:Old"] == PageInfo(True, True, [], None)


def test_article_categories_follows_continue():
    s = Recorder(
        {"continue": {"gapcontinue": "B", "continue": "gapcontinue||"},
         "query": {"pages": [{"title": "A", "categories": [{"title": "Category:Music"}]}]}},
        {"query": {"pages": [{"title": "B", "categories": [{"title": "Category:Love"}]}, {"title": "C"}]}})
    assert WikiClient("en", session=s).article_categories() == {"Music", "Love"}
    assert s.calls[1][1]["gapcontinue"] == "B"


def test_item_for_missing_is_none():
    s = Recorder({"entities": {"-1": {"site": "frwiki", "title": "Category:X", "missing": ""}}})
    assert WikiClient("en", session=s).item_for("frwiki", "Category:X") is None
    s = Recorder({"entities": {"Q9": {"id": "Q9", "type": "item"}}})
    assert WikiClient("en", session=s).item_for("frwiki", "Category:X") == "Q9"


def test_new_item_returns_id():
    s = Recorder(CSRF, {"entity": {"id": "Q42"}, "success": 1})
    item = WikiClient("en", session=s).new_item({"en": "Music"}, {"en": "Semina Verbi category"},
                                                {"frwiki": "Category:Musique"}, tags="seminaverbi-mcp")
    assert item == "Q42"
    params = s.calls[-1][1]
    assert params["action"] == "wbeditentity" and params["new"] == "item" and params["tags"] == "seminaverbi-mcp"
    assert '"frwiki"' in params["data"] and '"Category:Musique"' in params["data"]


def test_set_sitelink_params():
    s = Recorder(CSRF, {"success": 1})
    WikiClient("en", session=s).set_sitelink("Q42", "frwiki", "Category:Musique", tags="seminaverbi-mcp")
    params = s.calls[-1][1]
    assert (params["action"], params["id"], params["linksite"], params["linktitle"]) == (
        "wbsetsitelink", "Q42", "frwiki", "Category:Musique")


def test_sitelink_reads_item():
    s = Recorder({"entities": {"Q9": {"id": "Q9", "sitelinks": {"enwiki": {"site": "enwiki", "title": "Category:Video games"}}}}})
    assert WikiClient("en", session=s).sitelink("Q9", "enwiki") == "Category:Video games"
    s = Recorder({"entities": {"Q9": {"id": "Q9", "sitelinks": {}}}})
    assert WikiClient("en", session=s).sitelink("Q9", "frwiki") is None


def test_move_params():
    s = Recorder(CSRF, {"move": {"from": "Category:A", "to": "Category:B"}})
    WikiClient("en", session=s).move("Category:A", "Category:B", "why", tags="seminaverbi-mcp")
    params = s.calls[-1][1]
    assert (params["action"], params["from"], params["to"], params["tags"]) == ("move", "Category:A", "Category:B",
                                                                                "seminaverbi-mcp")


def test_move_failure_is_sync_error():
    s = Recorder(CSRF, {"warnings": {}})
    with pytest.raises(SyncError, match="moving Category:A to Category:B failed"):
        WikiClient("en", session=s).move("Category:A", "Category:B", "why")


def test_members_follows_continue():
    s = Recorder({"continue": {"cmcontinue": "x", "continue": "-||"},
                  "query": {"categorymembers": [{"title": "A", "ns": 0}]}},
                 {"query": {"categorymembers": [{"title": "Categoria:B", "ns": 14}]}})
    assert WikiClient("it", session=s).members("Categoria:X") == [("A", 0), ("Categoria:B", 14)]


def test_category_namespaces_include_local_and_aliases():
    s = Recorder({"query": {"namespaces": {"14": {"name": "Categoria", "canonical": "Category"}},
                            "namespacealiases": [{"id": 14, "alias": "Cat"}, {"id": 2, "alias": "U"}]}})
    assert WikiClient("it", session=s).category_namespaces() == ["Cat", "Categoria", "Category"]


class Limited:
    """Answers 429 (with Retry-After) `times` times, then the JSON body."""

    def __init__(self, times, body, retry_after="1"):
        self.left, self.body, self.retry_after, self.headers, self.calls = times, body, retry_after, {}, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.left:
            self.left -= 1
            return LimitedResponse(self.retry_after)
        return Response(self.body)

    post = get


class LimitedResponse:
    status_code = 429

    def __init__(self, retry_after):
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def raise_for_status(self):
        import requests
        raise requests.HTTPError("429 Client Error: Too Many Requests")


def test_rate_limited_requests_are_retried(monkeypatch):
    waits = []
    monkeypatch.setattr("sync.wiki.time.sleep", waits.append)
    s = Limited(2, {"query": {"tokens": {"logintoken": "t"}}}, retry_after="3")
    assert WikiClient("en", session=s)._token("login") == "t"
    assert s.calls == 3 and waits == [3.0, 3.0]


def test_rate_limit_gives_up_after_retries(monkeypatch):
    import requests
    monkeypatch.setattr("sync.wiki.time.sleep", lambda s: None)
    s = Limited(99, {}, retry_after=None)
    with pytest.raises(requests.HTTPError):
        WikiClient("en", session=s)._token("login")
    assert s.calls == 6


def test_search_source_params():
    s = Recorder({"query": {"search": [{"title": "Template:Libro"}]}})
    assert WikiClient("it", session=s).search_source("Romanzo (Letteratura)") == ["Template:Libro"]
    params = s.calls[-1][1]
    assert params["srsearch"] == 'insource:"Romanzo (Letteratura)"' and params["srnamespace"] == "10|828"


def test_mediawiki_ratelimited_error_is_retried(monkeypatch):
    waits = []
    monkeypatch.setattr("sync.wiki.time.sleep", waits.append)
    s = Recorder({"error": {"code": "ratelimited", "info": "slow down"}},
                 {"error": {"code": "ratelimited", "info": "slow down"}},
                 {"query": {"tokens": {"logintoken": "t"}}})
    assert WikiClient("en", session=s)._token("login") == "t"
    assert len(s.calls) == 3 and waits == [65.0, 65.0]


def test_mediawiki_ratelimited_gives_up(monkeypatch):
    monkeypatch.setattr("sync.wiki.time.sleep", lambda s: None)
    s = Recorder(*([{"error": {"code": "ratelimited", "info": "slow down"}}] * 7))
    with pytest.raises(SyncError, match="ratelimited"):
        WikiClient("en", session=s)._token("login")


def test_retry_after_http_date_and_huge_values_are_handled(monkeypatch):
    waits = []
    monkeypatch.setattr("sync.wiki.time.sleep", waits.append)
    s = Limited(1, {"query": {"tokens": {"logintoken": "t"}}}, retry_after="Wed, 21 Oct 2015 07:28:00 GMT")
    assert WikiClient("en", session=s)._token("login") == "t"
    s = Limited(1, {"query": {"tokens": {"logintoken": "t"}}}, retry_after="86400")
    assert WikiClient("en", session=s)._token("login") == "t"
    assert waits[0] == 1.0 and waits[1] == 120.0

def test_page_info_follows_category_continuation():
    s = Recorder(
        {"continue": {"clcontinue": "5|M", "continue": "||"},
         "query": {"pages": [{"title": "Category:A", "pageid": 5, "categories": [{"title": "Category:B"}]}]}},
        {"query": {"pages": [{"title": "Category:A", "pageid": 5, "categories": [{"title": "Category:Parent"}],
                              "pageprops": {"wikibase_item": "Q1"}}]}})
    info = WikiClient("en", session=s).page_info(["Category:A"])
    assert info["Category:A"].categories == ["B", "Parent"] and info["Category:A"].item == "Q1"
    assert s.calls[1][1]["clcontinue"] == "5|M"


def test_article_categories_replaces_continuation():
    s = Recorder(
        {"continue": {"clcontinue": "1|X", "continue": "||"}, "query": {"pages": [{"title": "A", "categories": [{"title": "Category:M"}]}]}},
        {"continue": {"gapcontinue": "C", "continue": "gapcontinue||"}, "query": {"pages": [{"title": "A", "categories": [{"title": "Category:N"}]}]}},
        {"query": {"pages": [{"title": "C", "categories": [{"title": "Category:O"}]}]}})
    assert WikiClient("en", session=s).article_categories() == {"M", "N", "O"}
    assert "clcontinue" not in s.calls[2][1] and s.calls[2][1]["gapcontinue"] == "C"
