import pytest

from pipeline import load_config
from pipeline.domains import DOMAINS, architecture, common
from pipeline.judge import Judge
from pipeline.queue import Queue, Row
from test_pipeline_art import time_claim
from test_pipeline_film import FakeSparql, FakeWikidataEntities, entity, item, wiki_for_pages

CFG = {"sitelink_floor": 15}
PROOF_CLASSES = ("Q163687", "Q108325", "Q160742", "Q44613")  # asked in one band
CATHOLIC = ["Q9592", "Q1841"]


# --- triage ---

def arow(sources, count, instance_of=("Q16970",), calibration=False, **data):
    return Row("Q1", "architecture", sources, {}, {}, calibration,
               data={0: {"count": count, "instance_of": list(instance_of), **data}})


def test_registered_and_judged_with_its_own_wording():
    assert DOMAINS["architecture"] is architecture
    assert Judge._questions(arow(["building"], 30)) is architecture.QUESTIONS


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


@pytest.mark.parametrize("sources", [["press:sir"], ["unesco"], ["list"], ["building"]])
def test_a_building_found_not_catholic_is_dropped_whatever_its_source(sources):
    assert architecture.triage(arow(sources, 90, catholic=False, press={"u": 0.9}), CFG) == (
        "drop", "not a Catholic building")


def test_a_building_found_catholic_or_never_checked_passes():
    assert architecture.triage(arow(["press:sir"], 3, catholic=True, press={"u": 0.9}), CFG) == ("pass", "")
    assert architecture.triage(arow(["building"], 30), CFG) == ("pass", "")


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
    assert architecture.BANDED == ("Q16970", "Q2977")
    assert "an ordinary parish church, however old, does not" in architecture.QUESTIONS["plausible"]
    assert set(architecture.QUESTIONS["connections"]) == {"direct", "seeds", "entertainment", "none"}


class Dioceses(FakeSparql):
    """A FakeSparql whose diocese query answers a row when it names one of `catholic`."""

    def __init__(self, catholic=()):
        super().__init__({f"VALUES ?diocese {{ wd:{d} }}": [{"diocese": d}] for d in catholic})


@pytest.mark.parametrize("religions,expected", [(["Q9592"], True), (["Q597526"], True), (["Q64900"], True),
                                                (["Q35032", "Q1841"], True), (["Q35032"], False),
                                                (["Q82708"], False), (["Q432"], False)])
def test_press_stage0_reads_the_stated_religions(religions, expected):
    sparql = Dioceses()
    assert architecture.press_stage0("Q1", entity("B", P140=religions, P708=["Q7"]), sparql) == {"catholic": expected}
    assert sparql.queries == []  # a stated religion decides: the diocese is not asked


def test_press_stage0_asks_the_diocese_of_a_building_that_states_no_religion():
    assert architecture.press_stage0("Q1", entity("B", P708=["Q7"]), Dioceses(["Q7"])) == {"catholic": True}
    assert architecture.press_stage0("Q1", entity("B", P5607=["Q7"]), Dioceses(["Q7"])) == {"catholic": True}
    sparql = Dioceses()
    assert architecture.press_stage0("Q1", entity("B", P708=["Q8"]), sparql) == {"catholic": False}
    assert "VALUES ?diocese { wd:Q8 }" in sparql.queries[0] and "wd:Q9592" in sparql.queries[0]


def test_press_stage0_without_religion_or_diocese_is_not_catholic():
    sparql = Dioceses()
    assert architecture.press_stage0("Q1", entity("B"), sparql) == {"catholic": False}
    assert sparql.queries == []


def test_press_stage0_an_unknown_religion_is_stated_and_a_novalue_is_not():
    """As WDQS's wdt:P140 reads them: `somevalue` binds ?stated (no diocese fallback), `novalue` binds nothing."""
    for snaktype, expected, asked in (("somevalue", False, 0), ("novalue", True, 1)):
        ent = entity("B", P708=["Q7"])
        ent["claims"]["P140"] = [{"mainsnak": {"snaktype": snaktype}, "rank": "normal"}]
        sparql = Dioceses(["Q7"])
        assert architecture.press_stage0("Q1", ent, sparql) == {"catholic": expected}
        assert len(sparql.queries) == asked


def test_press_stage0_reads_the_best_rank_only():
    ent = entity("B", P140=["Q9592", "Q35032"])
    ent["claims"]["P140"][0]["rank"] = "deprecated"
    assert architecture.press_stage0("Q1", ent, Dioceses()) == {"catholic": False}
    ent["claims"]["P140"][1]["rank"] = "preferred"
    ent["claims"]["P140"][0]["rank"] = "normal"
    assert architecture.press_stage0("Q1", ent, Dioceses()) == {"catholic": False}


def test_config_block():
    cfg = load_config()["architecture"]
    assert cfg["sitelink_floor"] == 15 and cfg["unesco"] == ["Q9259", "Q43113623"]
    assert cfg["works"] == [{"qid": "Q48435", "name": "Sagrada Família"}]
    assert "christian_religions" not in cfg
    religions = cfg["catholic_religions"]
    assert len(religions) == len(set(religions)) == 30
    assert {"Q9592", "Q1841", "Q7361618", "Q597526", "Q17549077", "Q49376", "Q1546359", "Q64900", "Q472189",
            "Q64871", "Q3443559"} <= set(religions)
    # Christianity in general, the Byzantine Rite (Orthodox too), Orthodox, Protestant and other confessions
    assert not {"Q5043", "Q617075", "Q3333484", "Q35032", "Q383258", "Q683724", "Q75809", "Q6423963", "Q82708",
                "Q42504", "Q106687", "Q432"} & set(religions)


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


