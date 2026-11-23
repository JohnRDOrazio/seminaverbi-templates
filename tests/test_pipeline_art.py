from pipeline import load_config
from pipeline.domains import DOMAINS, art
from pipeline.queue import Row

CFG = {"sitelink_floor": 15}


def arow(sources, count, instance_of=("Q3305213",), calibration=False, **data):
    return Row("Q1", "art", sources, {}, {}, calibration,
               data={0: {"count": count, "instance_of": list(instance_of), **data}})


def test_registry_has_art():
    assert DOMAINS["art"] is art


def test_art_floor_and_the_list_source():
    assert art.triage(arow(["depicts"], 14), CFG) == ("drop", "14 sitelinks, under the floor of 15")
    assert art.triage(arow(["depicts"], 15), CFG)[0] == "pass"
    assert art.triage(arow(["list"], 2), CFG)[0] == "pass"
    assert art.triage(arow(["list", "depicts"], 2), CFG)[0] == "pass"


def test_copies_and_people_are_dropped():
    assert art.triage(arow(["depicts"], 40, ["Q5"]), CFG) == ("drop", "a person, not a work")
    assert art.triage(arow(["depicts"], 40, ["Q1784021"]), CFG) == (
        "drop", "a copy or study, not a work of its own")
    assert art.triage(arow(["depicts"], 40, ["Q1784021", "Q3305213"]), CFG)[0] == "pass"  # a work class stays a work
    assert art.triage(arow(["depicts"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")


def test_calibration_always_passes():
    assert art.triage(arow(["calibration"], 1, ["Q5"], calibration=True, folded_into="Q9"), CFG) == (
        "pass", "calibration")


def test_significance_is_the_quick_question():
    assert "beyond depicting a religious subject" in art.QUESTIONS["plausible"]
    assert "decoration, satire or shock" in art.QUESTIONS["connections"]["entertainment"]
    assert set(art.QUESTIONS["connections"]) == {"direct", "seeds", "entertainment", "none"}


def test_classes_and_owns():
    assert art.WORK_CLASSES[:8] == ("Q3305213", "Q860861", "Q132137", "Q15711026", "Q46686", "Q3714446",
                                    "Q21061279", "Q219423")
    assert art.STOP_CLASSES == ("Q41176", "Q16970")
    assert art.covered.__name__ == "covered_with_parts"


def test_config_block():
    cfg = load_config()["art"]
    assert cfg["sitelink_floor"] == 15
    assert cfg["scene_classes"] and cfg["works"] == [] and cfg["iconclass_prefixes"] == ["7", "11"]


# --- stage 0: candidates ---

import pytest

from pipeline.domains import common
from pipeline.queue import Queue
from test_pipeline_film import FakeSparql, item


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CLOSURE_CACHE", tmp_path / "closure.json")
    return Queue(tmp_path / "q.db")


def test_each_art_source_tags_its_rows(q):
    sparql = FakeSparql({"wdt:P180": [item("Q1", "A", 30, "Q3305213")], "wdt:P1257": [item("Q2", "B", 30, "Q3305213")],
                         "wdt:P136 ?genre": [item("Q3", "C", 30, "Q3305213")]})
    art.candidates(q, sparql, None, load_config())
    assert {q.row(x).sources[0] for x in ("Q1", "Q2", "Q3")} == {"depicts", "iconclass", "genre"}
    assert q.row("Q1").sources == ["depicts"] and q.row("Q2").sources == ["iconclass"]
    assert q.row("Q1").domain == "art"


def test_scenes_are_queried_as_depicted_not_as_classes(q):
    sparql = FakeSparql()
    art.candidates(q, sparql, None, load_config())
    depicts = [x for x in sparql.queries if "wdt:P180" in x]
    assert depicts and any("wd:Q51628" in x and "wd:Q302" in x for x in depicts)
    assert not any("wd:Q51628" in x for x in sparql.queries if "wdt:P180" not in x and "?sub" not in x)
    assert any("wdt:P411" in x for x in depicts)
    assert any("wd:Q12405827" in x for x in depicts)


def test_art_queries_are_bounded_without_a_subclass_path(q):
    sparql = FakeSparql()
    art.candidates(q, sparql, None, load_config())
    assert not any("wdt:P279*" in x for x in sparql.queries if "?sub" not in x)
    assert all("LIMIT 500" in x for x in sparql.queries if "wikibase:sitelinks" in x)
    assert any('STRSTARTS(STR(?ic), "7")' in x and 'STRSTARTS(STR(?ic), "11")' in x for x in sparql.queries)


def test_fold_stops_at_a_building(q):
    # the fresco cycle Q1 is part of (P361) the chapel Q2, an instance of church building (Q16970):
    # the fold query excludes buildings, so Q1 stays the candidate and Q2 is never queued
    sparql = FakeSparql({"wdt:P180": [item("Q1", "Cycle", 30, "Q219423")]})
    art.candidates(q, sparql, None, load_config())
    folds = [x for x in sparql.queries if "wdt:P361" in x]
    assert folds and all("FILTER NOT EXISTS" in x and "wd:Q16970" in x and "wd:Q41176" in x for x in folds)
    assert q.row("Q1") is not None and q.row("Q2") is None


def test_a_panel_folds_into_its_altarpiece(q):
    sparql = FakeSparql({"wdt:P180": [item("Q10", "Panel", 30, "Q3305213")],
                         "wdt:P361": [{"item": "Q10", "whole": "Q1"}],
                         "VALUES ?item { wd:Q1 }": [item("Q1", "Altar", 40, "Q15711026")]})
    art.candidates(q, sparql, None, load_config())
    assert q.row("Q1").sources == ["depicts"] and set(q.row("Q1").data[0]["parts"]) == {"Q10"}


def test_listed_works_are_queued_by_qid(q):
    cfg = load_config()
    cfg["art"]["works"] = [{"qid": "Q7", "name": "x"}]
    sparql = FakeSparql({"wdt:P361": [], "VALUES ?item { wd:Q7 }": [item("Q7", "Listed", 3, "Q5555")]})
    art.candidates(q, sparql, None, cfg)
    assert q.row("Q7").sources == ["list"]
    listed = [x for x in sparql.queries if "VALUES ?item { wd:Q7 }" in x and "wikibase:sitelinks" in x]
    assert listed and all("wdt:P31" not in x.split("OPTIONAL")[0] and "LIMIT 500" in x for x in listed)


def test_a_listed_person_is_reported_and_skipped(q, capsys):
    cfg = load_config()
    cfg["art"]["works"] = [{"qid": "Q7", "name": "x"}]
    art.candidates(q, FakeSparql({"wdt:P361": [], "VALUES ?item { wd:Q7 }": [item("Q7", "Man", 90, "Q5")]}), None, cfg)
    assert q.row("Q7") is None and "person" in capsys.readouterr().out


def test_art_people_and_other_domains_are_left(q, capsys):
    q.add("Q8", "literature", "author", {}, {})
    sparql = FakeSparql({"wdt:P180": [item("Q8", "Book", 90, "Q3305213"), item("Q9", "Man", 90, "Q5")]})
    art.candidates(q, sparql, None, load_config())
    assert q.row("Q8").domain == "literature" and q.row("Q9") is None
    assert "person" in capsys.readouterr().out
