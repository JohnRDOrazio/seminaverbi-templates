import hashlib

import pytest

from pipeline.press.judge import CULTURE, PICK, SUBSTANTIVE, PressJudge, article_key
from pipeline.queue import Queue
from test_jev import FakeJev, choice
from tools import jev

URL = "https://example.org/a"


@pytest.fixture
def queue(tmp_path):
    return Queue(tmp_path / "q.sqlite")


class Counting(FakeJev):
    """FakeJev that also checks the 150-word bound on every text it is sent."""

    def system_one(self, state, questions, model=None):
        assert len(state["text"].split()) <= 150
        return super().system_one(state, questions, model)


def test_article_key():
    assert article_key(URL) == "press:" + hashlib.sha1(URL.encode()).hexdigest()


def test_answers_are_cached_per_article(queue):
    fake = Counting({"culture": 0.8})
    judge = PressJudge(fake, queue)
    assert judge.culture(URL, "Title. Opening text.") == 0.8
    assert judge.culture(URL, "Title. Opening text.") == 0.8
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == jev.MODEL
    assert fake.calls[0]["questions"]["culture"].instructions == CULTURE
    row = queue.db.execute("SELECT qid FROM jev").fetchall()
    assert [r["qid"] for r in row] == [article_key(URL)]


def test_pick_returns_qid_probability_confidence(queue):
    fake = Counting({"pick": choice({"Q1": 0.85, "Q2": 0.1, "none": 0.05}, 0.7)})
    out = PressJudge(fake, queue).pick(URL, "passage", "Ben-Hur", {"Q1": "Ben-Hur — 1959 film", "Q2": "Ben-Hur — novel"})
    assert out == ("Q1", 0.85, 0.7)
    q = fake.calls[0]["questions"]["pick"]
    assert q.instructions == PICK
    assert q.criteria == {"Q1": {"name": "Ben-Hur — 1959 film"}, "Q2": {"name": "Ben-Hur — novel"},
                          "none": {"name": "None of these"}}
    assert fake.calls[0]["state"] == {"text": "passage", "mention": "Ben-Hur"}


def test_pick_none_returns_none(queue):
    fake = Counting({"pick": choice({"Q1": 0.1, "none": 0.9}, 0.8)})
    assert PressJudge(fake, queue).pick(URL, "p", "X", {"Q1": "X — y"}) == (None, 0.9, 0.8)


def test_substantive_names_the_item(queue):
    fake = Counting({"substantive": 0.6})
    judge = PressJudge(fake, queue)
    assert judge.substantive(URL, "Ben-Hur", "passage", "Ben-Hur — 1959 film") == 0.6
    assert fake.calls[0]["questions"]["substantive"].instructions == SUBSTANTIVE
    assert fake.calls[0]["state"] == {"text": "passage", "mention": "Ben-Hur", "item": "Ben-Hur — 1959 film"}


def test_substantive_asks_about_the_item_as_a_subject_not_the_name():
    """The 2026-11-23 dry run kept six Bond titles listed in a casting article and Botticelli's Birth of Venus
    used as a comparison: the question names the item and tells the subjects from the rest."""
    assert "`item`" in SUBSTANTIVE
    for wording in ("subjects", "listed", "in passing", "comparison", "another sense"):
        assert wording in SUBSTANTIVE


def test_text_over_150_words_is_refused(queue):
    fake, long = Counting(), " ".join(["w"] * 151)
    judge = PressJudge(fake, queue)
    with pytest.raises(ValueError):
        judge.culture("u", long)
    with pytest.raises(ValueError):
        judge.pick("u", long, "X", {})
    with pytest.raises(ValueError):
        judge.substantive("u", "X", long, "X — y")
    assert fake.calls == []
    PressJudge(Counting({"culture": 0.5}), queue).culture("u", " ".join(["w"] * 150))