CATHOLIC_TEST = ("  OPTIONAL { ?item wdt:P140 ?religion . FILTER(?religion IN (wd:Q1841, wd:Q9592)) }\n"
                 "  OPTIONAL { ?item wdt:P140 ?stated }\n"
                 "  OPTIONAL { ?item wdt:P708|wdt:P5607 ?diocese . ?diocese wdt:P140 ?faith ."
                 " FILTER(?faith IN (wd:Q1841, wd:Q9592)) }\n"
                 "  FILTER(BOUND(?religion) || (!BOUND(?stated) && BOUND(?diocese)))\n")


def test_every_query_of_every_class_asks_for_a_catholic_building(q):
    """A Catholic religion among the stated ones, or none stated and a Catholic diocese; a dedication proves
    nothing."""
    sparql = FakeSparql()
    stage0(q, sparql, catholic_religions=CATHOLIC)
    building, unesco = asked(sparql)
    for queries in (building, unesco):
        for cls in architecture.WORK_CLASSES:
            assert of_class(queries, cls) and all(CATHOLIC_TEST in x for x in of_class(queries, cls))
    assert not any("P825" in x for x in building + unesco)


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
    for cls in architecture.BANDED:
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
    for cls in architecture.BANDED:
        low, high = of_class(unesco, cls)
        assert "FILTER(?sitelinks >= 1)" in low and "FILTER(?sitelinks < 5)" in low
        assert "FILTER(?sitelinks >= 5)" in high and "?sitelinks <" not in high


def test_a_nested_work_class_is_asked_by_its_own_query_only(q):
    # on Wikidata chapel and basilica are subclasses of church building: each class is asked once
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
    """Answers a class-bounded query from items {qid: (religions, diocese religion)} as its Catholic test reads."""

    def __init__(self, items):
        self.items = items

    def select(self, query):
        if "ORDER BY DESC(?sitelinks)" not in query:
            return []
        test = query.split("FILTER(?religion IN (")[1].split(")")[0]
        taken = {x.strip().removeprefix("wd:") for x in test.split(",")}
        return [item(qid, qid, 30, "Q16970") for qid, (has, diocese) in self.items.items()
                if taken & set(has) or (not has and diocese in taken)]


def test_the_catholic_test_on_stated_religions_and_dioceses(q):
    stage0(q, Triples({"Q1": (["Q432", "Q1841"], None), "Q2": (["Q432"], None), "Q3": ([], None),
                       "Q4": ([], "Q9592"), "Q5": (["Q35032"], "Q9592"), "Q6": ([], "Q35032")}))
    assert q.row("Q1").sources == ["building", "unesco"]  # a converted or shared church
    assert q.row("Q4") is not None  # no religion stated, in a Catholic diocese
    assert all(q.row(x) is None for x in ("Q2", "Q3", "Q5", "Q6"))


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


# --- retriage ---

def dropped(q, qid="Q1", sources=("building",), count=3, instance_of=("Q16970",), status=None):
    q.add(qid, "architecture", sources[0], {}, {}, False, {"count": count, "instance_of": list(instance_of)})
    for extra in sources[1:]:
        q.add(qid, "architecture", extra, {}, {}, False)
    q.set_outcome(qid, 1, "pass")
    q.set_outcome(qid, 2, "drop", {"reason": f"{count} sitelinks, under the floor of 15"})
    if status:
        q.set_status(qid, status)


def test_a_floor_drop_that_gained_unesco_is_triaged_again(q):
    dropped(q)
    q.add("Q1", "architecture", "unesco", {}, {}, False)
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 1
    assert 2 not in q.row("Q1").outcomes and q.row("Q1").outcomes[1] == "pass"


@pytest.mark.parametrize("status", ["open", "declined"])
def test_a_row_with_a_status_keeps_its_outcomes(q, status):
    dropped(q, sources=("building", "unesco"), status=status)
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 0
    assert q.row("Q1").outcomes[2] == "drop"


def test_a_person_stays_dropped(q):
    dropped(q, sources=("building", "unesco"), instance_of=("Q5", "Q16970"))
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 0
    assert q.row("Q1").outcomes[2] == "drop"


def test_a_row_still_under_the_floor_stays_dropped(q):
    dropped(q)
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 0
    assert q.row("Q1").outcomes[2] == "drop"


def test_retriage_leaves_other_domains_calibration_and_other_outcomes(q):
    dropped(q, "Q1", sources=("building", "unesco"))
    q.set_domain("Q1", "art")
    dropped(q, "Q2", sources=("building", "unesco"))
    q.set_outcome("Q2", 2, "translation")
    q.add("Q3", "architecture", "calibration", {}, {}, True, {"count": 3, "instance_of": ["Q16970"]})
    q.set_outcome("Q3", 2, "drop")
    assert architecture.triage(q.row("Q3"), CFG)[0] == "pass"
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 0
    assert q.row("Q1").outcomes[2] == "drop" and q.row("Q2").outcomes[2] == "translation"
    assert q.row("Q3").outcomes[2] == "drop"


def test_a_cleared_row_loses_its_later_outcomes(q):
    dropped(q, sources=("building", "unesco"))
    for stage in (3, 4, 5):
        q.set_outcome("Q1", stage, "pass")
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 1
    assert set(q.row("Q1").outcomes) == {0, 1}


@pytest.mark.parametrize("data", [{"reason": "a person, not a work"}, {}, {"reason": "x", "skipped": True},
                                  {"reason": "3 sitelinks, under the floor of 15", "skipped": True}])
