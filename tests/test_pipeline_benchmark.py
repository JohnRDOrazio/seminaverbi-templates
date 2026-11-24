import pytest

from pipeline.domains import benchmark
from pipeline.queue import Queue
from test_pipeline_film import FakeSparql


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
