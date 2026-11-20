import pytest

from pipeline import load_config
from pipeline.queue import Queue


@pytest.fixture
def q():
    return Queue(":memory:")


def test_add_is_idempotent_and_merges_sources(q):
    assert q.add("Q1", "film", "vatican", {"en": "A"}, {"enwiki": "A"}) is True
    assert q.add("Q1", "film", "facet", {}, {}) is False
    assert q.pending(1)[0].sources == ["vatican", "facet"]


def test_pending_follows_outcomes(q):
    q.add("Q1", "film", "s", {}, {}); q.add("Q2", "film", "s", {}, {})
    q.set_outcome("Q1", 1, "pass"); q.set_outcome("Q2", 1, "drop")
    assert [r.qid for r in q.pending(2)] == ["Q1"]
    assert q.pending(1) == []


def test_redo_clears_the_stage_and_later(q):
    q.add("Q1", "film", "s", {}, {})
    for s in (1, 2, 3): q.set_outcome("Q1", s, "pass")
    q.redo(2, "Q1")
    assert [r.qid for r in q.pending(2)] == ["Q1"] and q.pending(3) == []


def test_jev_cache_survives_reopen(tmp_path):
    q = Queue(tmp_path / "p.sqlite"); q.add("Q1", "film", "s", {}, {})
    q.jev_put("Q1", "h1", "question?", {"p": 0.7})
    assert Queue(tmp_path / "p.sqlite").jev_get("Q1", "h1") == {"p": 0.7}


def test_declined_rows_are_never_pending(q):
    q.add("Q1", "film", "s", {}, {}); q.set_status("Q1", "declined", reason="off topic")
    assert q.pending(1) == []


def test_status_page_and_domain_filter_and_data(q):
    q.add("Q1", "film", "s", {}, {}); q.add("Q2", "book", "s", {}, {}, calibration=True)
    q.set_outcome("Q1", 1, "translation", {"n": 2})
    q.set_status("Q1", "approved", page="Film X")
    assert [r.qid for r in q.pending(1, domain="book")] == ["Q2"]
    row = q.by_status("approved")[0]
    assert (row.qid, row.page, row.outcomes, row.data) == ("Q1", "Film X", {1: "translation"}, {1: {"n": 2}})


def test_config_has_the_pipeline_defaults():
    c = load_config()
    assert c["vote_threshold"] == 3 and c["film"]["seeds"]["awards"] == ["Q2503453", "Q23011208"]


def test_stage_zero_data_round_trips_and_merges(q):
    q.add("Q1", "film", "a", {}, {}, data={"count": 3})
    assert q.add("Q1", "film", "b", {}, {}, data={"instance_of": ["Q11424"]}) is False
    row = q.pending(1)[0]
    assert row.data[0] == {"count": 3, "instance_of": ["Q11424"]}
    q.add("Q2", "film", "a", {}, {})
    assert 0 not in q.pending(1)[1].data


def test_repeat_add_with_calibration_sets_the_flag(q):
    q.add("Q1", "film", "facet", {}, {})
    assert q.pending(1)[0].calibration is False
    assert q.add("Q1", "film", "calibration", {}, {}, calibration=True) is False
    assert q.pending(1)[0].calibration is True
    q.add("Q1", "film", "facet", {}, {})  # a later plain add never clears it
    assert q.pending(1)[0].calibration is True


def test_votes_page_and_with_outcome(q):
    q.add("Q1", "film", "s", {}, {}); q.add("Q2", "film", "s", {}, {})
    q.set_votes("Q1", 4); q.set_page("Q1", "Semina Verbi:Article proposals/A")
    q.set_outcome("Q1", 1, "translation", {"have": ["it"]}); q.set_outcome("Q2", 1, "pass")
    r = q.with_outcome(1, "translation")
    assert [(x.qid, x.votes, x.page) for x in r] == [("Q1", 4, "Semina Verbi:Article proposals/A")]


def test_queue_without_votes_column_is_migrated(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("CREATE TABLE candidates (qid TEXT PRIMARY KEY, domain TEXT NOT NULL, sources TEXT NOT NULL,"
               " labels TEXT NOT NULL, sitelinks TEXT NOT NULL, calibration INTEGER NOT NULL,"
               " status TEXT, page TEXT, reason TEXT, added_at REAL NOT NULL)")
    db.commit(); db.close()
    q = Queue(tmp_path / "old.sqlite"); q.add("Q1", "film", "s", {}, {}); q.set_votes("Q1", 2)
    assert q.pending(1)[0].votes == 2