def test_only_a_floor_drop_is_triaged_again(q, data):
    dropped(q, sources=("building", "unesco"))
    q.set_outcome("Q1", 2, "drop", data)
    assert common.retriage(q, "architecture", architecture.triage, CFG) == 0
    assert q.row("Q1").outcomes[2] == "drop"


@pytest.mark.parametrize("floor_dropped", [False, True])
def test_a_building_skipped_by_hand_is_not_brought_back_by_candidates(q, tmp_path, floor_dropped):
    """Skipped at triage, or (a row the floor had dropped) at the quick check: either way it stays."""
    from pipeline import __main__ as cli
    q.add("Q1", "architecture", "building", {}, {}, False, {"count": 3, "instance_of": ["Q16970"]})
    q.set_outcome("Q1", 1, "pass")
    if floor_dropped:
        q.set_outcome("Q1", 2, "drop", {"reason": "3 sitelinks, under the floor of 15"})
    assert cli.main(["skip", "Q1", "--reason", "not now", "--db", str(tmp_path / "q.db")]) == 0
    before = q.row("Q1").data
    stage0(q, FakeSparql({"wdt:P1435": [item("Q1", "A", 3, "Q16970")]}))
    row = q.row("Q1")
    assert "unesco" in row.sources and {n: row.data[n] for n in before if n} == {n: before[n] for n in before if n}


# --- the judging order ---

def order(q):
    return [r.qid for r in q.pending(1, "architecture")]


def counted(*pairs):
    return [item(qid, qid, n, "Q16970") for qid, n in pairs]


ORDERED = {"wdt:P1435": counted(("Q4", 40), ("Q3", 2), ("Q6", 60)), "VALUES ?item": counted(("Q8", 5), ("Q9", 7)),
           "FILTER(?sitelinks < 20)": counted(("Q15", 15), ("Q1", 60)), "ORDER BY DESC": counted(("Q6", 60), ("Q2", 90))}
TWO = [{"qid": "Q9", "name": "x"}, {"qid": "Q8", "name": "y"}]


def test_the_works_come_first_then_the_most_linked_buildings(q):
    stage0(q, FakeSparql(ORDERED), works=TWO)
    assert order(q) == ["Q9", "Q8", "Q2", "Q1", "Q6", "Q4", "Q15", "Q3"]  # a tie goes by QID
    assert set(q.row("Q6").sources) == {"building", "unesco"}


def test_the_order_does_not_depend_on_the_clock(q, monkeypatch):
    from pipeline import queue
    monkeypatch.setattr(queue.time, "time", lambda: 1000.0)
    stage0(q, FakeSparql(ORDERED), works=TWO)
    assert order(q) == ["Q9", "Q8", "Q2", "Q1", "Q6", "Q4", "Q15", "Q3"]


def test_a_failed_source_adds_what_was_found_in_order(q):
    with pytest.raises(RuntimeError, match="timeout"):
        stage0(q, FailingOn(ORDERED, "wdt:P1435"), works=TWO)
    assert order(q) == ["Q9", "Q8", "Q2", "Q1", "Q6", "Q15"]


def test_a_later_run_adds_its_rows_after_those_queued(q):
    stage0(q, FakeSparql({"ORDER BY DESC": counted(("Q5", 20))}), works=[])
    stage0(q, FakeSparql(ORDERED), works=TWO)
    assert order(q) == ["Q5", "Q9", "Q8", "Q2", "Q1", "Q6", "Q4", "Q15", "Q3"]


def test_a_class_closure_too_large_for_the_nested_exclusion_is_reported(q, monkeypatch, capsys):
    monkeypatch.setattr(common, "VALUES_MAX", 2)
    sparql = FakeSparql({"?sub wdt:P279* wd:Q44613": [{"sub": s} for s in ("Q44613", "QA", "QB")]})
    stage0(q, sparql)
    assert "Q44613 has 3 classes, over 2" in capsys.readouterr().err


def test_a_work_class_lost_to_a_cycle_is_reported(q, capsys):
    both = [{"sub": "Q44613"}, {"sub": "Q160742"}]
    stage0(q, FakeSparql({"wdt:P279* wd:Q44613": both, "wdt:P279* wd:Q160742": both}))
    err = capsys.readouterr().err
    assert "Q44613 is left out" in err and "Q160742 is left out" in err


def test_ordinary_closures_report_nothing(q, capsys):
    stage0(q, FakeSparql({}))
    assert capsys.readouterr().err == ""


def test_retriage_never_triages_a_row_with_a_status(q):
    dropped(q, status="open")
    calls = []
    common.retriage(q, "architecture", lambda row, cfg: calls.append(row.qid) or ("pass", ""), CFG)
    assert calls == []


def test_candidates_clears_a_floor_drop_when_unesco_arrives(q):
    dropped(q)
    sparql = FakeSparql({"wdt:P1435": [item("Q1", "A", 3, "Q16970")]})
    stage0(q, sparql)
    assert 2 not in q.row("Q1").outcomes


def test_a_run_that_raised_in_retriage_is_cleared_by_the_next(q, monkeypatch):
    dropped(q)
    sparql = FakeSparql({"wdt:P1435": [item("Q1", "A", 3, "Q16970")]})
    real = common.retriage

    def boom(*args):
        raise RuntimeError("retriage failed")

    monkeypatch.setattr(common, "retriage", boom)
    with pytest.raises(RuntimeError, match="retriage failed"):
        stage0(q, sparql)
    assert "unesco" in q.row("Q1").sources and q.row("Q1").outcomes[2] == "drop"
    monkeypatch.setattr(common, "retriage", real)
    stage0(q, sparql)
    assert 2 not in q.row("Q1").outcomes


