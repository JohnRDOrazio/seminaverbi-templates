from types import SimpleNamespace

import pytest
from typesafe_sdk import TypeSafeError

from pipeline.judge import CONNECTIONS, Judge, qhash
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
    assert fake.calls[0]["questions"]["connection"].criteria["none"] == {"name": CONNECTIONS["none"]}
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


def test_quick_without_wikipedia_sitelink(queue):
    queue.add("Q3", "film", "wikidata", {}, {"commonswiki": "X"})
    queue.add("Q4", "film", "wikidata", {}, {}, calibration=True)
    rows = {x.qid: x for x in queue.pending(1)}
    j = FakeJudge(0.9)
    assert quick(rows["Q3"], j, FakeWikipedia) == ("drop", {"reason": "no wikipedia sitelink"})
    assert quick(rows["Q4"], j, FakeWikipedia) == ("pass", {})
    assert j.seen == []


class EmptyLead(FakeWikipedia):
    def lead(self, title):
        return ""


def test_quick_drops_a_row_with_an_empty_lead_without_asking(queue):
    j = FakeJudge(0.9)
    assert quick(row(queue), j, EmptyLead) == ("drop", {"reason": "no lead"})
    assert j.seen == []


def asks_again(make_judge, ask, change, monkeypatch, answers):
    """True when, after `change`, the same question is no longer answered from the cache."""
    fake = FakeJev(*answers)
    ask(make_judge(fake))
    n = len(fake.calls)
    change(monkeypatch)
    ask(make_judge(fake))
    return len(fake.calls) > n


def test_cache_keys_follow_the_question_texts_and_model(queue, monkeypatch):
    import copy
    from pipeline import judge as judge_module
    r = row(queue)
    make = lambda fake: Judge(fake, queue, DATA)
    conn = choice({"direct": 0.9, "seeds": 0.05, "entertainment": 0.03, "none": 0.02}, 0.9)
    assert asks_again(make, lambda j: j.connection(r, "d"),
                      lambda mp: mp.setitem(judge_module.CONNECTIONS, "none", "nothing at all"),
                      monkeypatch, [{"connection": conn}, {"connection": conn}])
    assert asks_again(make, lambda j: j.plausible(r, "s"), lambda mp: mp.setattr(jev, "MODEL", "jev-9"),
                      monkeypatch, [{"plausible": 0.5}, {"plausible": 0.5}])
    assert asks_again(make, lambda j: j.connection(r, "d"), lambda mp: mp.setattr(jev, "MODEL", "jev-10"),
                      monkeypatch, [{"connection": conn}, {"connection": conn}])
    ev = {"evidence": choice({"p0": 0.9, "p1": 0.1}, 0.9)}
    assert asks_again(make, lambda j: j.evidence(r, "faith", ["a", "b"]), lambda mp: mp.setattr(jev, "MODEL", "jev-11"),
                      monkeypatch, [ev, ev])
    themes = {t.key: 0.5 for t in __import__("sync.categories", fromlist=["leaf_themes"]).leaf_themes(DATA)}
    changed = copy.deepcopy(DATA)
    holder = {"data": DATA}
    make_t = lambda fake: Judge(fake, queue, holder["data"])

    def edit_theme(mp):
        changed["themes"][0]["children"][0]["definition"] = {"en": "A new definition."}
        holder["data"] = changed
    assert asks_again(make_t, lambda j: j.themes(r, "d"), edit_theme, monkeypatch, [themes, themes])
    holder["data"] = DATA
    changed_fields = copy.deepcopy(DATA)

    def edit_field(mp):
        changed_fields["fields"][0]["children"][0]["definition"] = {"en": "Moving pictures, redefined."}
        holder["data"] = changed_fields
    levels = [{"level": choice({"arts": 0.95, "religion": 0.05}, 0.9)},
              {"level": choice({"film": 0.95, "music": 0.05}, 0.9)},
              {"level": choice({"feature-films": 0.95, "short-films": 0.05}, 0.9)}]
    assert asks_again(make, lambda j: j.field(r, "d"),
                      lambda mp: mp.setattr(jev, "LEVEL_INSTRUCTIONS", "Which category? (reworded)"),
                      monkeypatch, levels + levels)
    # per-request cache: only the level whose options changed (film's definition, offered at level 2) is asked
    # again; the reworded instructions above are still in force, so the first ask is answered from the cache
    fake = FakeJev(levels[1])
    edit_field(monkeypatch)
    assert make_t(fake).field(r, "d")["choice"] == "feature-films"
    assert [set(c["questions"]["level"].criteria) for c in fake.calls] == [{"film", "music"}]


class FailsAfter(FakeJev):
    """Answers the given responses, then raises TypeSafeError (a paid level succeeded, the next one failed)."""

    def system_one(self, state, questions, model=None):
        if not self.responses:
            self.calls.append({"state": state, "questions": questions, "model": model})
            raise TypeSafeError("down")
        return super().system_one(state, questions, model)


LEVELS = [{"level": choice({"arts": 0.95, "religion": 0.05}, 0.9)},
          {"level": choice({"film": 0.95, "music": 0.05}, 0.9)},
          {"level": choice({"feature-films": 0.95, "short-films": 0.05}, 0.9)}]


def test_a_failed_level_keeps_the_paid_levels_before_it(queue):
    """CodeRabbit 4640187385: level 1 answered, level 2 failed; the retry must not pay for level 1 again."""
    first = FailsAfter(LEVELS[0])
    with pytest.raises(TypeSafeError):
        Judge(first, queue, DATA).field(row(queue), "dossier")
    assert len(first.calls) == 2
    retry = FakeJev(LEVELS[1], LEVELS[2])
    d = Judge(retry, queue, DATA).field(row(queue), "dossier")
    assert d["choice"] == "feature-films" and d["outcome"] == "applied"
    assert len(retry.calls) == 2
    assert [set(c["questions"]["level"].criteria) for c in retry.calls] == [{"film", "music"},
                                                                          {"feature-films", "short-films"}]


