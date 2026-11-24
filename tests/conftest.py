import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test whose fakes miss a client fails here instead of asking the live wikis."""
    def refuse(self, request, **kw):
        raise AssertionError(f"a test tried to reach {request.url}")
    monkeypatch.setattr(requests.Session, "send", refuse)


@pytest.fixture
def use_repository(monkeypatch):
    """use_repository(fake): the client the code makes for the repository wiki is `fake`. Page wikis given as
    `wikis` get it too when the repository is one of them."""
    import pipeline.__main__
    import sync.wiki

    def use(fake, wikis=None):
        def factory(wiki):
            assert wiki == sync.wiki.REPO_WIKI
            return fake
        monkeypatch.setattr(sync.wiki, "WikiClient", factory)
        monkeypatch.setattr(pipeline.__main__, "WikiClient", factory)
        if wikis is not None and sync.wiki.REPO_WIKI in wikis:
            wikis[sync.wiki.REPO_WIKI] = fake
    return use