def test_a_failed_source_still_retriages_and_the_error_propagates(q):
    dropped(q)
    # the last query, unesco on monasteries, fails
    sparql = FailingOn({"wdt:P1435": [item("Q1", "A", 3, "Q16970")]},
                       "P1435 ?heritage .\n  ?item wikibase:sitelinks ?sitelinks . FILTER(?sitelinks >= 1)\n"
                       "  ?item wdt:P31 ?cls . FILTER(?cls IN (wd:Q44613))")
    with pytest.raises(RuntimeError, match="timeout"):
        stage0(q, sparql)
    assert "unesco" in q.row("Q1").sources and 2 not in q.row("Q1").outcomes


def broken(monkeypatch, name, ran):
    real = getattr(architecture.benchmark_code if name == "hold" else common, name)
    module = architecture.benchmark_code if name == "hold" else common

    def boom(*args):
        ran.append(name)
        raise ValueError(f"{name} failed")

    def spy(*args):
        ran.append(name)
        return real(*args)

    monkeypatch.setattr(module, name, boom)
    other, other_name = (common, "retriage") if name == "hold" else (architecture.benchmark_code, "hold")
    real_other = getattr(other, other_name)

    def spy_other(*args):
        ran.append(other_name)
        return real_other(*args)

    monkeypatch.setattr(other, other_name, spy_other)


@pytest.mark.parametrize("name", ["retriage", "hold"])
def test_the_source_error_survives_a_failing_finish(q, monkeypatch, capsys, name):
    ran = []
    broken(monkeypatch, name, ran)
    sparql = FailingOn({}, "wdt:P1435")
    with pytest.raises(RuntimeError, match="timeout"):
        stage0(q, sparql)
    assert ran == ["hold", "retriage"]
    assert f"architecture candidates: {name} failed" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["retriage", "hold"])
def test_a_failing_finish_alone_propagates_and_both_ran(q, monkeypatch, name):
    ran = []
    broken(monkeypatch, name, ran)
    with pytest.raises(ValueError, match=f"{name} failed"):
        stage0(q, FakeSparql({}))
    assert ran == ["hold", "retriage"]


# --- stage 4: the dossier ---

DC = {"dossier_languages": 3}


def drow(sitelinks, calibration=False, **data):
    return Row("Q1", "architecture", ["building"], {}, sitelinks, calibration, data={0: {"count": 40, **data}})


def test_wikidata_note_names_the_facts_of_the_building_in_order():
    ent = entity("Sagrada Família", P1435=["Q7"], P131=["Q5"], P825=["Q4"], P140=["Q3"], P149=["Q2"], P84=["Q6"])
    ent["claims"]["P571"] = [time_claim(1882)]
    labels = {"Q6": "Antoni Gaudí", "Q2": "Modernisme", "Q3": "Catholicism", "Q4": "Holy Family", "Q5": "Barcelona",
              "Q7": "World Heritage Site"}
    note = architecture.wikidata_note({**ent, "id": "Q9"}, labels.get)
    assert note == {"lang": "en", "url": "https://www.wikidata.org/wiki/Q9", "heading": "Wikidata", "text": (
        "Architect: Antoni Gaudí; style: Modernisme; religion: Catholicism; dedicated to: Holy Family; "
        "location: Barcelona; date: 1882; heritage: World Heritage Site.")}


def test_wikidata_note_leaves_out_what_is_not_stated_or_has_no_label():
    ent = entity(P84=["Q6"], P149=["Q2"], P1435=["Q7"])
    note = architecture.wikidata_note({**ent, "id": "Q9"}, {"Q6": "Antoni Gaudí", "Q7": "World Heritage Site"}.get)
    assert note["text"] == "Architect: Antoni Gaudí; heritage: World Heritage Site."
    assert architecture.wikidata_note({**entity(), "id": "Q9"}, {}.get) is None


def test_wikidata_note_of_a_person_is_none():
    ent = entity("Man", P31=["Q5"], P140=["Q3"])
    assert architecture.wikidata_note({**ent, "id": "Q9"}, {"Q3": "Catholicism"}.get) is None


def test_the_dossier_reads_the_lead_and_wanted_sections_of_each_language():
    pages = {("en", "wikipedia"): {"#lead:T": "A church.", "T": [("Architecture", "a"), ("Façade", "f"),
                                                                 ("Gallery", "g"), ("Stained glass", "s")]},
             ("it", "wikipedia"): {"T it": [("Cenni storici", "c"), ("Mosaici", "m"), ("Note", "n")]},
             ("es", "wikipedia"): {"T es": [("Historia", "h")]},
             ("de", "wikipedia"): {"T de": [("Baugeschichte", "b"), ("Weblinks", "w")]}}
    d = architecture.dossier(drow({"dewiki": "T de", "itwiki": "T it", "eswiki": "T es", "enwiki": "T"}), DC,
                             wiki_for_pages(pages), FakeWikidataEntities({"Q1": entity()}))
    assert d["languages"] == {"en": 4, "it": 2, "es": 1}  # the German page is past dossier_languages
    assert [(n["lang"], n["heading"]) for n in d["notes"]] == [
        ("en", "Lead"), ("en", "Stained glass"), ("it", "Mosaici"), ("en", "Architecture"), ("en", "Façade"),
        ("it", "Cenni storici"), ("es", "Historia")]
    assert d["notes"][0] == {"lang": "en", "url": "https://en.wikipedia.org/wiki/T", "heading": "Lead",
                             "text": "A church."}


