import pytest

from pipeline import load_config
from pipeline.domains import architecture, common
from pipeline.queue import Queue, Row
from test_pipeline_film import FakeSparql, item

CFG = {"sitelink_floor": 15}
PROOF_CLASSES = ("Q163687", "Q108325", "Q160742", "Q44613")


# --- triage ---

def arow(sources, count, instance_of=("Q16970",), calibration=False, **data):
    return Row("Q1", "architecture", sources, {}, {}, calibration,
               data={0: {"count": count, "instance_of": list(instance_of), **data}})


def test_the_floor():
    assert architecture.triage(arow(["building"], 14), CFG) == ("drop", "14 sitelinks, under the floor of 15")
    assert architecture.triage(arow(["building"], 15), CFG) == ("pass", "")


@pytest.mark.parametrize("sources", [["unesco"], ["list"], ["building", "unesco"], ["building", "list"]])
def test_unesco_and_listed_buildings_pass_below_the_floor(sources):
    assert architecture.triage(arow(sources, 3), CFG) == ("pass", "")


def test_a_kept_press_mention_waives_the_floor_at_the_threshold():
    assert architecture.triage(arow(["press:sir"], 3, press={"u": 0.6}), CFG) == ("pass", "")
    assert architecture.triage(arow(["press:sir"], 3, press={"u": 0.55}), CFG) == (
        "drop", "3 sitelinks, under the floor of 15")


@pytest.mark.parametrize("sources", [["building"], ["unesco"], ["list"]])
def test_a_person_is_dropped_even_when_the_item_is_also_a_building(sources):
    assert architecture.triage(arow(sources, 90, ["Q5", "Q16970"]), CFG) == ("drop", "a person, not a work")


def test_a_museum_or_ruin_that_is_also_a_church_passes():
    assert architecture.triage(arow(["building"], 30, ["Q33506", "Q109607", "Q16970"]), CFG) == ("pass", "")


def test_calibration_always_passes():
    assert architecture.triage(arow(["calibration"], 1, ["Q5"], calibration=True), CFG) == ("pass", "calibration")


# --- classes ---

def test_owns(tmp_path):
    cache = tmp_path / "closure.json"
    sparql = FakeSparql({"wdt:P279* wd:Q163687": [{"sub": "Q163687"}, {"sub": "Q120560"}]})
    assert architecture.owns(["Q163687"], sparql, cache)
    assert architecture.owns(["Q120560"], sparql, cache)  # a subclass
    assert not architecture.owns(["Q41176"], sparql, cache)


def test_classes_questions_and_press():
    assert architecture.WORK_CLASSES == ("Q16970", "Q2977", "Q163687", "Q108325", "Q160742", "Q44613")
    assert architecture.CHRISTIAN_BY_CLASS == ("Q16970", "Q2977")
    assert "an ordinary parish church, however old, does not" in architecture.QUESTIONS["plausible"]
    assert set(architecture.QUESTIONS["connections"]) == {"direct", "seeds", "entertainment", "none"}
    assert architecture.press_stage0("Q1", {"claims": {"P527": []}}, None) == {}


def test_config_block():
    cfg = load_config()["architecture"]
    assert cfg["sitelink_floor"] == 15 and cfg["unesco"] == ["Q9259", "Q43113623"]
    assert cfg["works"] == [{"qid": "Q48435", "name": "Sagrada Família"}] and cfg["benchmark"] == []
    religions = cfg["christian_religions"]
    assert len(religions) == len(set(religions))
    assert {"Q5043", "Q1841", "Q3333484", "Q75809", "Q6423963"} <= set(religions)
    assert not {"Q432", "Q483654", "Q9268", "Q748", "Q46825", "Q66085482"} & set(religions)


# --- stage 0: candidates ---

@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CLOSURE_CACHE", tmp_path / "closure.json")
    return Queue(tmp_path / "q.db")


def config(**block):
    cfg = load_config()
    cfg["architecture"].update(block)
    return cfg


def stage0(q, sparql, **block):
    return architecture.candidates(q, sparql, None, config(**block))


def asked(sparql):
    """The class-bounded queries, as (building, unesco)."""
    bounded = [x for x in sparql.queries if "ORDER BY DESC(?sitelinks)" in x]
    return [x for x in bounded if "wdt:P1435" not in x], [x for x in bounded if "wdt:P1435" in x]


def of_class(queries, cls):
    return [x for x in queries if f"?cls IN (wd:{cls})" in x]


