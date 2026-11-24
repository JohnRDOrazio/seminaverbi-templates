import pytest

from pipeline import load_config
from pipeline.domains import DOMAINS, architecture, common
from pipeline.judge import Judge
from pipeline.queue import Queue, Row
from test_pipeline_art import time_claim
from test_pipeline_film import FakeSparql, FakeWikidataEntities, entity, item, wiki_for_pages

CFG = {"sitelink_floor": 15}
PROOF_CLASSES = ("Q163687", "Q108325", "Q160742", "Q44613")


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
    assert cfg["works"] == [{"qid": "Q48435", "name": "Sagrada Família"}, {"qid": "Q12506", "name": "Hagia Sophia"}]
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


# --- retriage ---

def dropped(q, qid="Q1", sources=("building",), count=3, instance_of=("Q16970",), status=None):
    q.add(qid, "architecture", sources[0], {}, {}, False, {"count": count, "instance_of": list(instance_of)})
    for extra in sources[1:]:
        q.add(qid, "architecture", extra, {}, {}, False)
    q.set_outcome(qid, 1, "pass")
    q.set_outcome(qid, 2, "drop", {"reason": "x"})
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
    sparql = FailingOn({"wdt:P1435": [item("Q1", "A", 3, "Q16970")]}, "VALUES ?item { wd:Q3 }")
    with pytest.raises(RuntimeError, match="timeout"):
        stage0(q, sparql, works=[{"qid": "Q3", "name": "C"}])
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
        ("en", "Lead"), ("en", "Architecture"), ("en", "Façade"), ("en", "Stained glass"),
        ("it", "Cenni storici"), ("it", "Mosaici"), ("es", "Historia")]
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
           "Pilgrimage", "Consecration", "Dedication", "Significance", "Interpretations"],
    "it": ["Storia", "Cenni storici", "Costruzione", "Architettura", "Progetto", "Pianta", "Esterno", "Interni",
           "Decorazioni", "Opere d'arte", "Iconografia", "Simbolismo", "Mosaici", "Vetrate", "Sculture", "La scultura",
           "Facciate", "Liturgia", "Culto", "Pellegrinaggi", "Consacrazione", "Dedicazione", "Significato",
           "Interpretazioni"],
    "es": ["Historia", "Contexto histórico", "Construcción", "Arquitectura", "Diseño", "Planta", "Exterior",
           "Interiores", "Decoración", "Arte", "Iconografía", "Simbología", "Símbolos", "Mosaicos", "Vidrieras",
           "Vitrales", "Esculturas", "Fachadas", "Liturgia", "Uso litúrgico", "Culto", "Peregrinación",
           "Consagración", "Dedicación", "Significado", "Interpretaciones"],
    "fr": ["Histoire", "Historique", "Construction", "Architecture", "Conception", "Plan", "Extérieur", "Intérieur",
           "Décoration", "Décors", "Œuvres d'art", "Art sacré", "Iconographie", "Symbolique", "Mosaïques", "Vitraux",
           "Sculptures", "Façades", "Liturgie", "Culte", "Pèlerinage", "Consécration", "Dédicace", "Signification",
           "Interprétations"],
    "de": ["Geschichte", "Baugeschichte", "Bau", "Architektur", "Baubeschreibung", "Entwurf", "Grundriss", "Äußeres",
           "Außenbau", "Inneres", "Innenraum", "Ausstattung", "Kunstwerke", "Ikonographie", "Ikonografie",
           "Symbolik", "Mosaiken", "Fenster", "Glasfenster", "Glasmalereien", "Skulpturen", "Fassade", "Liturgie",
           "Gottesdienste", "Wallfahrt", "Weihe", "Patrozinium", "Bedeutung", "Deutung", "Interpretation"],
    "pt": ["História", "Contexto histórico", "Construção", "Arquitetura", "Arquitectura", "Projeto", "Planta",
           "Exterior", "Interior", "Decoração", "Arte", "Iconografia", "Simbolismo", "Símbolos", "Mosaicos",
           "Vitrais", "Esculturas", "Fachadas", "Liturgia", "Uso litúrgico", "Culto", "Peregrinações", "Consagração",
           "Dedicação", "Significado", "Interpretação"],
}
UNWANTED = {
    "en": ["See also", "References", "External links", "Gallery", "Bibliography"],
    "it": ["Note", "Voci correlate", "Bibliografia", "Collegamenti esterni", "Altri progetti"],
    "es": ["Véase también", "Referencias", "Enlaces externos", "Bibliografía"],
    "fr": ["Articles connexes", "Voir aussi", "Notes et références", "Liens externes", "Bibliographie"],
    "de": ["Weblinks", "Literatur", "Einzelnachweise", "Siehe auch"],
    "pt": ["Ver também", "Referências", "Ligações externas", "Bibliografia"],
}


@pytest.mark.parametrize("lang", common.LANGS)
def test_the_headings_read(lang):
    words = architecture.HEADINGS[lang]
    assert len(words) == len(set(words)) and all(w == w.lower() for w in words)
    assert [h for h in WANTED[lang] if not common.wanted(h, words)] == []
    assert [h for h in UNWANTED[lang] if common.wanted(h, words)] == []


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
    kinds = [b["kind"] for b in bench]
    assert (kinds.count("strong"), kinds.count("weak"), kinds.count("control")) == (11, 4, 3)
    assert all(b["name"] for b in bench)


@pytest.mark.parametrize("calibrate_first", [False, True])
def test_a_listed_work_of_the_benchmark_is_held_back_until_its_release(q, calibrate_first):
    """Hagia Sophia is in `works` and in the benchmark: the benchmark wins, whatever the order of the commands; once
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
