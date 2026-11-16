import pytest

from sync.wiki import SyncError, WikiClient


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
