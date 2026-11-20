from types import SimpleNamespace

import pytest
from typesafe_sdk import TypeSafeError

from pipeline.judge import Judge, qhash
from pipeline.queue import Queue
from pipeline.stages import quick
from test_jev import DATA, FakeJev, choice
from tools import jev


@pytest.fixture
def queue(tmp_path):
    q = Queue(tmp_path / "q.sqlite")
    q.add("Q1", "film", "wikidata", {"en": "A"}, {"enwiki": "A (film)", "itwiki": "A"})
    return q


def row(queue):
    return queue.pending(1)[0]


def test_cached_answer_is_not_asked_again(queue):
    fake = FakeJev({"plausible": 0.7})
    judge = Judge(fake, queue, DATA)
    assert judge.plausible(row(queue), "A synopsis.") == 0.7
    assert judge.plausible(row(queue), "A synopsis.") == 0.7
    assert len(fake.calls) == 1


def test_changed_wording_is_a_new_hash():
    assert qhash("n", "instr", "text") != qhash("n", "instr two", "text")
    assert qhash("n", "instr", "text") != qhash("n", "instr", "text two")
    assert qhash("n", "instr", "text") == qhash("n", "instr", "text")
    assert len(qhash("n", "i", "t")) == 64


def test_connection_choice_has_the_four_keys(queue):
    probs = {"direct": 0.1, "seeds": 0.6, "entertainment": 0.2, "none": 0.1}
    fake = FakeJev({"connection": choice(probs, 0.8)})
    out = Judge(fake, queue, DATA).connection(row(queue), "dossier")
    assert set(fake.calls[0]["questions"]["connection"].criteria) == {"direct", "seeds", "entertainment", "none"}
    assert fake.calls[0]["questions"]["connection"].criteria["none"] == {"name": "no real connection"}
    assert out == {"choice": "seeds", "probabilities": probs, "confidence": 0.8}


def test_evidence_returns_the_chosen_paragraph_index(queue):
    fake = FakeJev({"evidence": choice({"p0": 0.1, "p1": 0.8, "p2": 0.1}, 0.9)})
    judge = Judge(fake, queue, DATA)
    assert judge.evidence(row(queue), "faith", ["a", "b", "c"]) == 1
    assert judge.evidence(row(queue), "faith", ["a", "b", "c"]) == 1
    assert len(fake.calls) == 1
    assert fake.calls[0]["questions"]["evidence"].criteria == {"p0": {"name": "a"}, "p1": {"name": "b"},
                                                                "p2": {"name": "c"}}
    assert "Faith" in fake.calls[0]["questions"]["evidence"].instructions


def test_field_and_themes_are_cached_json(queue):
    fake = FakeJev({"level": choice({"arts": 0.95, "religion": 0.05}, 0.9)},
                   {"level": choice({"film": 0.95, "music": 0.05}, 0.9)},
                   {"level": choice({"feature-films": 0.95, "short-films": 0.05}, 0.9)},
                   {"faith": 0.9, "hope": 0.5, "family": 0.1})
    judge = Judge(fake, queue, DATA)
    d = judge.field(row(queue), "dossier")
    assert d["choice"] == "feature-films" and d["outcome"] == "applied"
    t = judge.themes(row(queue), "dossier")
    assert t["faith"] == {"p": 0.9, "status": "applied"} and t["hope"]["status"] == "author"
    n = len(fake.calls)
    assert judge.field(row(queue), "dossier") == d and judge.themes(row(queue), "dossier") == t
    assert len(fake.calls) == n


class Boom:
    def system_one(self, **kw):
        raise TypeSafeError("down")


def test_typesafe_error_leaves_no_cache_entry(queue):
    with pytest.raises(TypeSafeError):
        Judge(Boom(), queue, DATA).plausible(row(queue), "S")
    fake = FakeJev({"plausible": 0.4})
    assert Judge(fake, queue, DATA).plausible(row(queue), "S") == 0.4
    assert len(fake.calls) == 1


class FakeJudge:
    def __init__(self, p):
        self.p, self.seen = p, []

    def plausible(self, row, synopsis):
        self.seen.append(synopsis)
        return self.p


class FakeWikipedia:
    def __init__(self, lang):
        self.lang = lang

    def lead(self, title):
        return f"{self.lang}:{title}"


def test_quick_uses_first_language_and_drops_below_threshold(queue):
    r = row(queue)
    j = FakeJudge(0.2)
    assert quick(r, j, FakeWikipedia) == ("drop", {"p": 0.2})
    assert j.seen == ["en:A (film)"]
    assert quick(r, FakeJudge(0.5), FakeWikipedia) == ("pass", {"p": 0.5})


def test_quick_falls_back_to_next_language_and_keeps_calibration(queue):
    queue.add("Q2", "film", "wikidata", {}, {"itwiki": "B"}, calibration=True)
    r = next(x for x in queue.pending(1) if x.qid == "Q2")
    j = FakeJudge(0.0)
    assert quick(r, j, FakeWikipedia) == ("pass", {"p": 0.0})
    assert j.seen == ["it:B"]
