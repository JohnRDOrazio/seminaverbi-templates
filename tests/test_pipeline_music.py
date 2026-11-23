from pipeline.domains import DOMAINS, common, music
from pipeline.queue import Row
from test_pipeline_film import FakeSparql

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
