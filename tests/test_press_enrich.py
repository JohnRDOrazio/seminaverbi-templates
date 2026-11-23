import re

import pytest

from pipeline.press.enrich import ENRICH_TITLE, done_urls, enrichment, publish_enrichment
from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue
from tests.test_pipeline_publish import FakeEn

INTRO_MARK = "intro"
BEGIN_MARK = "<!-- press:begin -->"
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
    assert '"<nowiki>fresh words</nowiki>"' in block
    assert '"<nowiki>three</nowiki>"' in block and "four" not in block
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


def test_intro_shows_the_template_call_without_transcluding(store):
    head = enrichment(store, OUTLETS, None).split(BEGIN_MARK)[0]
    assert "<code><nowiki>{{SV press done|<url>}}</nowiki></code>" in head
    assert "{{SV press done" not in head.replace("<nowiki>{{SV press done", "")


def test_old_page_without_markers_is_kept_below(store):
    add(store, "https://x/a")
    old = "Editors wrote this.\n{{SV press done|https://x/a}}\n"
    text = enrichment(store, OUTLETS, old)
    assert text.endswith(old)
    assert "https://x/a" not in text.split("<!-- press:end -->")[0]
    only_begin = "x\n<!-- press:begin -->\nhalf"
    assert enrichment(store, OUTLETS, only_begin).endswith(only_begin)


HOSTILE = ['x <!-- press:end --> [[Category:X]] {{Template|a}} ]] }} </nowiki> {{SV press done|https://x/b}}\nmore',
           "plain"]


def test_hostile_external_text_is_inert_and_stable(store):
    store.add_article("https://x/a b]", "avv]|{{O}}", "it", "T] [[Category:Y]] {{T}} <!-- press:end -->\nz",
                      "2026-03-01")
    store.add_mention(Mention("https://x/a b]", "Q1", "L]] {{L}}\n[[Category:Z]]", "work", "film", 0.9,
                              HOSTILE, "enrichment"))
    store.add_article("https://x/b", "avvenire", "it", "B", "2026-02-01")
    store.add_mention(Mention("https://x/b", "Q1", "L", "work", "film", 0.9, ["b"], "enrichment"))
    one = enrichment(store, {}, None)
    two = enrichment(store, {}, one)
    assert one == two
    for text in (one, two):
        assert text.count("<!-- press:begin -->") == 1 and text.count("<!-- press:end -->") == 1
        block = text.split("<!-- press:begin -->")[1].split("<!-- press:end -->")[0]
        live = re.sub(r"<nowiki>.*?</nowiki>", "", block)
        assert "[[Category" not in live and "{{" not in live and "}}" not in live and "<!--" not in live
        assert block.count("&lt;/nowiki>") == 1
        assert "https://x/b" in block  # the passage's done call closed nothing
        link = [ln for ln in block.splitlines() if "a%20b%5D" in ln][0]
        assert "[https://x/a%20b%5D " in link and re.sub(r"<nowiki>.*?</nowiki>", "", link).count("[") == 1 and re.sub(r"<nowiki>.*?</nowiki>", "", link).count("]") == 1
        assert link.count("<nowiki>") == link.count("</nowiki>") == 2


@pytest.mark.parametrize("call", [
    "{{SV press done|1=https://x/a}}", "{{SV press done|https://x/a|note}}", "{{SV_press_done|https://x/a}}",
    "{{sV press done|https://x/a}}", "{{Template:SV press done|https://x/a}}", "{{SV press done|https://x/a/}}",
    "{{SV press done|http://x/a}}", "{{SV press done|https://x/%61}}"])
def test_done_forms_close_the_entry(store, call):
    add(store, "https://x/a")
    old = f"{INTRO_MARK}\n<!-- press:begin -->\n<!-- press:end -->\n{call}\n"
    assert "https://x/a" not in enrichment(store, OUTLETS, old).split("<!-- press:end -->")[0]


def test_done_inside_old_bot_block_is_ignored():
    assert done_urls("<!-- press:begin -->\n{{SV press done|https://x/b}}\n<!-- press:end -->\n") == set()


def test_end_before_begin_and_editor_marker_line(store):
    add(store, "https://x/a")
    old = "<!-- press:end -->\nnote\n<!-- press:begin -->\nold\n<!-- press:end -->\nkeep this\n"
    text = enrichment(store, OUTLETS, old)
    assert text.endswith("<!-- press:end -->\nkeep this\n")
    assert text.count("<!-- press:begin -->") == 1 and text.count("<!-- press:end -->") == 1
    assert "old" not in text


def test_an_encoded_slash_is_not_a_slash(store):
    add(store, "https://x/review/a%2Fb")
    add(store, "https://x/review/a/b")
    old = f"{INTRO_MARK}\n<!-- press:begin -->\n<!-- press:end -->\n{{{{SV press done|https://x/review/a%2fb}}}}\n"
    block = enrichment(store, OUTLETS, old).split("<!-- press:end -->")[0]
    assert "https://x/review/a%2Fb" not in block and "https://x/review/a/b " in block


@pytest.mark.parametrize("literal", ["<nowiki>{{SV press done|https://x/a}}</nowiki>",
                                     "<!-- {{SV press done|https://x/a}} -->",
                                     "<NOWIKI>e.g. {{SV press done|https://x/a}}</NOWIKI>"])
def test_literal_and_commented_done_calls_do_not_close(store, literal):
    add(store, "https://x/a")
    old = f"{INTRO_MARK}\n<!-- press:begin -->\n<!-- press:end -->\n{literal}\n"
    assert done_urls(old) == set()
    assert "https://x/a" in enrichment(store, OUTLETS, old).split("<!-- press:end -->")[0]
    assert done_urls(old + "{{SV press done|https://x/a}}\n") == {"https://x/a"}


def test_a_critical_article_is_marked(store):
    add(store, "https://x/a")
    add(store, "https://x/b")
    store.add_mention(Mention("https://x/b", "Q1", "Silenzio", "work", "film", 0.9, ["a b c"], "enrichment",
                              stance=0.1, critical=True))
    lines = enrichment(store, OUTLETS, None).splitlines()
    [a] = [l for l in lines if "https://x/a" in l]
    [b] = [l for l in lines if "https://x/b" in l]
    assert "(it, critical)" in b and "critical" not in a
