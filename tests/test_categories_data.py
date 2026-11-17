import json

from conftest import ROOT
from sync.categories import load, nodes

LANGS = ["en", "it", "es", "fr", "de", "pt"]
SEED_THEMES = {"love", "forgiveness", "faith", "hope", "virtue", "redemption", "conversion",
               "repentance", "conscience", "family"}
TYPO_FIXES = {"Hitory": "History", "Geografy": "Geography"}


def all_nodes():
    return nodes(load(ROOT))


def test_every_node_has_six_names():
    for key, names, _parent, _desc in all_nodes():
        missing = [lang for lang in LANGS if not names.get(lang, "").strip()]
        assert missing == [], f"{key} lacks {missing}"


def test_keys_unique():
    keys = [n[0] for n in all_nodes()]
    assert len(keys) == len(set(keys))


def test_names_unique_per_language():
    for lang in LANGS:
        names = [n[1][lang] for n in all_nodes()]
        dupes = sorted({x for x in names if names.count(x) > 1})
        assert dupes == [], f"{lang}: {dupes}"


def test_no_typo_names():
    en = {n[1]["en"] for n in all_nodes()}
    assert not en & set(TYPO_FIXES)
    assert set(TYPO_FIXES.values()) <= en


def test_covers_main_page():
    fixture = json.loads((ROOT / "tests" / "fixtures" / "mainpage_categories.json").read_text(encoding="utf-8"))
    for lang in ("en", "it"):
        names = {n[1][lang] for n in all_nodes()}
        expected = {TYPO_FIXES.get(x, x) for x in fixture[lang]}
        assert expected - names == set(), lang


def test_seed_themes():
    data = load(ROOT)
    themes = {t["key"]: t["names"] for t in data["themes"]}
    assert SEED_THEMES <= set(themes)
    assert themes["forgiveness"]["en"] == "Forgiveness" and themes["forgiveness"]["it"] == "Perdono"
    assert themes["repentance"]["en"] == "Repentance"
    assert themes["conscience"]["en"] == "Conscience" and themes["conscience"]["it"] == "Coscienza"
    assert themes["family"]["it"] == "Famiglia"


def test_parents():
    data = load(ROOT)
    by_key = {n[0]: n for n in all_nodes()}
    assert by_key["root"][2] is None
    assert by_key["themes-root"][2] == "root"
    for field in data["fields"]:
        assert by_key[field["key"]][2] == "root"
    for theme in data["themes"]:
        assert by_key[theme["key"]][2] == "themes-root"


def test_order_root_fields_themes():
    keys = [n[0] for n in all_nodes()]
    data = load(ROOT)
    assert keys[0] == "root"
    assert keys.index("themes-root") > keys.index(data["fields"][-1]["key"])
    assert keys[-1] == data["themes"][-1]["key"]
