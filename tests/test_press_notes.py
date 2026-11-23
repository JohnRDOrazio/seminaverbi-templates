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
                        "label": "Vatican News (2026-04-02): New", "text": "new words"}

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


def test_a_mention_with_five_passages_yields_three(store):
    mention(store, "http://a/1", "Q1", ["a", "b", "c", "d", "e"])
    (n,) = press_notes(store, "Q1", OUTLETS)
    assert n["text"] == "a … b … c"


def _run(queue, outcome, data, monkeypatch, qid="Q1"):
    class Dom:
        __name__ = "pipeline.domains.film"

        @staticmethod
        def dossier_stage(row, cfg, wiki_for, wikidata):
            return outcome, dict(data)

    monkeypatch.setattr(cli, "_wikidata", lambda: None)
    queue.add(qid, "film", "s", {"en": "F"}, {})
    row = queue._rows("qid=?", (qid,))[0]
    return cli._stage_functions(Dom, queue, {})[4](row)


@pytest.mark.parametrize("outcome,data", [
    ("pass", {"notes": [], "languages": {}}),
    ("pass", {"languages": {}}),
    ("drop", {"reason": "empty dossier"}),
])
def test_no_mentions_leaves_the_domain_output_untouched(queue, monkeypatch, outcome, data):
    assert _run(queue, outcome, data, monkeypatch) == (outcome, data)


def test_press_only_row_with_empty_wiki_dossier_passes(store, queue, monkeypatch):
    mention(store, "http://a/1", "Q1", ["x y"])
    outcome, data = _run(queue, "drop", {"reason": "empty dossier"}, monkeypatch)
    assert outcome == "pass" and "reason" not in data
    assert [n["url"] for n in data["notes"]] == ["http://a/1"] and data["languages"] == {"it": 1}


def test_other_drop_reason_stays_dropped(store, queue, monkeypatch):
    mention(store, "http://a/1", "Q1", ["x y"])
    assert _run(queue, "drop", {"reason": "other"}, monkeypatch) == ("drop", {"reason": "other"})


def test_missing_press_config_does_not_break_rows_without_mentions(queue, monkeypatch):
    def boom():
        raise OSError("no config")
    monkeypatch.setattr(cli, "load_press_config", boom)
    assert _run(queue, "pass", {"notes": [], "languages": {}}, monkeypatch)[0] == "pass"


def test_press_notes_count_in_the_languages_of_a_passing_dossier(store, queue, monkeypatch):
    mention(store, "http://a/1", "Q1", ["x y"])
    mention(store, "http://a/2", "Q1", ["z w"])
    wiki = {"notes": [{"lang": "en", "url": "https://en.wikipedia.org/wiki/F", "heading": "Plot", "text": "t"}],
            "languages": {"en": 1, "de": 0}}
    outcome, data = _run(queue, "pass", wiki, monkeypatch)
    assert outcome == "pass" and len(data["notes"]) == 3
    assert data["languages"] == {"en": 1, "de": 0, "it": 2}
    from pipeline import stages
    assert stages._first_wiki(data["languages"]) == "it"


def test_a_neutral_or_critical_mention_gives_no_note():
    store = PressStore.of(Queue(":memory:"))
    for outcome in ("bearing", "mixed", "neutral", "critical", None):
        url = f"https://x/{outcome}"
        store.add_article(url, "sir", "it", "T", "2026-11-20")
        store.add_mention(Mention(url, "Q1", "x", "work", "film", 0.9, ["parole"], stance_outcome=outcome))
    assert {n["url"] for n in press_notes(store, "Q1", {})} == {"https://x/bearing", "https://x/mixed", "https://x/None"}


def test_an_article_without_a_title_is_named_by_its_url(store):
    mention(store, "http://a/x", "Q1", ["words"], title=None)
    mention(store, "http://a/y", "Q1", ["words"], title=None, published=None)
    assert [n["label"] for n in press_notes(store, "Q1", OUTLETS)] == ["Avvenire (2026-03-01): http://a/x",
                                                                       "Avvenire: http://a/y"]
