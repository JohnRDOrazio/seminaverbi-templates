import pytest

from pipeline.domains import common, film


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
