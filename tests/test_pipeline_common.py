import json
from datetime import date, timedelta

import pytest

from pipeline.domains import common, film
from pipeline.queue import Queue
from test_pipeline_film import FakeSparql


def test_film_keeps_its_old_names():
    assert film._query is common.query and film._bounded is common.bounded
    assert film._wikidata_qid is common.wikidata_qid and film._qids is common.qids


def test_field_keys_collects_descendants():
    tree = {"root": {"names": {}}, "fields": [{"key": "arts", "names": {}, "children": [
        {"key": "literature", "names": {}, "children": [
            {"key": "novels", "names": {}}, {"key": "poetry", "names": {}}]},
        {"key": "film", "names": {}, "children": [{"key": "feature-films", "names": {}}]}]}],
        "themes_root": {"names": {}}, "themes": []}
    assert common.field_keys(tree, {"literature"}) == {"literature", "novels", "poetry"}


def test_run_query_names_the_domain(capsys):
    class Boom:
        def select(self, q):
            raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        common.run_query(Boom(), "literature", "facet", "Q")
    assert "literature candidates: facet failed" in capsys.readouterr().out


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


def test_closure_is_fetched_once_and_cached(tmp_path):
    sparql = FakeSparql({"wd:Q3305213": [{"sub": "Q3305213"}, {"sub": "Q134194"}]})
    cache = tmp_path / "closure.json"
    assert common.class_closure(sparql, ["Q3305213"], cache) == {"Q3305213", "Q134194"}
    assert common.class_closure(sparql, ["Q3305213"], cache) == {"Q3305213", "Q134194"}
    assert len(sparql.queries) == 1


def test_corrupt_cache_is_refetched(tmp_path):
    cache = tmp_path / "closure.json"
    cache.write_text("{not json")
    sparql = FakeSparql({"wd:Q7366": [{"sub": "Q7366"}]})
    assert common.class_closure(sparql, ["Q7366"], cache) == {"Q7366"}


def test_closure_fetch_failure_raises(tmp_path):
    with pytest.raises(RuntimeError):
        common.class_closure(FakeSparql(error=RuntimeError("429")), ["Q7366"], tmp_path / "c.json")


def test_owns_reads_the_closure(tmp_path):
    sparql = FakeSparql({"wd:Q3305213": [{"sub": "Q3305213"}, {"sub": "Q134194"}]})
    assert common.owns(["Q134194"], ["Q3305213"], sparql, tmp_path / "c.json")
    assert not common.owns(["Q5"], ["Q3305213"], sparql, tmp_path / "c.json")


def test_other_domain_row_is_reported_and_skipped(q, capsys):
    q.add("Q1", "literature", "facet", {}, {})
    assert common.other_domain(q, "Q1", "music") == "literature"
    assert "already a literature candidate" in capsys.readouterr().out
    assert common.other_domain(q, "Q1", "literature") is None
    assert common.other_domain(q, "Q9", "music") is None


# --- fold_parts: parts folded into their whole (P361) ---

from types import SimpleNamespace  # noqa: E402


@pytest.fixture(autouse=False)
def no_closure_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CLOSURE_CACHE", tmp_path / "closure.json")


def part_row(qid, n):
    return {"item": qid, "n": str(n), "types": "Q1", "l_en": qid, "s_en": f"https://en.wikipedia.org/wiki/{qid}"}


whole_row = part_row


def test_part_folds_into_its_whole(q, no_closure_cache):
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    q.add("Q10", "music", "form", {}, {})
    added = common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"])
    row = q.row("Q1")
    assert added == 1 and q.row("Q10").data[0]["folded_into"] == "Q1"
    assert row.domain == "music" and "form" in row.sources
    assert set(row.data[0]["parts"]) == {"Q10"} and row.data[0]["count"] == 40
    assert row.data[0]["parts"]["Q10"]["count"] == 25


def test_unqueued_part_is_not_added_when_it_folds(q, no_closure_cache):
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"])
    assert q.row("Q10") is None and q.row("Q1") is not None


def test_whole_count_is_the_highest_of_its_parts(q, no_closure_cache):
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 5)]})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"])
    assert q.row("Q1").data[0]["count"] == 25