def test_a_cached_level_is_shared_by_the_same_question_on_the_same_row(queue):
    fake = FakeJev(*LEVELS)
    judge = Judge(fake, queue, DATA)
    judge.field(row(queue), "dossier")
    assert len(fake.calls) == 3
    assert judge.field(row(queue), "dossier")["choice"] == "feature-films"
    assert len(fake.calls) == 3


def _stored_questions(queue):
    return [r["question"] for r in queue.db.execute("SELECT question FROM jev")]


def test_the_queue_keeps_the_question_text_not_only_its_name(queue):
    """CodeRabbit 4640187374: the `question` column holds the wording that was asked (and hashed)."""
    from pipeline.judge import CONNECTION, CONNECTIONS, PLAUSIBLE
    Judge(FakeJev({"plausible": 0.7}), queue, DATA).plausible(row(queue), "A synopsis.")
    conn = choice({"direct": 0.9, "seeds": 0.05, "entertainment": 0.03, "none": 0.02}, 0.9)
    Judge(FakeJev({"connection": conn}), queue, DATA).connection(row(queue), "dossier")
    stored = _stored_questions(queue)
    assert len(stored) == 2
    assert any(PLAUSIBLE in s and jev.MODEL in s for s in stored)
    assert any(CONNECTION in s and CONNECTIONS["none"] in s for s in stored)
    assert "plausible" not in stored and "connection" not in stored


def test_the_connection_question_allows_an_implicit_tie_in_one_storyline_or_episode():
    from pipeline.judge import CONNECTION, CONNECTIONS
    assert "seeds of the Word" in CONNECTION
    assert all(w in CONNECTION for w in ("storyline", "character", "episode"))
    assert "episode" in CONNECTIONS["seeds"] and "not even" in CONNECTIONS["none"]


FILM_CONNECTION_HASH = "32ec60aff78f226ca6fca45dad148563c36db4ca04a5a66f30955e560cd30c73"  # recorded before the change
FILM_PLAUSIBLE_HASH = "ecf1345b053aa8c295db60ca8909053f6f794b3b78c6e1439db7a23503c42c1c"


def _wording_hash(fake, key):
    from pipeline.judge import question_text
    return qhash("system_one", question_text(jev.MODEL, {key: fake.calls[0 if key == "connection" else 1]["questions"][key]}),
                 "{}")


def test_film_wording_is_unchanged(queue):
    # state is {"text": ...}; the recorded hashes use "{}" as state, so compare the wording hash only
    fake = FakeJev({"connection": choice({"direct": 1.0, "seeds": 0.0, "entertainment": 0.0, "none": 0.0}, 0.9)},
                   {"plausible": 0.5})
    judge = Judge(fake, queue, DATA)
    judge.connection(row(queue), "t")
    judge.plausible(row(queue), "t")
    assert _wording_hash(fake, "connection") == FILM_CONNECTION_HASH
    assert _wording_hash(fake, "plausible") == FILM_PLAUSIBLE_HASH


def test_a_row_is_asked_with_its_domain_wording(queue, monkeypatch):
    from pipeline.domains import DOMAINS
    lit = SimpleNamespace(QUESTIONS={"plausible": "LIT plausible?", "connection": "LIT connection?",
                                     "connections": {"a": "alpha", "b": "beta"}})
    monkeypatch.setitem(DOMAINS, "literature", lit)
    queue.add("L1", "literature", "wikidata", {"en": "B"}, {})
    r = queue.row("L1")
    fake = FakeJev({"connection": choice({"a": 0.9, "b": 0.1}, 0.9)}, {"plausible": 0.5})
    judge = Judge(fake, queue, DATA)
    judge.connection(r, "text")
    judge.plausible(r, "text")
    q = fake.calls[0]["questions"]["connection"]
    assert q.instructions == "LIT connection?" and set(q.criteria) == {"a", "b"}
    assert fake.calls[1]["questions"]["plausible"].instructions == "LIT plausible?"


def test_an_unknown_domain_uses_film_wording(queue):
    from pipeline.domains import film
    queue.add("X1", "nowhere", "wikidata", {"en": "C"}, {})
    r = queue.row("X1")
    fake = FakeJev({"connection": choice({"direct": 1.0, "seeds": 0.0, "entertainment": 0.0, "none": 0.0}, 0.9)})
    Judge(fake, queue, DATA).connection(r, "t")
    assert fake.calls[0]["questions"]["connection"].instructions == film.QUESTIONS["connection"]


def test_quick_reads_a_borrowed_lead(queue):
    queue.add("Q5", "literature", "author", {}, {}, data={"lead_sitelinks": {"enwiki": "Best volume"}})
    r = next(x for x in queue.pending(1) if x.qid == "Q5")
    r.data = {0: {"lead_sitelinks": {"enwiki": "Best volume"}}}
    j = FakeJudge(0.9)
    assert quick(r, j, FakeWikipedia) == ("pass", {"p": 0.9})
    assert j.seen == ["en:Best volume"]


def test_quick_prefers_the_rows_own_wikipedia_over_a_borrowed_lead(queue):
    queue.add("Q6", "literature", "author", {}, {"itwiki": "Own"})
    r = next(x for x in queue.pending(1) if x.qid == "Q6")
    r.data = {0: {"lead_sitelinks": {"enwiki": "Best volume"}}}
    j = FakeJudge(0.9)
    quick(r, j, FakeWikipedia)
    assert j.seen == ["it:Own"]