def test_the_dossier_closes_with_one_wikidata_note():
    wd = FakeWikidataEntities({"Q1": entity(P84=["Q100"]), "Q100": entity("Gaudí")})
    pages = wiki_for_pages({("en", "wikipedia"): {"T": [("History", "h")]}})
    d = architecture.dossier(drow({"enwiki": "T"}), DC, pages, wd)
    assert [n["heading"] for n in d["notes"]] == ["History", "Wikidata"] and d["languages"] == {"en": 1}
    assert d["notes"][-1]["text"] == "Architect: Gaudí."
    assert d["notes"][-1]["url"] == "https://www.wikidata.org/wiki/Q1"


def test_the_scrovegni_chapel_is_read_without_its_frescos():
    frescos = {"P1": {"sitelinks": {"enwiki": "Lamentation"}, "count": 30}}
    wd = FakeWikidataEntities({"Q1": entity("Scrovegni Chapel", P527=["P1", "P2"], P361=["P3"]),
                               "P2": entity(sitelinks={"enwiki": "Last Judgment"})})
    pages = {("en", "wikipedia"): {"Scrovegni Chapel": [("Decoration", "Giotto's cycle")],
                                   "Lamentation": [("Iconography", "x")], "Last Judgment": [("Iconography", "y")]}}
    d = architecture.dossier(drow({"enwiki": "Scrovegni Chapel"}, parts=frescos), DC, wiki_for_pages(pages), wd)
    assert [(n["heading"], n["text"]) for n in d["notes"]] == [("Decoration", "Giotto's cycle")]
    assert wd.asked == ["Q1"]  # no part, no whole


def test_the_dossier_stage_drops_an_empty_dossier_but_not_calibration():
    wd = FakeWikidataEntities({"Q1": entity(P84=["Q100"]), "Q100": entity("Gaudí")})
    pages = wiki_for_pages({("en", "wikipedia"): {"T": []}})
    assert architecture.dossier_stage(drow({"enwiki": "T"}), DC, pages, wd) == ("drop", {"reason": "empty dossier"})
    outcome, data = architecture.dossier_stage(drow({"enwiki": "T"}, calibration=True), DC, pages, wd)
    assert outcome == "pass" and [n["heading"] for n in data["notes"]] == ["Wikidata"]


WANTED = {
    "en": ["History", "Historical background", "Construction", "Architecture", "Architectural style", "Design", "Plan",
           "Exterior", "Interior", "Decoration", "Art and treasures", "Iconography", "Symbolism", "Mosaics",
           "Stained glass", "Stained-glass windows", "Sculptures", "Façade", "West facade", "Liturgical use", "Worship",
           "Pilgrimage", "Consecration", "Dedication", "Significance", "Interpretations",
           "Art", "Works of art", "Arts", "Artworks", "Artistic features", "Floor plan", "Plans", "Plan and elevation", "Designs", "Designed spaces", "Decorative scheme",
           "Plan, 1200", "Art: the choir", "Plan–elevation", "Description"],
    "it": ["Storia", "Cenni storici", "Costruzione", "Architettura", "Progetto", "Pianta", "Esterno", "Interni",
           "Decorazioni", "Opere d'arte", "Iconografia", "Simbolismo", "Mosaici", "Vetrate", "Sculture", "La scultura",
           "Facciate", "Liturgia", "Culto", "Pellegrinaggi", "Consacrazione", "Dedicazione", "Significato",
           "Interpretazioni",
           "Progetti", "Esterni", "Interno", "Culti", "Arti figurative", "Arte", "Artisti", "Piante",
           "Descrizione"],
    "es": ["Historia", "Contexto histórico", "Construcción", "Arquitectura", "Diseño", "Planta", "Exterior",
           "Interiores", "Decoración", "Arte", "Iconografía", "Simbología", "Símbolos", "Mosaicos", "Vidrieras",
           "Vitrales", "Esculturas", "Fachadas", "Liturgia", "Uso litúrgico", "Culto", "Peregrinación",
           "Consagración", "Dedicación", "Significado", "Interpretaciones",
           "Proyectos", "Diseños", "Artes decorativas", "Artísticas", "Obras de arte",
           "Planta-baja", "Descripción"],
    "fr": ["Histoire", "Historique", "Construction", "Architecture", "Conception", "Plan", "Extérieur", "Intérieur",
           "Décoration", "Décors", "Œuvres d'art", "Art sacré", "Iconographie", "Symbolique", "Mosaïques", "Vitraux",
           "Sculptures", "Façades", "Liturgie", "Culte", "Pèlerinage", "Consécration", "Dédicace", "Signification",
           "Interprétations",
           "Art", "L'art", "L’art", "Arts décoratifs", "Projets", "Plans", "Œuvres",
           "Art, mobilier", "Description"],
    "de": ["Geschichte", "Baugeschichte", "Bau", "Architektur", "Baubeschreibung", "Entwurf", "Grundriss", "Äußeres",
           "Außenbau", "Inneres", "Innenraum", "Ausstattung", "Kunstwerke", "Ikonographie", "Ikonografie",
           "Symbolik", "Mosaiken", "Fenster", "Glasfenster", "Glasmalereien", "Skulpturen", "Fassade", "Liturgie",
           "Gottesdienste", "Wallfahrt", "Weihe", "Patrozinium", "Bedeutung", "Deutung", "Interpretation",
           "Entwürfe", "Pläne", "Plan", "Wandmalerei", "Wandmalereien", "Deckenmalerei", "Wiederaufbau", "Aufbau", "Umbau", "Umbauten", "Kirchenfenster", "Bauzeit", "Bauphasen", "Bauwerk", "Neubau", "Innenausstattung", "Äußere Gestalt", "Fresken",
           "Bau- und Nutzungsgeschichte", "Plan (1200)", "Beschreibung"],
    "pt": ["História", "Contexto histórico", "Construção", "Arquitetura", "Arquitectura", "Projeto", "Planta",
           "Exterior", "Interior", "Decoração", "Arte", "Iconografia", "Simbolismo", "Símbolos", "Mosaicos",
           "Vitrais", "Esculturas", "Fachadas", "Liturgia", "Uso litúrgico", "Culto", "Peregrinações", "Consagração",
           "Dedicação", "Significado", "Interpretação",
           "Projetos", "Obras de arte", "Artes", "Artístico", "Plantas",
           "Arte, talha", "Descrição"],
}
UNWANTED = {
    "en": ["See also", "References", "External links", "Gallery", "Bibliography",
           "Articles", "Arthurian legend", "Planets", "Heritage designation", "Designation", "Sister projects"],
    "it": ["Note", "Voci correlate", "Bibliografia", "Collegamenti esterni", "Altri progetti",
           "Internet", "Articoli", "Nella cultura di massa", "Internazionale", "Artefici vari"],
    "es": ["Véase también", "Referencias", "Enlaces externos", "Bibliografía",
           "Proyectos hermanos", "Artículos", "Artesanos"],
    "fr": ["Articles connexes", "Voir aussi", "Notes et références", "Liens externes", "Bibliographie",
           "Autres projets", "Article connexe", "Articles", "Planches"],
    "de": ["Weblinks", "Literatur", "Einzelnachweise", "Siehe auch",
           "Bauernkrieg", "Bauern", "Innenstadt", "Außenpolitik", "Schwesterprojekte", "Planeten"],
    "pt": ["Ver também", "Referências", "Ligações externas", "Bibliografia",
           "Outros projetos", "Outros projectos", "Artigos relacionados", "Projetos irmãos"],
}


