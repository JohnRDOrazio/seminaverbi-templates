import pytest

from pipeline.press.enrich import ENRICH_TITLE, done_urls, enrichment, publish_enrichment
from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue
from tests.test_pipeline_publish import FakeEn

OUTLETS = {"avvenire": "Avvenire", "vn": "Vatican News"}


@pytest.fixture
def store():
    return PressStore.of(Queue(":memory:"))


def add(store, url, qid="Q1", label="Silenzio", published="2026-03-01", passages=("a b c",), outlet="avvenire",
        lang="it", title="T"):
    store.add_article(url, outlet, lang, title, published)
    store.add_mention(Mention(url, qid, label, "work", "film", 0.9, list(passages), "enrichment"))


def test_enrichment_lists_newest_first_with_passages(store):
    add(store, "https://x/old", published="2026-01-05", title="Old", passages=["one", "two", "three", "four"])
    add(store, "https://x/new", published="2026-04-02", title="New", outlet="vn", lang="en", passages=["fresh words"])
    add(store, "https://x/skip", qid="Q2", label="Other", published="2026-02-01")
    store.set_route("https://x/skip", "Q2", "candidate")
    text = enrichment(store, OUTLETS, None, titles={"Q1": {"it": "Silenzio (film)", "en": "Silence"}})
    assert text.startswith("Articles of the Catholic press on works SeminaVerbi already covers.")
    assert "{{SV press done|<url>}}" in text.split("<!-- press:begin -->")[0]
    block = text.split("<!-- press:begin -->")[1].split("<!-- press:end -->")[0]
    assert "[[:it:Silenzio (film)]]" in block and "[[:en:Silence]]" in block
    assert block.index("https://x/new") < block.index("https://x/old")
    assert "Vatican News" in block and "2026-04-02" in block and "[https://x/new New]" in block
    assert '"fresh words"' in block
    assert '"three"' in block and "four" not in block
    assert "Other" not in block


def test_label_alone_without_titles(store):
    add(store, "https://x/a")
    assert "Silenzio" in enrichment(store, OUTLETS, None)


def test_enrichment_keeps_done_markers(store):
    add(store, "https://x/a")
    add(store, "https://x/b")
    old = "intro\n<!-- press:begin -->\nx\n<!-- press:end -->\n{{SV press done|https://x/a}}\nnote by editor\n"
    assert done_urls(old) == {"https://x/a"}
    text = enrichment(store, OUTLETS, old)
    assert "https://x/a" not in text.split("<!-- press:end -->")[0]
    assert "https://x/b" in text.split("<!-- press:end -->")[0]
    assert text.endswith("{{SV press done|https://x/a}}\nnote by editor\n")


def test_done_urls_none_and_spaces():
    assert done_urls(None) == set()
    assert done_urls("{{SV press done | https://x/a }}") == {"https://x/a"}


def test_unchanged_page_is_not_rewritten(store):
    add(store, "https://x/a")
    en = FakeEn()
    assert publish_enrichment(store, en, OUTLETS) is True
    assert en.edits[0]["title"] == ENRICH_TITLE
    assert publish_enrichment(store, en, OUTLETS) is False
    assert len(en.edits) == 1
