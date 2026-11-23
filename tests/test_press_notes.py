import pytest

from pipeline import __main__ as cli
from pipeline.press.notes import press_notes
from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue

OUTLETS = {"avvenire": "Avvenire", "vn": "Vatican News"}


@pytest.fixture
def queue():
    return Queue(":memory:")


@pytest.fixture
def store(queue):
    return PressStore.of(queue)


def mention(store, url, qid, passages, outlet="avvenire", lang="it", title="T", published="2026-03-01"):
    store.add_article(url, outlet, lang, title, published)
    store.add_mention(Mention(url, qid, "L", "work", "film", 0.9, passages, "candidate"))


def test_press_notes_come_first_in_the_dossier(store, queue):
    mention(store, "http://a/old", "Q1", ["old words"], title="Old", published="2026-01-05")
    mention(store, "http://a/new", "Q1", ["new words"], outlet="vn", lang="en", title="New", published="2026-04-02")
    mention(store, "http://a/other", "Q2", ["x"])
    notes = press_notes(store, "Q1", OUTLETS)
    assert [n["url"] for n in notes] == ["http://a/new", "http://a/old"]
    assert notes[0] == {"lang": "en", "url": "http://a/new", "heading": "Vatican News (2026-04-02): New",
                        "text": "new words"}

    class Dom:
        __name__ = "pipeline.domains.film"

        @staticmethod
        def dossier_stage(row, cfg, wiki_for, wikidata):
            return "pass", {"notes": [{"lang": "en", "url": "http://w", "heading": "h", "text": "t"}],
                            "languages": {"en": 1}}

    queue.add("Q1", "film", "s", {"en": "F"}, {})
    row = queue._rows("qid=?", ("Q1",))[0]
    fn = cli._stage_functions(Dom, queue, {})[4]
    outcome, data = fn(row)
    assert outcome == "pass"
    assert [n["url"] for n in data["notes"]] == ["http://a/new", "http://a/old", "http://w"]


def test_note_text_is_only_the_stored_passages(store):
    mention(store, "http://a/1", "Q1", ["one two", "three four", "five"])
    (n,) = press_notes(store, "Q1", OUTLETS)
    assert n["text"] == "one two … three four … five"


def test_unknown_outlet_falls_back_to_its_id_and_missing_date(store):
    mention(store, "http://a/1", "Q1", ["x"], outlet="zz", published=None)
    (n,) = press_notes(store, "Q1", OUTLETS)
    assert n["heading"] == "zz: T"


def test_no_mentions_no_notes(store):
    assert press_notes(store, "Q9", OUTLETS) == []
