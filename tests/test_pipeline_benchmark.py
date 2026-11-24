import pytest

from pipeline.domains import benchmark
from pipeline.queue import Queue
from test_pipeline_film import FakeSparql, item


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


def test_hold_marks_only_rows_of_its_domain(q):
    q.add("QA", "art", "depicts", {"en": "A"}, {"enwiki": "A"}, data={"count": 30})
    q.add("QB", "architecture", "depicts", {"en": "B"}, {"enwiki": "B"}, data={"count": 30})
    benchmark.hold(q, "architecture", [{"qid": "QA", "kind": "strong"}, {"qid": "QB", "kind": "weak"}])
    assert q.row("QB").calibration is True
    assert q.row("QB").data[0]["benchmark_kind"] == "weak"
    assert q.row("QA").calibration is False


def test_add_reports_under_its_domain(q, capsys):
    added = benchmark.add(q, FakeSparql({}), "architecture", ["Q9"], [], ["Q41176"])
    assert added == 0 and q.row("Q9") is None
    assert "architecture calibration: Q9 skipped (a person, or not an architecture work)" in capsys.readouterr().out


def test_add_queues_its_rows_under_its_domain(q, capsys):
    q.add("Q8", "art", "depicts", {"en": "Old"}, {"enwiki": "Old"}, data={"count": 30})
    sparql = FakeSparql({"VALUES ?item { wd:Q9 }\n  ?item wdt:P31/wdt:P279* wd:Q16970": [item("Q9", "A", 20, "Q16970")],
                         "VALUES ?item { wd:Q2 }": [item("Q2", "B", 3, "Q1440476")]})
    kinds = [{"qid": "Q2", "kind": "weak"}, {"qid": "Q8", "kind": "control"}]
    assert benchmark.add(q, sparql, "architecture", ["Q9"], kinds, ["Q16970"]) == 2
    assert [(q.row(x).domain, q.row(x).calibration) for x in ("Q9", "Q2", "Q8")] == [
        ("architecture", True), ("architecture", True), ("art", False)]
    assert q.row("Q2").data[0]["benchmark_kind"] == "weak" and "benchmark" not in q.row("Q9").data[0]
    assert "architecture candidates: Q8 is already a art candidate, skipped" in capsys.readouterr().out