def test_only_the_four_classes_need_a_statement_of_the_building(q):
    sparql = FakeSparql()
    stage0(q, sparql)
    for queries in asked(sparql):
        for cls in architecture.CHRISTIAN_BY_CLASS:
            assert of_class(queries, cls)
            assert not any("P825" in x for x in of_class(queries, cls))
        for cls in PROOF_CLASSES:
            assert of_class(queries, cls)
            assert all("FILTER(BOUND(?religion) || EXISTS { ?item wdt:P825 [] })" in x for x in of_class(queries, cls))


def test_every_query_leaves_out_a_stated_other_religion(q):
    sparql = FakeSparql()
    stage0(q, sparql, christian_religions=["Q5043", "Q1841"])
    excluded = ("  OPTIONAL { ?item wdt:P140 ?religion }\n"
                "  FILTER(!BOUND(?religion) || ?religion IN (wd:Q1841, wd:Q5043))\n")
    building, unesco = asked(sparql)
    assert building and unesco and all(excluded in x for x in building + unesco)


def test_building_queries_carry_the_floor(q):
    sparql = FakeSparql()
    stage0(q, sparql, sitelink_floor=12)
    building, _ = asked(sparql)
    assert all("FILTER(?sitelinks >= " in x and "LIMIT 500" in x for x in building)
    assert all("FILTER(?sitelinks >= 12)" in x for cls in PROOF_CLASSES for x in of_class(building, cls))


def test_churches_and_cathedrals_are_asked_in_two_bands_that_meet(q):
    sparql = FakeSparql()
    stage0(q, sparql)
    building, unesco = asked(sparql)
    for cls in architecture.CHRISTIAN_BY_CLASS:
        low, high = of_class(building, cls)
        assert "FILTER(?sitelinks >= 15)" in low and "FILTER(?sitelinks < 20)" in low
        assert "FILTER(?sitelinks >= 20)" in high and "?sitelinks <" not in high
    assert all(len(of_class(building, cls)) == 1 for cls in PROOF_CLASSES)


def test_a_floor_above_the_band_leaves_one_query_per_class(q):
    sparql = FakeSparql()
    stage0(q, sparql, sitelink_floor=20)
    building, _ = asked(sparql)
    assert len(building) == 6 and all("FILTER(?sitelinks >= 20)" in x and "?sitelinks <" not in x for x in building)


def test_unesco_queries_have_both_designations_and_no_floor(q):
    sparql = FakeSparql()
    stage0(q, sparql)
    _, unesco = asked(sparql)
    assert all("wd:Q9259" in x and "wd:Q43113623" in x and "FILTER(?sitelinks >= 15)" not in x for x in unesco)
    assert all(len(of_class(unesco, c)) == 1 and "FILTER(?sitelinks >= 1)" in of_class(unesco, c)[0]
               for c in PROOF_CLASSES)
    for cls in architecture.CHRISTIAN_BY_CLASS:
        low, high = of_class(unesco, cls)
        assert "FILTER(?sitelinks >= 1)" in low and "FILTER(?sitelinks < 5)" in low
        assert "FILTER(?sitelinks >= 5)" in high and "?sitelinks <" not in high


def test_a_nested_work_class_is_asked_by_its_own_query_only(q):
    # on Wikidata chapel and basilica are subclasses of church building: asked as churches they would need no proof
    sparql = FakeSparql({
        "wdt:P279* wd:Q16970": [{"sub": s} for s in ("Q16970", "Q2977", "Q108325", "Q746310", "Q1457501")],
        "wdt:P279* wd:Q108325": [{"sub": s} for s in ("Q108325", "Q1457501")],
        "wdt:P279* wd:Q44613": [{"sub": s} for s in ("Q44613", "Q160742")]})
    stage0(q, sparql)
    for queries in asked(sparql):
        assert any("wd:Q746310" in x for x in queries)  # a stave church stays a church
        assert not any("wd:Q16970" in x and ("wd:Q108325" in x or "wd:Q1457501" in x) for x in queries)
        assert any("?cls IN (wd:Q108325, wd:Q1457501)" in x for x in queries)
        assert of_class(queries, "Q44613") and of_class(queries, "Q160742")


def test_church_buildings_are_asked_apart_from_their_subclasses(q):
    sparql = FakeSparql({"wdt:P279* wd:Q16970": [{"sub": s} for s in ("Q16970", "Q317557", "Q746310")]})
    stage0(q, sparql)
    building, _ = asked(sparql)
    churches = [x for x in building if "wd:Q16970" in x]
    assert ["VALUES ?kind { wd:Q16970 }" in x for x in churches] == [True, True, False, False]
    assert all("VALUES ?kind { wd:Q317557 wd:Q746310 }" in x for x in churches[2:])