@pytest.mark.parametrize("lang", common.LANGS)
def test_the_headings_read(lang):
    words = architecture.HEADINGS[lang]
    assert len(words) == len(set(words)) and all(w == w.lower() for w in words)
    skip = architecture.LINK_SECTIONS
    stop = architecture.STOP[lang]
    assert all(w == w.lower() for w in stop) and len(architecture.GROUPS[lang]) == len(architecture.CAPS)
    assert [h for h in WANTED[lang] if not common.wanted(h, words, skip)] == []
    assert [h for h in UNWANTED[lang] if common.wanted(h, words, skip)] == []


def test_a_fragment_with_a_trailing_blank_is_a_whole_word():
    assert common.wanted("L'art", ("art ",)) and common.wanted("Art sacré", ("art ",))
    assert not common.wanted("Articles", ("art ",))


def test_link_sections_are_left_out_of_the_dossier():
    pages = {("it", "wikipedia"): {"T": [("Progetti", "p"), ("Altri progetti", "x"), ("Collegamenti esterni", "y")]},
             ("fr", "wikipedia"): {"T": [("Projets", "p"), ("Autres projets", "x")]}}
    d = architecture.dossier(drow({"itwiki": "T", "frwiki": "T"}), DC, wiki_for_pages(pages),
                             FakeWikidataEntities({"Q1": entity()}))
    assert [n["heading"] for n in d["notes"]] == ["Progetti", "Projets"]


def en_dossier(*sections, lead="", **other):
    pages = {("en", "wikipedia"): {"T": list(sections), "#lead:T": lead}}
    pages |= {(lang, "wikipedia"): {"T": value} for lang, value in other.items()}
    links = {"enwiki": "T", **{f"{lang}wiki": "T" for lang in other}}
    notes = architecture.dossier(drow(links), DC, wiki_for_pages(pages), FakeWikidataEntities({"Q1": entity()}))["notes"]
    return [(n["heading"], n["text"]) for n in notes]


def test_a_wanted_heading_takes_its_subtree():
    assert en_dossier(("The portals and their sculpture", "stub", 1), ("Royal Portal", "west", 2),
                      ("Central tympanum", "Christ", 3), ("Towers", "t", 1), ("Clock", "c", 2)) == [
        ("The portals and their sculpture", "stub"),
        ("The portals and their sculpture / Royal Portal", "west"),
        ("The portals and their sculpture / Central tympanum", "Christ")]


def test_a_parent_with_no_text_of_its_own_still_gives_its_subtree():
    assert en_dossier(("Stained glass windows", "", 1), ("12th century", "blue", 2)) == [
        ("Stained glass windows / 12th century", "blue")]


def test_a_wanted_subsection_is_read_once():
    assert en_dossier(("Architecture", "a", 1), ("Interior", "i", 2), ("Nave", "n", 3), ("Mosaics", "m", 2)) == [
        ("Mosaics", "m"), ("Architecture", "a"), ("Interior", "i"), ("Interior / Nave", "n")]


def test_link_sections_and_stopped_headings_lose_their_subtree():
    assert en_dossier(("Interior", "i", 1), ("Organ", "o", 2), ("History", "of the organ", 3), ("Nave", "n", 2),
                      ("See also", "s", 1), ("History", "h", 2), ("Bells", "b", 1), ("History", "x", 2)) == [
        ("Interior", "i"), ("Interior / Nave", "n")]


def test_a_stopped_word_in_a_wanted_heading_does_not_stop_it():
    assert en_dossier(("Organ and stained glass", "o", 1)) == [("Organ and stained glass", "o")]


