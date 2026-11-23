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


def test_closure_cache_file_is_read_once_per_process(tmp_path, monkeypatch):
    """owns() is asked several times per hit: the cache file is parsed once, and kept current when it is written."""
    from pathlib import Path
    cache = tmp_path / "closure.json"
    _write_closure_cache(cache, "Q7366", date.today().isoformat(), ["Q7366", "Q1"])
    reads = []
    real = Path.read_text

    def counting(self, *a, **k):
        if self == cache:
            reads.append(self)
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", counting)
    sparql = FakeSparql({"wd:Q3305213": [{"sub": "Q3305213"}]})
    for _ in range(5):
        assert common.owns(["Q1"], ["Q7366"], sparql, cache)
    assert len(reads) == 1 and sparql.queries == []
    assert common.class_closure(sparql, ["Q3305213"], cache) == {"Q3305213"}  # fetched and written
    assert common.class_closure(sparql, ["Q3305213", "Q7366"], cache) == {"Q3305213", "Q7366", "Q1"}
    assert len(reads) == 1 and len(sparql.queries) == 1
    assert json.loads(real(cache))["Q3305213"]["subclasses"] == ["Q3305213"]


def test_bounded_with_a_closure_lists_the_classes_instead_of_a_path():
    plain = common.bounded("  ?item wdt:P1 ?x .", "Q1")
    listed = common.bounded("  ?item wdt:P1 ?x .", "Q1", ["Q2", "Q3"])
    assert "wdt:P31/wdt:P279* wd:Q1" in plain
    assert "P279*" not in listed and "FILTER(?cls IN (wd:Q1, wd:Q2, wd:Q3))" in listed and "LIMIT 500" in listed