def test_works_are_one_query_per_chunk_without_class_or_religion(q):
    sparql = FakeSparql()
    stage0(q, sparql, works=[{"qid": f"Q{n}", "name": "x"} for n in range(1000, 1060)])
    listed = [x for x in sparql.queries if "VALUES ?item" in x]
    assert len(listed) == 2 and "wd:Q1049 }" in listed[0] and "VALUES ?item { wd:Q1050 " in listed[1]
    assert not any("P140" in x or "P825" in x or "?cls" in x or "FILTER(?sitelinks" in x for x in listed)


def test_a_person_is_reported_and_skipped(q, capsys):
    sparql = FakeSparql({"wdt:P1435": [item("Q8", "Woman", 90, "Q5 Q16970")],
                         "VALUES ?item { wd:Q7 }": [item("Q7", "Man", 90, "Q5")],
                         "ORDER BY DESC(?sitelinks)": [item("Q9", "Man", 90, "Q5 Q16970")]})
    assert stage0(q, sparql, works=[{"qid": "Q7", "name": "x"}]) == 0
    assert q.row("Q7") is None and q.row("Q8") is None and q.row("Q9") is None
    out = capsys.readouterr().out
    assert all(f"architecture candidates: {qid} is a person, skipped" in out for qid in ("Q7", "Q8", "Q9"))


def test_each_source_tags_its_rows(q):
    sparql = FakeSparql({"wdt:P1435": [item("Q2", "B", 4, "Q16970"), item("Q1", "A", 30, "Q16970")],
                         "VALUES ?item { wd:Q3 }": [item("Q3", "C", 3, "Q32815")],
                         "ORDER BY DESC(?sitelinks)": [item("Q1", "A", 30, "Q16970")]})
    assert stage0(q, sparql, works=[{"qid": "Q3", "name": "C"}]) == 3
    assert q.row("Q1").sources == ["building", "unesco"]
    assert q.row("Q2").sources == ["unesco"] and q.row("Q3").sources == ["list"]
    row = q.row("Q1")
    assert row.domain == "architecture" and row.calibration is False
    assert row.labels == {"en": "A"} and row.sitelinks == {"enwiki": "A"}
    assert row.data[0] == {"count": 30, "instance_of": ["Q16970"]}


def test_a_building_queued_under_art_is_left_unchanged(q, capsys):
    q.add("Q1", "art", "depicts", {"en": "Old"}, {"enwiki": "Old"}, data={"count": 20})
    sparql = FakeSparql({"ORDER BY DESC(?sitelinks)": [item("Q1", "New", 30, "Q108325")],
                         "VALUES ?item": [item("Q1", "New", 30, "Q108325")]})
    assert stage0(q, sparql, works=[{"qid": "Q1", "name": "x"}]) == 0
    row = q.row("Q1")
    assert row.domain == "art" and row.sources == ["depicts"] and row.data[0] == {"count": 20}
    assert row.labels == {"en": "Old"}
    assert "Q1 is already a art candidate, skipped" in capsys.readouterr().out


class Triples:
    """Answers a class-bounded query from items {qid: religions} as its religion test reads: an item is kept when
    one of its P140 values is in the list, or it has none."""

    def __init__(self, religions):
        self.religions = religions

    def select(self, query):
        if "ORDER BY DESC(?sitelinks)" not in query:
            return []
        test = query.split("|| ?religion IN (")[1].split(")")[0]
        taken = {x.strip().removeprefix("wd:") for x in test.split(",")}
        return [item(qid, qid, 30, "Q16970") for qid, has in self.religions.items()
                if not has or taken & set(has)]


def test_a_converted_church_with_two_religions_is_added(q):
    stage0(q, Triples({"Q1": ["Q432", "Q1841"], "Q2": ["Q432"], "Q3": []}))
    assert q.row("Q1").sources == ["building", "unesco"]
    assert q.row("Q2") is None and q.row("Q3") is not None


class FailingOn(FakeSparql):
    def __init__(self, answers, marker):
        super().__init__(answers)
        self.marker = marker

    def select(self, query):
        if self.marker in query:
            raise RuntimeError("timeout")
        return super().select(query)


def test_the_benchmark_is_held_even_when_a_later_source_fails(q):
    sparql = FailingOn({"ORDER BY DESC(?sitelinks)": [item("Q1", "A", 30, "Q16970")]}, "wdt:P1435")
    with pytest.raises(RuntimeError, match="timeout"):
        stage0(q, sparql, benchmark=[{"qid": "Q1", "name": "x", "kind": "strong"}])
    row = q.row("Q1")
    assert row.calibration and row.data[0]["benchmark"] and row.data[0]["benchmark_kind"] == "strong"
