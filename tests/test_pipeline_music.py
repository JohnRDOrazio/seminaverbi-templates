import pytest

from pipeline import load_config
from pipeline.domains import DOMAINS, common, music
from pipeline.queue import Queue, Row
from test_pipeline_film import (FakeSparql, FakeWikidataEntities, claim, entity, item, wiki_for_pages)

CFG = {"sitelink_floor": 10, "artist_sitelink_floor": 20}


def mrow(sources, count, instance_of=("Q7366",), calibration=False, **data):
    return Row("Q1", "music", sources, {}, {}, calibration,
               data={0: {"count": count, "instance_of": list(instance_of), **data}})


def test_registry_has_music():
    assert DOMAINS["music"] is music


def test_floor_for_forms_and_a_higher_one_for_artist_only_rows():
    assert music.triage(mrow(["form"], 10), CFG)[0] == "pass"
    assert music.triage(mrow(["form"], 9), CFG) == ("drop", "9 sitelinks, under the floor of 10")
    assert music.triage(mrow(["artist"], 19), CFG) == ("drop", "19 sitelinks, under the floor of 20")
    assert music.triage(mrow(["artist", "genre"], 12), CFG)[0] == "pass"


def test_people_and_folded_parts_are_dropped():
    assert music.triage(mrow(["artist"], 90, ["Q5"]), CFG) == ("drop", "a person, not a work")
    assert music.triage(mrow(["artist"], 90, ["Q215380"]), CFG) == ("drop", "a person, not a work")
    assert music.triage(mrow(["form"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")


def test_recordings_are_dropped():
    assert music.triage(mrow(["form"], 40, ["Q3302947"]), CFG) == (
        "drop", "a recording or edition, not a work of its own")


def test_calibration_always_passes():
    assert music.triage(mrow(["calibration"], 1, calibration=True), CFG) == ("pass", "calibration")
    assert music.triage(mrow(["calibration"], 1, ["Q5"], calibration=True, folded_into="Q9"), CFG)[0] == "pass"


def test_owns_uses_the_music_classes(tmp_path):
    sparql = FakeSparql({"wd:Q7366": [{"sub": "Q7366"}]})
    assert music.owns(["Q7366"], sparql, tmp_path / "c.json")


def test_contract_constants():
    assert music.WORK_CLASSES[:4] == ("Q105543609", "Q7366", "Q482994", "Q484692")
    assert "Q134556" in music.WORK_CLASSES
    assert music.FOLD_WHOLES == ("Q105543609",) and music.FOLD_STOP == ("Q482994",)
    assert music.covered is common.covered_with_parts


def test_wording_has_the_four_connections():
    assert set(music.QUESTIONS["connections"]) == {"direct", "seeds", "entertainment", "none"}
    assert "song, verse or movement" in music.QUESTIONS["connection"]
    assert "song, verse or movement" in music.QUESTIONS["connections"]["seeds"]


def test_a_work_class_shields_a_row_from_the_recording_and_group_drops():
    assert music.triage(mrow(["form"], 40, ["Q7366", "Q3302947"]), CFG)[0] == "pass"
    assert music.triage(mrow(["form"], 40, ["Q3302947"]), CFG)[0] == "drop"
    assert music.triage(mrow(["form"], 40, ["Q482994", "Q215380"]), CFG)[0] == "pass"
    assert music.triage(mrow(["form"], 40, ["Q7366", "Q5"]), CFG) == ("drop", "a person, not a work")


# --- stage 0: candidates ---

@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CLOSURE_CACHE", tmp_path / "closure.json")
    return Queue(tmp_path / "q.db")


def test_each_source_tags_its_rows(q):
    sparql = FakeSparql({"wdt:P7937": [item("Q1", "A", 30, "Q7366")], "wdt:P136 ?genre": [item("Q2", "B", 30, "Q7366")],
                         "wdt:P175|wdt:P86|wdt:P676": [item("Q3", "C", 30, "Q7366")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q1").sources == ["form"] and q.row("Q2").sources == ["genre"] and q.row("Q3").sources == ["artist"]
    assert q.row("Q1").domain == "music"


def test_facet_rows_are_tagged_and_genres_are_not_repeated(q):
    sparql = FakeSparql({"wdt:P921|wdt:P144": [item("Q4", "D", 30, "Q7366")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q4").sources == ["facet"]
    assert not any("wdt:P136 ?genre" in x and "wdt:P921" in x for x in sparql.queries)


def test_no_subclass_path_in_candidate_queries(q):
    sparql = FakeSparql()
    music.candidates(q, sparql, None, load_config())
    assert any("wikibase:sitelinks" in x for x in sparql.queries)
    assert not any("wdt:P279*" in x for x in sparql.queries if "?sub" not in x)
    assert all("LIMIT 500" in x for x in sparql.queries if "wikibase:sitelinks" in x)


def test_a_song_does_not_fold_into_its_album(q):
    sparql = FakeSparql({"wdt:P7937": [item("Q10", "Song", 30, "Q7366")]})
    music.candidates(q, sparql, None, load_config())
    folds = [x for x in sparql.queries if "wdt:P361" in x]
    assert folds and all("FILTER NOT EXISTS" in x and "wd:Q482994" in x for x in folds)
    assert q.row("Q10") is not None and q.row("Q20") is None


def test_a_part_folds_into_its_composition(q):
    sparql = FakeSparql({"wdt:P7937": [item("Q10", "Kyrie", 30, "Q7366")],
                         "wdt:P361": [{"item": "Q10", "whole": "Q1"}],
                         "VALUES ?item { wd:Q1 }": [item("Q1", "Mass", 40, "Q105543609")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q1").sources == ["form"] and set(q.row("Q1").data[0]["parts"]) == {"Q10"}


def test_people_are_reported_not_queued(q, capsys):
    sparql = FakeSparql({"wdt:P7937": [item("Q5x", "P", 90, "Q5")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q5x") is None and "person" in capsys.readouterr().out


def test_groups_are_skipped_but_a_work_that_is_also_a_group_class_stays(q):
    sparql = FakeSparql({"wdt:P7937": [item("Q6", "Band", 90, "Q215380"), item("Q7", "Odd", 90, "Q7366 Q215380")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q6") is None and q.row("Q7") is not None


def test_an_item_of_another_domain_is_reported_and_left(q, capsys):
    q.add("Q8", "literature", "author", {}, {})
    sparql = FakeSparql({"wdt:P7937": [item("Q8", "Libretto", 90, "Q7366")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q8").domain == "literature" and "literature" in capsys.readouterr().out


# --- stage 4: the dossier ---

DC = {"dossier_languages": 3}


def drow(sitelinks, parts=None, calibration=False):
    data = {"count": 30, "instance_of": ["Q105543609"]}
    if parts is not None:
        data["parts"] = parts
    return Row("Q1", "music", ["form"], {}, sitelinks, calibration, data={0: data})


def test_dossier_reads_the_wanted_sections_and_the_parts():
    parts = {"P1": {"sitelinks": {"enwiki": "Kyrie"}, "count": 9}}
    pages = {("en", "wikipedia"): {"Mass": [("Background", "b"), ("Lyrics", "l"), ("Track listing", "t")],
                                   "Kyrie": [("Structure", "s"), ("Personnel", "x")], "#lead:Kyrie": "kyrie lead"}}
    d = music.dossier(drow({"enwiki": "Mass"}, parts), DC, wiki_for_pages(pages), FakeWikidataEntities({"Q1": entity()}))
    assert [n["heading"] for n in d["notes"]] == ["Background", "Lyrics", "Kyrie", "Kyrie / Structure"]
    assert d["languages"] == {"en": 4}


def test_a_whole_with_no_stored_parts_reads_its_parts_from_wikidata():
    wd = FakeWikidataEntities({"Q1": entity(P527=["P1"]), "P1": entity(sitelinks={"enwiki": "Kyrie"})})
    pages = {("en", "wikipedia"): {"Mass": [], "Kyrie": [("Music", "m")]}}
    d = music.dossier(drow({"enwiki": "Mass"}), DC, wiki_for_pages(pages), wd)
    assert [n["heading"] for n in d["notes"]] == ["Kyrie / Music"]


def test_the_original_language_comes_first():
    wd = FakeWikidataEntities({"Q1": entity(P407=["Q188"])})
    pages = {("en", "wikipedia"): {"M": [("Background", "e")]}, ("de", "wikipedia"): {"M de": [("Hintergrund", "d")]}}
    d = music.dossier(drow({"enwiki": "M", "dewiki": "M de"}), DC, wiki_for_pages(pages), wd)
    assert list(d["languages"]) == ["de", "en"]


def test_every_language_has_its_headings():
    assert set(music.HEADINGS) == set(common.LANGS)
    assert all(len(words) == len(music.HEADINGS["en"]) or len(words) >= 10 for words in music.HEADINGS.values())


def time_claim(year):
    return {"mainsnak": {"datavalue": {"value": {"time": f"+{year}-00-00T00:00:00Z"}}}}


def test_wikidata_note_names_the_psalm_it_sets():
    ent = entity("Miserere", P86=["Q100"], P144=["Q200"], P676=["Q300"], P175=["Q400"])
    ent["claims"]["P577"] = [time_claim(1638)]
    labels = {"Q100": "Allegri", "Q200": "Psalm 51", "Q300": "Anon", "Q400": "Choir"}
    note = music.wikidata_note({**ent, "id": "Q1"}, labels.get, is_bible_book=lambda qid: qid == "Q200")
    assert note["lang"] == "en" and note["heading"] == "Wikidata"
    assert note["url"] == "https://www.wikidata.org/wiki/Q1"
    for word in ("Allegri", "Anon", "Choir", "1638", "Psalm 51", "a book of the Bible"):
        assert word in note["text"]


def test_wikidata_note_without_facts_is_none():
    assert music.wikidata_note({"id": "Q1", **entity()}, {}.get) is None


def test_the_dossier_carries_one_wikidata_note_and_no_lyrics():
    ent = {"id": "Q1", **entity(P86=["Q100"], P921=["Q200"])}
    wd = FakeWikidataEntities({"Q1": ent, "Q100": entity("Allegri"), "Q200": entity("Gospel of John", P31=["Q29154430"])})
    d = music.dossier(drow({"enwiki": "M", "itwiki": "M it"}),
                      DC, wiki_for_pages({("en", "wikipedia"): {"M": [("Lyrics", "l")]},
                                          ("it", "wikipedia"): {"M it": [("Testo", "t")]}}), wd)
    wikidata_notes = [n for n in d["notes"] if n["heading"] == "Wikidata"]
    assert len(wikidata_notes) == 1 and "a book of the Bible" in wikidata_notes[0]["text"]
    assert d["languages"] == {"en": 1, "it": 1}


def test_dossier_stage_drops_an_empty_dossier_but_not_calibration():
    wd = FakeWikidataEntities({"Q1": entity(P86=["Q100"]), "Q100": entity("Bach")})
    pages = wiki_for_pages({("en", "wikipedia"): {"M": []}})
    assert music.dossier_stage(drow({"enwiki": "M"}), DC, pages, wd) == ("drop", {"reason": "empty dossier"})
    assert music.dossier_stage(drow({"enwiki": "M"}, calibration=True), DC, pages, wd)[0] == "pass"


def test_wikiquote_keeps_sections_naming_the_composer():
    wd = FakeWikidataEntities({"Q1": entity(P86=["Q100"]), "Q100": entity("Bach")})
    pages = {("en", "wikipedia"): {"M": []}, ("en", "wikiquote"): {"M": [("Bach", "q"), ("Other", "n")]}}
    d = music.dossier(drow({"enwiki": "M"}), DC, wiki_for_pages(pages), wd)
    assert [n["heading"] for n in d["notes"] if n["heading"] != "Wikidata"] == ["Bach"]


# --- calibration ---

def test_calibration_skips_people(q, capsys):
    sparql = FakeSparql({"VALUES ?item": [item("Q1", "Song", 20, "Q7366")]})  # Q2 (Bach) answers to no work class
    assert music.add_calibration(q, sparql, ["Q1", "Q2"]) == 1
    assert q.row("Q1").calibration and q.row("Q1").sources == ["calibration"] and q.row("Q2") is None
    assert "music calibration: Q2 skipped (a person or group)" in capsys.readouterr().out


def test_calibration_skips_a_person_the_query_returns(q, capsys):
    sparql = FakeSparql({"VALUES ?item": [item("Q2", "Bach", 90, "Q5")]})
    assert music.add_calibration(q, sparql, ["Q2"]) == 0
    assert "Q2 skipped" in capsys.readouterr().out


def test_calibration_roots_are_music_theatre_and_opera(monkeypatch):
    seen = {}
    monkeypatch.setattr(common, "calibration_qids", lambda wikis, wd, tree, roots: seen.update(roots=roots) or ([], []))
    music.calibration_qids({}, wikidata=object(), tree={})
    assert seen["roots"] == {"music", "musical-theatre", "opera"}
