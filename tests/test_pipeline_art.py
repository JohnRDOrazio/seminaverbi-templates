from pipeline import load_config
from pipeline.domains import DOMAINS, art
from pipeline.queue import Row

CFG = {"sitelink_floor": 15}


def arow(sources, count, instance_of=("Q3305213",), calibration=False, **data):
    return Row("Q1", "art", sources, {}, {}, calibration,
               data={0: {"count": count, "instance_of": list(instance_of), **data}})


def test_registry_has_art():
    assert DOMAINS["art"] is art


def test_art_floor_and_the_list_source():
    assert art.triage(arow(["depicts"], 14), CFG) == ("drop", "14 sitelinks, under the floor of 15")
    assert art.triage(arow(["depicts"], 15), CFG)[0] == "pass"
    assert art.triage(arow(["list"], 2), CFG)[0] == "pass"
    assert art.triage(arow(["list", "depicts"], 2), CFG)[0] == "pass"


def test_copies_and_people_are_dropped():
    assert art.triage(arow(["depicts"], 40, ["Q5"]), CFG) == ("drop", "a person, not a work")
    assert art.triage(arow(["depicts"], 40, ["Q1784021"]), CFG) == (
        "drop", "a copy or study, not a work of its own")
    assert art.triage(arow(["depicts"], 40, ["Q1784021", "Q3305213"]), CFG)[0] == "pass"  # a work class stays a work
    assert art.triage(arow(["depicts"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")


def test_calibration_always_passes():
    assert art.triage(arow(["calibration"], 1, ["Q5"], calibration=True, folded_into="Q9"), CFG) == (
        "pass", "calibration")


def test_significance_is_the_quick_question():
    assert "beyond depicting a religious subject" in art.QUESTIONS["plausible"]
    assert "decoration, satire or shock" in art.QUESTIONS["connections"]["entertainment"]
    assert set(art.QUESTIONS["connections"]) == {"direct", "seeds", "entertainment", "none"}


def test_classes_and_owns():
    assert art.WORK_CLASSES[:8] == ("Q3305213", "Q860861", "Q132137", "Q15711026", "Q46686", "Q3714446",
                                    "Q21061279", "Q219423")
    assert art.STOP_CLASSES == ("Q41176", "Q16970")
    assert art.covered.__name__ == "covered_with_parts"


def test_config_block():
    cfg = load_config()["art"]
    assert cfg["sitelink_floor"] == 15
    assert cfg["scene_classes"] and cfg["works"] == [] and cfg["iconclass_prefixes"] == ["7", "11"]


# --- stage 0: candidates ---

import pytest

from pipeline.domains import common
from pipeline.queue import Queue
from test_pipeline_film import FakeSparql, item


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CLOSURE_CACHE", tmp_path / "closure.json")
    return Queue(tmp_path / "q.db")


def test_each_art_source_tags_its_rows(q):
    sparql = FakeSparql({"wdt:P180": [item("Q1", "A", 30, "Q3305213")], "wdt:P1257": [item("Q2", "B", 30, "Q3305213")],
                         "wdt:P136 ?genre": [item("Q3", "C", 30, "Q3305213")]})
    art.candidates(q, sparql, None, load_config())
    assert {q.row(x).sources[0] for x in ("Q1", "Q2", "Q3")} == {"depicts", "iconclass", "genre"}
    assert q.row("Q1").sources == ["depicts"] and q.row("Q2").sources == ["iconclass"]
    assert q.row("Q1").domain == "art"


def test_scenes_are_queried_as_depicted_not_as_classes(q):
    sparql = FakeSparql()
    art.candidates(q, sparql, None, load_config())
    depicts = [x for x in sparql.queries if "wdt:P180" in x]
    assert depicts and any("wd:Q51628" in x and "wd:Q302" in x for x in depicts)
    assert not any("wd:Q51628" in x for x in sparql.queries if "wdt:P180" not in x and "?sub" not in x)
    assert any("wdt:P411" in x for x in depicts)
    assert any("wd:Q12405827" in x for x in depicts)


def test_art_queries_are_bounded_without_a_subclass_path(q):
    sparql = FakeSparql()
    art.candidates(q, sparql, None, load_config())
    assert not any("wdt:P279*" in x for x in sparql.queries if "?sub" not in x)
    assert all("LIMIT 500" in x for x in sparql.queries if "wikibase:sitelinks" in x)
    assert any('STRSTARTS(STR(?ic), "7")' in x and 'STRSTARTS(STR(?ic), "11")' in x for x in sparql.queries)


def test_fold_stops_at_a_building(q):
    # the fresco cycle Q1 is part of (P361) the chapel Q2, an instance of church building (Q16970):
    # the fold query excludes buildings, so Q1 stays the candidate and Q2 is never queued
    sparql = FakeSparql({"wdt:P180": [item("Q1", "Cycle", 30, "Q219423")]})
    art.candidates(q, sparql, None, load_config())
    folds = [x for x in sparql.queries if "wdt:P361" in x]
    assert folds and all("FILTER NOT EXISTS" in x and "wd:Q16970" in x and "wd:Q41176" in x for x in folds)
    assert q.row("Q1") is not None and q.row("Q2") is None


def test_a_panel_folds_into_its_altarpiece(q):
    sparql = FakeSparql({"wdt:P180": [item("Q10", "Panel", 30, "Q3305213")],
                         "wdt:P361": [{"item": "Q10", "whole": "Q1"}],
                         "VALUES ?item { wd:Q1 }": [item("Q1", "Altar", 40, "Q15711026")]})
    art.candidates(q, sparql, None, load_config())
    assert q.row("Q1").sources == ["depicts"] and set(q.row("Q1").data[0]["parts"]) == {"Q10"}


def test_listed_works_are_queued_by_qid(q):
    cfg = load_config()
    cfg["art"]["works"] = [{"qid": "Q7", "name": "x"}]
    sparql = FakeSparql({"wdt:P361": [], "VALUES ?item { wd:Q7 }": [item("Q7", "Listed", 3, "Q5555")]})
    art.candidates(q, sparql, None, cfg)
    assert q.row("Q7").sources == ["list"]
    listed = [x for x in sparql.queries if "VALUES ?item { wd:Q7 }" in x and "wikibase:sitelinks" in x]
    assert listed and all("wdt:P31" not in x.split("OPTIONAL")[0] and "LIMIT 500" in x for x in listed)


def test_a_listed_person_is_reported_and_skipped(q, capsys):
    cfg = load_config()
    cfg["art"]["works"] = [{"qid": "Q7", "name": "x"}]
    art.candidates(q, FakeSparql({"wdt:P361": [], "VALUES ?item { wd:Q7 }": [item("Q7", "Man", 90, "Q5")]}), None, cfg)
    assert q.row("Q7") is None and "person" in capsys.readouterr().out


def test_art_people_and_other_domains_are_left(q, capsys):
    q.add("Q8", "literature", "author", {}, {})
    sparql = FakeSparql({"wdt:P180": [item("Q8", "Book", 90, "Q3305213"), item("Q9", "Man", 90, "Q5")]})
    art.candidates(q, sparql, None, load_config())
    assert q.row("Q8").domain == "literature" and q.row("Q9") is None
    assert "person" in capsys.readouterr().out


# --- dossier ---

from test_pipeline_film import FakeWikidataEntities, claim, entity, wiki_for_pages  # noqa: E402

DC = {"dossier_languages": 3}


def drow(sitelinks, parts=None, calibration=False):
    data = {"count": 30, "instance_of": ["Q3305213"]}
    if parts is not None:
        data["parts"] = parts
    return Row("Q1", "art", ["depicts"], {}, sitelinks, calibration, data={0: data})


def test_every_language_has_history_and_description_headings():
    assert set(art.HEADINGS) == set(common.LANGS)
    for lang, words in art.HEADINGS.items():
        assert len(words) >= 10, lang
    for words in art.HEADINGS.values():
        assert any(w.startswith(("hist", "storia", "geschichte", "provenien", "provenan", "origin",
                                 "entstehung")) for w in words)
        assert any(w.startswith(("descri", "beschreibung")) for w in words)
        assert any(w.startswith(("iconogra", "ikono")) for w in words)


def test_the_dossier_reads_english_then_italian_without_an_original_language_rule():
    wd = FakeWikidataEntities({"Q1": entity(P407=["Q188"])})  # a German label on the work: no effect on the order
    pages = {("en", "wikipedia"): {"T": [("History", "h"), ("Provenance", "p"), ("Iconography", "i"),
                                         ("Gallery", "g")]},
             ("it", "wikipedia"): {"T it": [("Storia", "s"), ("Descrizione", "d")]},
             ("de", "wikipedia"): {"T de": [("Beschreibung", "b")]}}
    d = art.dossier(drow({"dewiki": "T de", "itwiki": "T it", "enwiki": "T"}), {"dossier_languages": 3},
                    wiki_for_pages(pages), wd)
    assert list(d["languages"]) == ["en", "it", "de"]
    assert [n["heading"] for n in d["notes"] if n["lang"] == "en"] == ["History", "Provenance", "Iconography"]


def test_a_panel_page_is_read_with_the_altarpiece():
    parts = {"P1": {"sitelinks": {"enwiki": "Panel"}, "count": 9}}
    pages = {("en", "wikipedia"): {"Altar": [("Description", "d")], "Panel": [("Iconography", "i")]}}
    d = art.dossier(drow({"enwiki": "Altar"}, parts), DC, wiki_for_pages(pages), FakeWikidataEntities({"Q1": entity()}))
    assert [n["heading"] for n in d["notes"]] == ["Description", "Panel / Iconography"]


def time_claim(year):
    return {"mainsnak": {"datavalue": {"value": {"time": f"+{year}-00-00T00:00:00Z"}}}}


def test_wikidata_note_names_what_depicts_who_made_it_and_where_it_is():
    ent = entity("Trinity", P180=["Q1"], P170=["Q2"], P195=["Q3"], P276=["Q4"], P144=["Q5"])
    ent["claims"]["P571"] = [time_claim(1411)]
    labels = {"Q1": "Trinity", "Q2": "Rublev", "Q3": "Tretyakov Gallery", "Q4": "Moscow", "Q5": "Genesis 18"}
    note = art.wikidata_note({**ent, "id": "Q9"}, labels.get)
    assert note["heading"] == "Wikidata" and note["lang"] == "en"
    assert note["url"] == "https://www.wikidata.org/wiki/Q9"
    for word in ("Depicts: Trinity", "creator: Rublev", "date: 1411", "collection: Tretyakov Gallery",
                 "location: Moscow", "based on: Genesis 18"):
        assert word in note["text"]


def test_wikidata_note_without_facts_is_none():
    assert art.wikidata_note({"id": "Q1", **entity()}, {}.get) is None


def test_dossier_stage_drops_an_empty_dossier_but_not_calibration():
    wd = FakeWikidataEntities({"Q1": entity(P170=["Q100"]), "Q100": entity("Rublev")})
    pages = wiki_for_pages({("en", "wikipedia"): {"T": []}})
    assert art.dossier_stage(drow({"enwiki": "T"}), DC, pages, wd) == ("drop", {"reason": "empty dossier"})
    assert art.dossier_stage(drow({"enwiki": "T"}, calibration=True), DC, pages, wd)[0] == "pass"


def test_the_dossier_carries_one_wikidata_note():
    wd = FakeWikidataEntities({"Q1": entity(P170=["Q100"]), "Q100": entity("Rublev")})
    d = art.dossier(drow({"enwiki": "T"}), DC, wiki_for_pages({("en", "wikipedia"): {"T": [("History", "h")]}}), wd)
    assert [n["heading"] for n in d["notes"]].count("Wikidata") == 1 and d["languages"] == {"en": 1}


# --- calibration and the benchmark ---

def test_calibration_roots_are_the_visual_arts_and_crafts_without_comics(monkeypatch):
    seen = {}
    monkeypatch.setattr(common, "calibration_qids", lambda wikis, wd, tree, roots: seen.update(tree=tree, roots=roots) or ([], []))
    tree = {"root": {"names": {}}, "themes_root": {"names": {}}, "themes": [],
            "fields": [{"key": "arts", "names": {}, "children": [
                {"key": "visual-arts", "names": {}, "children": [{"key": "comics", "names": {}}, {"key": "painting", "names": {}}]},
                {"key": "crafts", "names": {}}]}]}
    art.calibration_qids({}, wikidata=object(), tree=tree)
    assert seen["roots"] == {"visual-arts", "crafts"}
    assert "comics" not in common.field_keys(seen["tree"], seen["roots"])
    assert "painting" in common.field_keys(seen["tree"], seen["roots"])


def test_benchmark_rows_are_flagged(q):
    sparql = FakeSparql({"VALUES ?item": [item("Q1", "A", 50), item("Q2", "B", 50)]})
    cfg_benchmark = [{"qid": "Q2", "name": "Rublev Trinity", "kind": "strong"}]
    art.add_calibration(q, sparql, ["Q1"], benchmark=cfg_benchmark)
    assert q.row("Q2").calibration and q.row("Q2").data[0]["benchmark"]
    assert q.row("Q2").data[0]["benchmark_kind"] == "strong"
    assert not q.row("Q1").data[0].get("benchmark")


def test_a_benchmark_work_need_not_be_of_a_work_class(q):
    sparql = FakeSparql({"VALUES ?item { wd:Q2 }": [item("Q2", "Chapel", 40, "Q16970")]})
    assert art.add_calibration(q, sparql, [], benchmark=[{"qid": "Q2", "name": "x", "kind": "control"}]) == 1
    assert q.row("Q2").calibration and q.row("Q2").data[0]["benchmark_kind"] == "control"


def test_benchmark_defaults_to_the_config(q, monkeypatch):
    monkeypatch.setattr(art, "load_config", lambda: {"art": {"benchmark": [{"qid": "Q2", "name": "x", "kind": "implicit"}]}})
    art.add_calibration(q, FakeSparql({"VALUES ?item { wd:Q2 }": [item("Q2", "B", 40)]}), [])
    assert q.row("Q2").data[0]["benchmark"]


def test_the_configured_benchmark_is_a_hand_picked_list():
    bench = load_config()["art"]["benchmark"]
    assert 15 <= len(bench) <= 25
    assert len({b["qid"] for b in bench}) == len(bench)
    assert {b["kind"] for b in bench} == {"strong", "implicit", "control"}
    assert all(b["qid"].startswith("Q") and b["name"] for b in bench)


# --- a release survives later runs ---

BENCH = [{"qid": "Q2", "name": "Rublev Trinity", "kind": "strong"}]


def released_queue(q):
    art.add_calibration(q, FakeSparql({"VALUES ?item { wd:Q2 }": [item("Q2", "B", 50)]}), [], benchmark=BENCH)
    for stage in (1, 2):
        q.set_outcome("Q2", stage, "pass", {})
    assert q.release_calibration("Q2")


def test_calibrate_after_a_release_keeps_the_row_released_and_live(q):
    released_queue(q)
    assert art.add_calibration(q, FakeSparql({"VALUES ?item { wd:Q2 }": [item("Q2", "B", 50)]}), [], benchmark=BENCH) == 0
    row = q.row("Q2")
    assert row.calibration is False and not row.data[0].get("benchmark") and row.data[0]["released"]
    assert "Q2" in [r.qid for r in q.pending(1)]


def test_a_release_clears_the_calibration_outcomes_so_the_row_runs_as_a_candidate(q, monkeypatch):
    from pipeline import stages
    released_queue(q)
    row = q.row("Q2")
    assert set(row.outcomes) == {0}
    monkeypatch.setattr(stages, "_local_item", lambda r, repo: "item")
    monkeypatch.setattr(stages, "_title_matches", lambda r, lang, wiki, item: (lang == "en", []))
    outcome, _ = stages.covered(row, type("R", (), {"item_sitelinks": lambda s, i: (None, {"it": 1})})(), {l: None for l in common.LANGS})
    assert outcome == "translation"  # a calibration row would pass


def test_candidates_hold_back_an_unreleased_benchmark_work(q):
    cfg = load_config()
    cfg["art"]["benchmark"] = [{"qid": "Q1", "name": "x", "kind": "strong"}, {"qid": "Q2", "name": "y", "kind": "control"}]
    sparql = FakeSparql({"wdt:P180": [item("Q1", "A", 30, "Q3305213"), item("Q2", "B", 30, "Q3305213"),
                                      item("Q3", "C", 30, "Q3305213")]})
    q.add("Q2", "art", "calibration", {}, {}, data={"released": True})  # already released: left alone
    art.candidates(q, sparql, None, cfg)
    one = q.row("Q1")
    assert one.calibration and one.data[0]["benchmark"] and one.data[0]["benchmark_kind"] == "strong"
    assert q.row("Q3").calibration is False
    assert "Q1" not in [r.qid for r in q.open_rows("art")]
    assert q.row("Q2").calibration is False and "benchmark" not in q.row("Q2").data[0]


# --- calibration and the benchmark leave other rows alone ---

@pytest.mark.parametrize("status", ["open", "approved", "created", "declined"])
def test_calibration_leaves_a_row_with_a_status_alone(q, status, capsys):
    for qid in ("Q1", "Q2"):
        q.add(qid, "art", "depicts", {"en": qid}, {"enwiki": qid}, data={"count": 30})
        q.set_outcome(qid, 2, "drop", {"reason": "x"})
        q.set_status(qid, status)
    sparql = FakeSparql({"VALUES ?item": [item("Q1", "A", 50, "Q3305213"), item("Q2", "B", 50, "Q3305213")]})
    art.add_calibration(q, sparql, ["Q1"], benchmark=[{"qid": "Q2", "name": "x", "kind": "strong"}])
    for qid in ("Q1", "Q2"):
        row = q.row(qid)
        assert row.calibration is False and row.outcomes[2] == "drop" and not row.data[0].get("benchmark")
    assert "already " + status in capsys.readouterr().out


def test_a_benchmark_qid_held_by_another_domain_is_not_held_back(q):
    q.add("Q1", "music", "form", {"en": "A"}, {"enwiki": "A"}, data={"count": 30})
    art._hold_benchmark(q, [{"qid": "Q1", "name": "x", "kind": "strong"}])
    row = q.row("Q1")
    assert row.domain == "music" and row.calibration is False and "benchmark" not in row.data[0]
