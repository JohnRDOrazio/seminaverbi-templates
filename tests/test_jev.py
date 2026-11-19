import json
from types import SimpleNamespace

import pytest

from tools import jev

LANGS = ["en", "it", "es", "fr", "de", "pt"]


def node(key, children=None, definition=None):
    n = {"key": key, "names": {lang: key.title() for lang in LANGS},
         "definition": {"en": definition or f"Definition of {key}."}}
    if children:
        n["children"] = children
    return n


DATA = {
    "root": {"names": {lang: "Root" for lang in LANGS}, "definition": {"en": "Everything."}},
    "themes_root": {"names": {lang: "Themes" for lang in LANGS}, "definition": {"en": "Themes."}},
    "fields": [
        node("arts", [node("film", [node("feature-films"), node("short-films")]), node("music")]),
        node("religion", [node("saints")]),
    ],
    "themes": [node("virtues", [node("faith"), node("hope")]), node("society", [node("family")])],
}


def choice(probs, confidence):
    best = max(probs, key=probs.get)
    return SimpleNamespace(choice=best, probabilities=probs, confidence=confidence)


class FakeJev:
    """Answers system_one from a queue of {question_id: answer} dicts and records every call."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def system_one(self, state, questions, model=None):
        self.calls.append({"state": state, "questions": questions, "model": model})
        answers = self.responses.pop(0)
        return SimpleNamespace(
            choices={k: v for k, v in answers.items() if hasattr(v, "choice")},
            nouls={k: SimpleNamespace(noul=v) for k, v in answers.items() if isinstance(v, float)},
        )


def nodes_by_key():
    from sync.categories import nodes
    return {n.key: n for n in nodes(DATA)}


def test_place_options_are_candidates_with_definitions():
    by = nodes_by_key()
    fake = FakeJev({"place": choice({"arts": 0.9, "religion": 0.1}, 0.8)})
    d = jev.place(fake, by["saints"], [by["arts"], by["religion"]], DATA)
    q = fake.calls[0]["questions"]["place"]
    assert set(q.criteria) == {"arts", "religion"}
    assert "Definition of arts." in json.dumps(q.criteria["arts"])
    assert fake.calls[0]["state"]["node"]["definition"] == "Definition of saints."
    assert d.choice == "arts" and d.node == "saints"


@pytest.mark.parametrize("p, conf, outcome", [(0.85, 0.7, "applied"), (0.85, 0.5, "author"), (0.7, 0.9, "author")])
def test_place_policy_applied_vs_author(p, conf, outcome):
    by = nodes_by_key()
    fake = FakeJev({"place": choice({"arts": p, "religion": round(1 - p, 2)}, conf)})
    assert jev.place(fake, by["saints"], [by["arts"], by["religion"]], DATA).outcome == outcome


def test_model_pinned():
    by = nodes_by_key()
    fake = FakeJev({"place": choice({"arts": 1.0, "religion": 0.0}, 1.0)})
    jev.place(fake, by["saints"], [by["arts"], by["religion"]], DATA)
    assert fake.calls[0]["model"] == "jev-1.14.0" == jev.MODEL


def test_classify_descends_while_confident():
    fake = FakeJev(
        {"level": choice({"arts": 0.95, "religion": 0.05}, 0.9)},
        {"level": choice({"film": 0.9, "music": 0.1}, 0.85)},
        {"level": choice({"feature-films": 0.92, "short-films": 0.08}, 0.9)},
    )
    d = jev.classify(fake, "A feature film about a saint.", DATA)
    assert d.choice == "feature-films" and d.outcome == "applied"
    assert "medium" in fake.calls[0]["questions"]["level"].instructions


def test_classify_stops_at_parent_when_unsure():
    fake = FakeJev(
        {"level": choice({"arts": 0.95, "religion": 0.05}, 0.9)},
        {"level": choice({"film": 0.9, "music": 0.1}, 0.85)},
        {"level": choice({"feature-films": 0.55, "short-films": 0.45}, 0.1)},
    )
    d = jev.classify(fake, "A film.", DATA)
    assert d.choice == "film" and d.outcome == "applied"
    # the evidence is the answer that chose film, not the unsure level below it
    assert d.choice in d.probabilities and d.confidence == 0.85 and "film" in d.options


def test_classify_top_level_unsure_goes_to_author():
    # a top-level tie explores both branches (arts, then religion); the result goes to the author
    fake = FakeJev({"level": choice({"arts": 0.5, "religion": 0.5}, 0.0)},
                   {"level": choice({"film": 0.9, "music": 0.1}, 0.9)},
                   {"level": choice({"feature-films": 0.5, "short-films": 0.5}, 0.0)},
                   {"level": choice({"saints": 1.0}, 1.0)})
    d = jev.classify(fake, "?", DATA)
    assert d.choice == "saints" and d.outcome == "author"


def test_classify_clear_but_unsure_top_level_goes_to_author():
    fake = FakeJev({"level": choice({"arts": 0.7, "religion": 0.3}, 0.4)})
    d = jev.classify(fake, "?", DATA)
    assert (d.choice, d.outcome) == ("arts", "author") and len(fake.calls) == 1


def test_classify_beam_on_near_tie():
    # arts 0.52 / religion 0.48 at the top: both branches are explored; religion's child is far surer
    fake = FakeJev(
        {"level": choice({"arts": 0.52, "religion": 0.48}, 0.05)},
        {"level": choice({"film": 0.5, "music": 0.5}, 0.0)},
        {"level": choice({"saints": 1.0}, 1.0)},
    )
    d = jev.classify(fake, "The life of a saint.", DATA)
    assert d.choice == "saints"


def test_themes_only_leaf_themes_one_request():
    fake = FakeJev({"faith": 0.9, "hope": 0.5, "family": 0.1})
    result = jev.themes(fake, "text", DATA)
    assert len(fake.calls) == 1
    assert set(fake.calls[0]["questions"]) == {"faith", "hope", "family"}  # never the groups
    assert result == {"faith": (0.9, "applied"), "hope": (0.5, "author"), "family": (0.1, "no")}


def test_themes_question_says_genuinely_explore():
    fake = FakeJev({"faith": 0.9, "hope": 0.5, "family": 0.1})
    jev.themes(fake, "text", DATA)
    assert "genuinely explore" in fake.calls[0]["questions"]["faith"].instructions


def test_record_appends_with_thresholds(tmp_path):
    log = tmp_path / "decisions.json"
    log.write_text("[]")
    by = nodes_by_key()
    d = jev.place(FakeJev({"place": choice({"arts": 0.9, "religion": 0.1}, 0.8)}),
                  by["saints"], [by["arts"], by["religion"]], DATA)
    jev.record(d, log)
    jev.record(d, log)
    entries = json.loads(log.read_text())
    assert len(entries) == 2 and entries[0]["id"] != entries[1]["id"]
    assert entries[0]["thresholds"]["choice_probability"] == jev.CHOICE_PROBABILITY
    assert entries[0]["probabilities"] == {"arts": 0.9, "religion": 0.1}


def test_cli_api_error_exit_1_no_key_in_output(monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-very-secret-key")

    class Boom:
        def system_one(self, *a, **k):
            raise jev.TypeSafeError("upstream failure")

    code = jev.main(["place", "music", "arts", "religion"], client=Boom(), data=DATA)
    out, err = capsys.readouterr()
    assert code == 1 and "upstream failure" in err
    assert "ts-very-secret-key" not in out + err


def test_beam_leaf_decision_carries_the_answer():
    fake = FakeJev(
        {"level": choice({"arts": 0.52, "religion": 0.48}, 0.05)},
        {"level": choice({"film": 0.5, "music": 0.5}, 0.0)},
        {"level": choice({"saints": 1.0}, 1.0)},
    )
    d = jev.classify(fake, "The life of a saint.", DATA)
    assert d.choice == "saints" and d.probabilities == {"saints": 1.0}


def test_cli_classify_record_and_unexpected_errors(monkeypatch, tmp_path, capsys):
    log = tmp_path / "log.json"
    log.write_text("[]")
    monkeypatch.setattr(jev, "LOG", log)
    text = tmp_path / "a.txt"
    text.write_text("A film.")
    fake = FakeJev({"level": choice({"arts": 0.95, "religion": 0.05}, 0.9)},
                   {"level": choice({"film": 0.95, "music": 0.05}, 0.9)},
                   {"level": choice({"feature-films": 0.95, "short-films": 0.05}, 0.9)})
    assert jev.main(["classify", str(text), "--record"], client=fake, data=DATA) == 0
    assert json.loads(log.read_text())[0]["choice"] == "feature-films"

    class Weird:
        def system_one(self, *a, **k):
            raise RuntimeError("unexpected")

    assert jev.main(["classify", str(text)], client=Weird(), data=DATA) == 1
    assert "unexpected" in capsys.readouterr().err


def test_record_keeps_every_policy_constant(tmp_path):
    log = tmp_path / "decisions.json"
    log.write_text("[]")
    d = jev.Decision("classify", "x", {}, {"film": 0.9}, 0.9, "film", "applied")
    t = jev.record(d, log)["thresholds"]
    assert t == {"choice_probability": jev.CHOICE_PROBABILITY, "choice_confidence": jev.CHOICE_CONFIDENCE,
                 "theme_apply": jev.THEME_APPLY, "theme_ask": jev.THEME_ASK, "beam_tie": jev.BEAM_TIE}


def test_cli_themes_record_outcome_from_the_answers(monkeypatch, tmp_path):
    log = tmp_path / "log.json"
    log.write_text("[]")
    monkeypatch.setattr(jev, "LOG", log)
    text = tmp_path / "a.txt"
    text.write_text("A film.")
    assert jev.main(["themes", str(text), "--record"], client=FakeJev({"faith": 0.5, "hope": 0.1, "family": 0.1}),
                    data=DATA) == 0
    assert jev.main(["themes", str(text), "--record"], client=FakeJev({"faith": 0.1, "hope": 0.1, "family": 0.1}),
                    data=DATA) == 0
    first, second = json.loads(log.read_text())
    assert first["outcome"] == "author" and first["choice"] == "" and first["confidence"] is None
    assert second["outcome"] == "no"


# --- type: which item type (instance of) the article's subject is ---

TYPES = {"Q13367": "film", "Q48444": "Christmas tradition", "Q48432": "musical work/composition"}


def test_type_offers_the_candidate_items_by_label():
    fake = FakeJev({"type": choice({"Q13367": 0.05, "Q48444": 0.9, "Q48432": 0.05}, 0.8)})
    d = jev.instance_type(fake, "Easter bread is baked for Easter.", TYPES)
    q = fake.calls[0]["questions"]["type"]
    assert set(q.criteria) == set(TYPES)
    assert q.criteria["Q48444"]["name"] == "Christmas tradition"
    assert fake.calls[0]["state"]["article"] == "Easter bread is baked for Easter."
    assert (d.question, d.choice, d.outcome) == ("type", "Q48444", "applied")


@pytest.mark.parametrize("p, conf, outcome", [(0.85, 0.7, "applied"), (0.85, 0.5, "author"), (0.7, 0.9, "author")])
def test_type_policy_applied_vs_author(p, conf, outcome):
    rest = round((1 - p) / 2, 3)
    fake = FakeJev({"type": choice({"Q13367": p, "Q48444": rest, "Q48432": rest}, conf)})
    assert jev.instance_type(fake, "text", TYPES).outcome == outcome


def test_type_cli_uses_the_given_candidates_and_their_labels(tmp_path, capsys):
    f = tmp_path / "a.wiki"
    f.write_text("A film.", encoding="utf-8")
    fake = FakeJev({"type": choice({"Q13367": 0.95, "Q48444": 0.05}, 0.9)})
    rc = jev.main(["type", str(f), "--candidate", "Q13367", "--candidate", "Q48444"], client=fake, data=DATA,
                  labels=lambda qids: {q: TYPES[q] for q in qids})
    assert rc == 0
    assert set(fake.calls[0]["questions"]["type"].criteria) == {"Q13367", "Q48444"}
    assert "type : Q13367 (applied" in capsys.readouterr().out