def test_part_with_two_wholes_stays_itself(q, capsys, no_closure_cache):
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}, {"item": "Q10", "whole": "Q2"}]})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "depicts", "art", ["Q15711026"])
    assert q.row("Q10") is not None and q.row("Q1") is None and "2 wholes" in capsys.readouterr().out


def test_part_with_no_whole_is_added_as_itself(q, no_closure_cache):
    assert common.fold_parts(q, FakeSparql(), [part_row("Q10", 25)], "form", "music", ["Q105543609"]) == 1
    assert q.row("Q10").domain == "music"


def test_closed_whole_absorbs_nothing(q, no_closure_cache):
    q.add("Q1", "music", "form", {}, {})
    q.set_status("Q1", "declined", reason="x")
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    assert common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"]) == 0
    assert q.row("Q10") is None


def test_stop_classes_are_excluded_in_the_query(q, no_closure_cache):
    sparql = FakeSparql()
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q1"], ["Q2"])
    assert any("NOT EXISTS" in s and "wd:Q2" in s for s in sparql.queries)


def row_with_parts(parts):
    return SimpleNamespace(qid="Q1", sitelinks={}, labels={}, calibration=False, data={0: {"parts": parts}})


def test_whole_is_a_translation_suggestion_when_a_part_is_covered():
    from test_pipeline_stages import FakeRepo, wikis as make_wikis
    repo = FakeRepo({("itwiki", "Kyrie"): "Q9"}, {"Q9": {"it": "Kyrie"}})
    wikis = make_wikis()
    outcome, data = common.covered_with_parts(
        row_with_parts({"Q10": {"sitelinks": {"itwiki": "Kyrie"}, "count": 5}}), repo, wikis)
    assert outcome == "translation" and data["have"] == ["it"] and data["note"].startswith("covered through")


def _write_closure_cache(cache, cls, date_text, subclasses):
    cache.write_text(json.dumps({cls: {"date": date_text, "subclasses": subclasses}}), encoding="utf-8")


def test_cache_entry_with_non_string_subclass_is_refetched(tmp_path):
    cache = tmp_path / "closure.json"
    _write_closure_cache(cache, "Q7366", date.today().isoformat(), [[]])
    sparql = FakeSparql({"wd:Q7366": [{"sub": "Q7366"}]})
    assert common.class_closure(sparql, ["Q7366"], cache) == {"Q7366"}
    assert len(sparql.queries) == 1


def test_future_dated_cache_entry_is_stale(tmp_path):
    cache = tmp_path / "closure.json"
    _write_closure_cache(cache, "Q7366", (date.today() + timedelta(days=5)).isoformat(), ["Q999"])
    sparql = FakeSparql({"wd:Q7366": [{"sub": "Q7366"}]})
    assert common.class_closure(sparql, ["Q7366"], cache) == {"Q7366"}
    assert len(sparql.queries) == 1


def test_bounded_with_a_closure_lists_the_classes_instead_of_a_path():
    plain = common.bounded("  ?item wdt:P1 ?x .", "Q1")
    listed = common.bounded("  ?item wdt:P1 ?x .", "Q1", ["Q2", "Q3"])
    assert "wdt:P31/wdt:P279* wd:Q1" in plain
    assert "P279*" not in listed and "VALUES ?cls { wd:Q1 wd:Q2 wd:Q3 }" in listed and "LIMIT 500" in listed


def test_a_part_held_by_another_domain_is_reported_and_not_folded(q, no_closure_cache, capsys):
    q.add("Q8", "literature", "author", {}, {})
    sparql = FakeSparql({"wdt:P361": [{"item": "Q8", "whole": "Q1"}, {"item": "Q10", "whole": "Q1"}],
                         "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    common.fold_parts(q, sparql, [part_row("Q8", 90), part_row("Q10", 25)], "form", "music", ["Q105543609"])
    row = q.row("Q1")
    assert set(row.data[0]["parts"]) == {"Q10"} and row.data[0]["count"] == 40
    assert q.row("Q8").domain == "literature" and "Q8 is already a literature candidate" in capsys.readouterr().out
