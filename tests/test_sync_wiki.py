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