def test_art_precedes_architecture_which_precedes_history_in_every_language():
    assert en_dossier(("History", "h", 1), ("Liturgical use", "l", 2), ("Architecture", "a", 1), ("Iconography", "i", 1),
                      lead="L", it=[("Storia", "s"), ("Interno", "n"), ("Mosaici", "m")]) == [
        ("Lead", "L"), ("Liturgical use", "l"), ("Iconography", "i"), ("Mosaici", "m"), ("Architecture", "a"),
        ("Interno", "n"), ("History", "h"), ("Storia", "s")]


def test_a_long_subtree_is_capped_and_shared_by_its_sections():
    long, (art, _, history) = "word " * 5000, architecture.CAPS
    notes = dict(en_dossier(("History", long, 1), ("Wars", long, 2), ("Fire", "short", 2), ("Iconography", long, 1)))
    assert notes["History / Fire"] == "short"
    assert history * 0.9 < sum(len(t) for h, t in notes.items() if h.startswith("History")) <= history
    assert len(notes["History"]) > history * 0.4 and len(notes["History / Wars"]) > history * 0.4
    assert art * 0.9 < len(notes["Iconography"]) <= art and notes["Iconography"].endswith("word …")
    assert len(dict(en_dossier(lead=long))["Lead"]) <= architecture.LEAD_CAP


def test_a_subtree_of_many_sections_keeps_the_first_ones_readable():
    notes = en_dossier(("History", "", 1), *((f"Year {n}", "word " * 300, 2) for n in range(30)))
    assert len(notes) == architecture.CAPS[2] // architecture.MIN_SHARE
    assert all(len(t) > architecture.MIN_SHARE * 0.9 for _, t in notes) and notes[0][0] == "History / Year 0"


def test_a_subtree_within_the_cap_is_read_whole():
    sections = [("History", "", 1), *((f"Year {n}", "x" * 149, 2) for n in range(10))]
    assert [t for _, t in en_dossier(*sections)] == ["x" * 149] * 10


def test_a_stub_takes_no_place_from_the_text():
    notes = en_dossier(("History", "stub", 1), *((f"Year {n}", "word " * 300, 2) for n in range(30)))
    assert notes[0] == ("History", "stub")
    assert [h for h, _ in notes[1:]] == [f"History / Year {n}" for n in range(4)]
    assert 0.9 * architecture.CAPS[2] < sum(len(t) for _, t in notes) <= architecture.CAPS[2]


def test_short_sections_leave_room_for_the_later_ones():
    many = [(f"Note {n}", "x" * 100, 2) for n in range(8)]
    notes = dict(en_dossier(("History", "", 1), *many, ("Fire", "word " * 1000, 2)))
    assert len(notes) == 9 and len(notes["History / Fire"]) > 1100


STOPPED = {
    "en": ["Bells", "The bells", "Organ", "Grand Organ", "Organs", "Statistics", "Dimensions", "Tourism",
           "Popular culture"],
    "it": ["Campane", "Le campane", "Organo", "Organi a canne", "Dimensioni", "Turismo", "Nella cultura di massa"],
    "es": ["Campanas", "El órgano", "Órganos", "Dimensiones", "Turismo", "En la cultura popular"],
    "fr": ["Cloches", "La cloche", "Orgue", "Les orgues", "Dimensions", "Tourisme", "Dans la culture populaire"],
    "de": ["Glocken", "Glocke", "Geläut", "Orgel", "Orgeln", "Große Orgel", "Chororgel", "Maße", "Tourismus"],
    "pt": ["Sinos", "Órgão", "Órgãos", "Dimensões", "Turismo", "Na cultura popular"],
}
NOT_STOPPED = {  # the parts of the building that hold them, and the art of their cases
    "en": ["Bell tower", "Bell towers", "Tower of the bells", "Organ loft", "Organ case", "Organ gallery"],
    "it": ["Campanile", "Torre delle campane", "Cella campanaria", "Cantoria dell'organo", "Cassa dell'organo"],
    "es": ["Campanario", "Torre de las campanas", "Tribuna del órgano", "Caja del órgano"],
    "fr": ["Clocher", "Clochers", "Clocheton", "Tour des cloches", "Tribune d'orgue", "Buffet d'orgue",
           "Tribune des orgues"],
    "de": ["Glockenturm", "Glockentürme", "Turm mit Glocken", "Orgelempore", "Orgelprospekt", "Empore der Orgel"],
    "pt": ["Campanário", "Torre sineira", "Torre dos sinos", "Tribuna do órgão", "Caixa do órgão"],
}


@pytest.mark.parametrize("lang", common.LANGS)
def test_the_instruments_are_stopped_and_the_towers_and_lofts_are_not(lang):
    def stopped(heading):
        tree = [(1, next(h for h in WANTED[lang]), "a"), (2, heading, "b"), (3, "xyz", "c")]
        return len(architecture._blocks(tree, lang)[0][1]) == 1
    assert [h for h in STOPPED[lang] if not stopped(h)] == []
    assert [h for h in NOT_STOPPED[lang] if stopped(h)] == []


# --- calibration and the benchmark ---

KINDS = [{"qid": "Q2", "name": "x", "kind": "strong"}, {"qid": "Q3", "name": "y", "kind": "weak"},
         {"qid": "Q4", "name": "z", "kind": "control"}]


def test_calibration_root_is_sacred_architecture(monkeypatch):
    seen = {}
    monkeypatch.setattr(common, "calibration_qids",
                        lambda wikis, wd, tree, roots: seen.update(tree=tree, roots=roots) or (["Q1"], ["en:X"]))
    tree = {"fields": []}
    assert architecture.calibration_qids({}, wikidata=object(), tree=tree) == (["Q1"], ["en:X"])
    assert seen == {"tree": tree, "roots": {"sacred-architecture"}}