def test_a_part_held_by_another_domain_is_reported_and_not_folded(q, no_closure_cache, capsys):
    q.add("Q8", "literature", "author", {}, {})
    sparql = FakeSparql({"wdt:P361": [{"item": "Q8", "whole": "Q1"}, {"item": "Q10", "whole": "Q1"}],
                         "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    common.fold_parts(q, sparql, [part_row("Q8", 90), part_row("Q10", 25)], "form", "music", ["Q105543609"])
    row = q.row("Q1")
    assert set(row.data[0]["parts"]) == {"Q10"} and row.data[0]["count"] == 40
    assert q.row("Q8").domain == "literature" and "Q8 is already a literature candidate" in capsys.readouterr().out


# --- a large closure is a path, not a VALUES list (the endpoint refuses a 2 MB query with HTTP 413) ---

def _closure(n):
    return [f"Q{1000 + i}" for i in range(n)]


def test_bounded_lists_a_closure_of_up_to_500_classes():
    listed = common.bounded("  ?item wdt:P1 ?x .", "Q1", _closure(499))  # 500 with Q1
    assert "?item wdt:P31 ?cls . FILTER(?cls IN (wd:Q1, wd:Q1000," in listed and "P279*" not in listed
    assert "VALUES ?cls" not in listed  # joined before the pattern, a VALUES list of classes timed out on WDQS


def test_bounded_uses_the_path_above_500_classes():
    big = common.bounded("  ?item wdt:P1 ?x .", "Q1", _closure(500))  # 501 with Q1
    assert "?cls" not in big and "wd:Q1499" not in big
    assert "?item wdt:P31/wdt:P279* wd:Q1 . hint:Prior hint:gearing \"forward\"" in big and "LIMIT 500" in big


def _fold_queries(q, closures, stop=()):
    sparql = FakeSparql({f"wd:{cls} . }}": [{"sub": s} for s in subs] for cls, subs in closures.items()})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q1"], list(stop))
    return [s for s in sparql.queries if "wdt:P361" in s]


def test_fold_lists_a_small_closure_of_wholes_and_stops(q, no_closure_cache):
    folds = _fold_queries(q, {"Q1": _closure(10), "Q2": _closure(3)}, ["Q2"])
    assert folds and all("?whole wdt:P31 ?cls . FILTER(?cls IN (wd:Q1, wd:Q1000," in s for s in folds)
    assert all("?whole wdt:P31 ?stop . FILTER(?stop IN (wd:Q1000," in s for s in folds)
    assert not any("P279*" in s for s in folds)


def test_fold_uses_the_path_for_a_large_closure_of_wholes_or_stops(q, no_closure_cache):
    folds = _fold_queries(q, {"Q1": _closure(600), "Q2": _closure(700)}, ["Q2"])
    assert folds and all("wd:Q1500" not in s for s in folds)
    assert all("?whole wdt:P31/wdt:P279* ?cls" in s and "VALUES ?cls { wd:Q1 }" in s for s in folds)
    assert all("?whole wdt:P31/wdt:P279* ?stop" in s and "VALUES ?stop { wd:Q2 }" in s for s in folds)
    assert all(s.count('hint:gearing "forward"') == 2 for s in folds)


def test_bounded_with_a_floor_filters_on_sitelinks_before_the_class():
    query = common.bounded("  ?item wdt:P1 ?x .", "Q1", ["Q2"], floor=15)
    inner = query.split("ORDER BY")[0]
    assert "?item wikibase:sitelinks ?sitelinks . FILTER(?sitelinks >= 15)" in inner
    assert inner.index("?item wdt:P1 ?x") < inner.index("FILTER(?sitelinks >= 15)") < inner.index("?item wdt:P31 ?cls")


def test_bounded_without_a_floor_is_unchanged():
    query = common.bounded("  ?item wdt:P1 ?x .", "Q1")
    assert "FILTER(?sitelinks" not in query
    assert "  ?item wdt:P31/wdt:P279* wd:Q1 .\n  ?item wikibase:sitelinks ?sitelinks .\n  } ORDER BY" in query


def test_an_open_part_that_folds_is_retired_and_reported(q, no_closure_cache, capsys):
    q.add("Q10", "music", "form", {}, {})
    q.set_outcome("Q10", 5, "pass", {})
    q.set_status("Q10", "open", page="P/Kyrie")
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"])
    part = q.row("Q10")
    assert part.data[0]["folded_into"] == "Q1" and part.data[0]["retired"] is True
    assert part.status == "open" and part.outcomes[5] == "pass"  # nothing deleted: its page is live
    out = capsys.readouterr().out
    assert "Q10 folded into Q1" in out and "P/Kyrie" in out and "by hand" in out


@pytest.mark.parametrize("status", ["approved", "created", "declined"])
def test_a_closed_part_that_folds_is_left_alone(q, no_closure_cache, status):
    q.add("Q10", "music", "form", {}, {})
    q.set_status("Q10", status)
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"])
    assert "retired" not in q.row("Q10").data[0]


def open_whole(q):
    q.add("Q1", "music", "form", {"en": "Mass"}, {"enwiki": "Mass"},
          data={"count": 40, "parts": {"Q10": {"sitelinks": {"enwiki": "Q10"}, "count": 25}}})
    for stage in range(1, 6):
        q.set_outcome("Q1", stage, "pass", {})
    q.set_status("Q1", "open", page="P/Mass")


NEW_PART = {"wdt:P361": [{"item": "Q11", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]}


def test_an_open_whole_that_gains_a_covered_part_is_retired_and_reported(q, no_closure_cache, capsys):
    open_whole(q)
    seen = []
    recheck = lambda row: (seen.append(set(row.data[0]["parts"])), ("translation", {"have": ["en"]}))[1]
    common.fold_parts(q, FakeSparql(NEW_PART), [part_row("Q11", 25)], "form", "music", ["Q105543609"],
                      recheck=recheck)
    row = q.row("Q1")
    assert seen == [{"Q10", "Q11"}]  # re-checked after the new part is recorded
    assert row.data[0]["retired"] is True and row.status == "open"
    assert row.outcomes == {s: "pass" for s in range(6)}  # nothing deleted: its page is live
    out = capsys.readouterr().out
    assert "Q1 gained parts and is now covered" in out and "P/Mass" in out and "by hand" in out


def test_an_open_whole_that_gains_an_uncovered_part_stays_open_and_unchanged(q, no_closure_cache, capsys):
    open_whole(q)
    seen = []
    common.fold_parts(q, FakeSparql(NEW_PART), [part_row("Q11", 25)], "form", "music", ["Q105543609"],
                      recheck=lambda row: (seen.append(row.qid), ("pass", {}))[1])
    row = q.row("Q1")
    assert seen == ["Q1"] and "retired" not in row.data[0] and row.status == "open"
    assert row.outcomes == {s: "pass" for s in range(6)}
    assert "covered" not in capsys.readouterr().out


@pytest.mark.parametrize("status", [None, "approved", "created", "declined"])
def test_only_an_open_whole_is_rechecked(q, no_closure_cache, status):
    open_whole(q)
    if status is None:
        q.db.execute("UPDATE candidates SET status=NULL WHERE qid='Q1'"); q.db.commit()
    else:
        q.set_status("Q1", status)
    seen = []
    common.fold_parts(q, FakeSparql(NEW_PART), [part_row("Q11", 25)], "form", "music", ["Q105543609"],
                      recheck=lambda row: (seen.append(row.qid), ("drop", {}))[1])
    assert seen == [] and "retired" not in q.row("Q1").data[0]


def test_an_open_whole_with_no_new_part_is_not_rechecked(q, no_closure_cache):
    open_whole(q)
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    seen = []
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"],
                      recheck=lambda row: (seen.append(row.qid), ("drop", {}))[1])
    assert seen == []


def failing_recheck(row):
    raise RuntimeError("wiki down")


@pytest.mark.parametrize("outcome, retired", [("translation", True), ("pass", False)])
def test_a_failed_recheck_stays_pending_until_a_later_run_succeeds(q, no_closure_cache, capsys, outcome, retired):
    open_whole(q)
    with pytest.raises(RuntimeError, match="wiki down"):
        common.fold_parts(q, FakeSparql(NEW_PART), [part_row("Q11", 25)], "form", "music", ["Q105543609"],
                          recheck=failing_recheck)
    row = q.row("Q1")
    assert set(row.data[0]["parts"]) == {"Q10", "Q11"} and row.data[0]["recheck_pending"] is True
    assert "retired" not in row.data[0]
    seen = []
    common.recheck_pending(q, "music", lambda r: (seen.append(r.qid), (outcome, {}))[1])  # no new parts this run
    row = q.row("Q1")
    assert seen == ["Q1"] and row.data[0]["recheck_pending"] is False
    assert bool(row.data[0].get("retired")) is retired and row.outcomes == {s: "pass" for s in range(6)}


def test_a_successful_recheck_clears_the_mark(q, no_closure_cache):
    open_whole(q)
    common.fold_parts(q, FakeSparql(NEW_PART), [part_row("Q11", 25)], "form", "music", ["Q105543609"],
                      recheck=lambda row: ("pass", {}))
    assert q.row("Q1").data[0]["recheck_pending"] is False


def test_only_open_unretired_wholes_of_the_domain_are_rechecked_when_pending(q, no_closure_cache):
    for qid, domain, status, extra in (("Q1", "music", "open", {}), ("Q2", "music", "open", {"retired": True}),
                                       ("Q3", "art", "open", {}), ("Q4", "music", "approved", {}),
                                       ("Q5", "music", "open", {"recheck_pending": False})):
        q.add(qid, domain, "s", {}, {}, data={"recheck_pending": True, **extra})
        q.set_status(qid, status)
    seen = []
    common.recheck_pending(q, "music", lambda r: (seen.append(r.qid), ("pass", {}))[1])
    assert seen == ["Q1"]


@pytest.mark.parametrize("domain", ["music", "art"])
def test_candidates_recheck_pending_wholes_when_covered_is_given(q, no_closure_cache, domain, monkeypatch):
    from pipeline import load_config
    from pipeline.domains import DOMAINS
    seen = []
    monkeypatch.setattr(common, "recheck_pending", lambda queue, d, recheck: seen.append((d, recheck)))
    marker = lambda row: ("pass", {})
    DOMAINS[domain].candidates(q, FakeSparql(), None, load_config(), covered=marker)
    DOMAINS[domain].candidates(q, FakeSparql(), None, load_config())
    assert seen == [(domain, marker)]
