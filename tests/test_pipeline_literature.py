import pytest

from pipeline.domains import DOMAINS, common, literature
from pipeline.queue import Queue, Row
from test_pipeline_film import FakeSparql, item

CFG = {"sitelink_floor": 15}


def lrow(sources, count, instance_of=("Q7725634",), folded_into=None, qid="Q1", calibration=False):
    data = {"count": count, "instance_of": list(instance_of)}
    if folded_into:
        data["folded_into"] = folded_into
    return Row(qid, "literature", sources, {}, {}, calibration, data={0: data})


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


def test_registry_has_literature():
    assert DOMAINS["literature"] is literature


def test_editions_are_dropped():
    assert literature.triage(lrow(["author"], 40, ["Q3331189"]), CFG) == (
        "drop", "an edition or translation, not a work of its own")


def test_floor_applies_to_author_works_and_facets():
    assert literature.triage(lrow(["author"], 2), CFG) == ("drop", "2 sitelinks, under the floor of 15")
    assert literature.triage(lrow(["facet"], 14), CFG)[0] == "drop"
    assert literature.triage(lrow(["illustrissimi:author"], 40), CFG)[0] == "pass"
    assert literature.triage(lrow(["illustrissimi:author"], 3), CFG)[0] == "drop"


def test_works_a_source_names_directly_skip_the_floor():
    assert literature.triage(lrow(["illustrissimi", "facet"], 1), CFG)[0] == "pass"
    assert literature.triage(lrow(["francis"], 0), CFG)[0] == "pass"


def test_a_folded_volume_is_dropped():
    assert literature.triage(lrow(["author"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")


def test_calibration_rows_always_pass():
    assert literature.triage(lrow(["calibration"], 1, ["Q3331189"], calibration=True), CFG) == (
        "pass", "calibration")


def test_domain_config_overrides_only_shared_keys():
    cfg = {"sitelink_floor": 15, "literature": {"sitelink_floor": 8, "authors": []}}
    merged = common.domain_config(cfg, "literature")
    assert merged["sitelink_floor"] == 8 and "authors" not in merged
    assert merged["literature"]["authors"] == [] and cfg["sitelink_floor"] == 15
    assert common.domain_config({"sitelink_floor": 15}, "film") == {"sitelink_floor": 15}


def test_calibration_skips_people(q, capsys):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            return [item("Q1", types="Q7725634")] if "wd:Q7725634 ." in query else []

    assert literature.add_calibration(q, Sparql(), ["Q1", "Q5"]) == 1
    assert "literature calibration: Q5 skipped (not a work)" in capsys.readouterr().out
    r = q.row("Q1")
    assert r.calibration and r.sources == ["calibration"] and "series" not in r.data[0]


def test_calibration_marks_series(q):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            return [item("Q7", types="Q1667921")] if "wd:Q1667921" in query else []

    assert literature.add_calibration(q, Sparql(), ["Q7"]) == 1
    assert q.row("Q7").data[0]["series"] is True


def test_questions_have_the_three_keys():
    assert set(literature.QUESTIONS) == {"plausible", "connection", "connections"}


def test_unfinished_stages_raise():
    with pytest.raises(NotImplementedError):
        literature.candidates(None, None, None, {})