def test_the_calibration_root_is_a_node_of_the_tree(monkeypatch):
    monkeypatch.setattr(common, "calibration_qids", lambda wikis, wd, tree, roots: (common.field_keys(tree, roots), []))
    keys, _ = architecture.calibration_qids({}, wikidata=object())
    assert keys == {"sacred-architecture"}


def test_calibration_adds_the_pages_and_the_benchmark_of_the_three_kinds(q):
    sparql = FakeSparql({"VALUES ?item { wd:Q1 }\n  ?item wdt:P31/wdt:P279* wd:Q2977 .": [item("Q1", "A", 50, "Q2977")],
                         "VALUES ?item { wd:Q2 wd:Q3 wd:Q4 }": [item("Q2", "B", 50), item("Q3", "C", 5),
                                                               item("Q4", "D", 90, "Q1440476")]})
    assert architecture.add_calibration(q, sparql, ["Q1"], benchmark=KINDS) == 4
    assert {w["qid"]: q.row(w["qid"]).data[0]["benchmark_kind"] for w in KINDS} == {
        "Q2": "strong", "Q3": "weak", "Q4": "control"}
    for qid in ("Q1", "Q2", "Q3", "Q4"):
        row = q.row(qid)
        assert row.domain == "architecture" and row.calibration and row.sources == ["calibration"]
    assert all(q.row(w["qid"]).data[0]["benchmark"] for w in KINDS) and "benchmark" not in q.row("Q1").data[0]
    assert q.open_rows("architecture") == []


def test_calibration_defaults_to_the_configured_benchmark(q, monkeypatch):
    monkeypatch.setattr(architecture, "load_config", lambda: {"architecture": {"benchmark": KINDS[1:2]}})
    assert architecture.add_calibration(q, FakeSparql({"VALUES ?item { wd:Q3 }": [item("Q3", "C", 5)]}), []) == 1
    assert q.row("Q3").data[0]["benchmark_kind"] == "weak"


def test_the_configured_benchmark():
    bench = load_config()["architecture"]["benchmark"]
    assert len({b["qid"] for b in bench}) == len(bench) == 18
    assert {kind: {b["qid"] for b in bench if b["kind"] == kind} for kind in ("strong", "weak", "control")} == {
        "strong": {"Q48435", "Q180274", "Q2981", "Q186282", "Q193193", "Q638250", "Q721817", "Q1303856", "Q12512",
                   "Q963954", "Q187702"},
        "weak": {"Q493133", "Q624556", "Q254476", "Q167538"},
        "control": {"Q243", "Q45178", "Q179199"}}
    assert all(b["name"] for b in bench)


@pytest.mark.parametrize("calibrate_first", [False, True])
def test_a_listed_work_of_the_benchmark_is_held_back_until_its_release(q, calibrate_first):
    """A building in `works` and in the benchmark (Hagia Sophia here, as test data): the benchmark wins, whatever the order of the commands; once
    released it is a candidate with the `list` source, so the floor does not apply."""
    hagia = [{"qid": "Q12506", "name": "Hagia Sophia", "kind": "strong"}]
    sparql = FakeSparql({"VALUES ?item { wd:Q12506 }": [item("Q12506", "Hagia Sophia", 3, "Q32815")]})
    runs = [lambda: stage0(q, sparql, works=[{"qid": "Q12506", "name": "Hagia Sophia"}], benchmark=hagia),
            lambda: architecture.add_calibration(q, sparql, [], benchmark=hagia)]
    for run in reversed(runs) if calibrate_first else runs:
        run()
        row = q.row("Q12506")
        assert row.calibration and row.data[0]["benchmark"] and row.data[0]["benchmark_kind"] == "strong"
        assert q.open_rows("architecture") == []
    assert set(q.row("Q12506").sources) == {"list", "calibration"}
    assert q.release_calibration("Q12506")
    for run in runs:  # later runs leave it released
        run()
    row = q.row("Q12506")
    assert row.calibration is False and "benchmark" not in row.data[0]
    assert [r.qid for r in q.open_rows("architecture")] == ["Q12506"]
    assert architecture.triage(row, CFG) == ("pass", "")


def test_release_benchmark_releases_one_building(q, tmp_path, capsys):
    from pipeline import __main__ as cli
    sparql = FakeSparql({"VALUES ?item": [item(w["qid"], w["name"], 50) for w in KINDS]})
    architecture.add_calibration(q, sparql, [], benchmark=KINDS)
    args = ["--domain", "architecture", "release-benchmark", "Q3", "--db", str(tmp_path / "q.db")]
    assert cli.main(args) == 0
    assert "released Q3" in capsys.readouterr().out
    assert [r.qid for r in q.open_rows("architecture")] == ["Q3"]
    assert q.row("Q2").calibration and q.row("Q4").calibration


def test_a_failing_chunk_of_works_keeps_the_earlier_ones_in_order(q):
    works = [{"qid": f"Q{n}", "name": "x"} for n in range(1000, 1060)]
    sparql = FailingOn({"VALUES ?item { wd:Q1000 ": counted(("Q1001", 90), ("Q1000", 3))}, "VALUES ?item { wd:Q1050 ")
    with pytest.raises(RuntimeError, match="timeout"):
        stage0(q, sparql, works=works)
    assert order(q) == ["Q1000", "Q1001"] and q.row("Q1000").sources == ["list"]
