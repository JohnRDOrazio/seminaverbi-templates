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
