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
