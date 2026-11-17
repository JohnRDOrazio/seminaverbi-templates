import json

from conftest import ROOT
from sync.categories import field_children, leaf_themes, load, nodes

LANGS = ["en", "it", "es", "fr", "de", "pt"]
SEED_THEMES = {"charity", "forgiveness", "faith", "hope", "redemption", "conversion",
               "repentance", "conscience", "family"}
TYPO_FIXES = {"Hitory": "History", "Geografy": "Geography"}
# Main-page categories whose name changes or that leave the tree; replaced by the migration map in Task 4.
RENAMED = {
    "en": {"Sculpting", "Medicinal science", "Video gaming", "Formal sciences", "Drama (Literature)",
           "Fiction (Literature)", "Poetry (Literature)", "Prose (Literature)", "Black and White photography",
           "Fresco painting", "Watercolor painting", "Archeology", "Mosaic art", "Literary arts",
           "Cinematography", "Sitcom", "Feature film", "Short film"},
    "it": {"Scienze formali", "Opera Drammatica (Letteratura)", "Narrativa (Letteratura)",
           "Poesia (Letteratura)", "Romanzo (Letteratura)", "Economia (scienza)", "Politica (Scienza)",
           "Arti vitree", "Arte del mosaico", "Arti letterarie", "Bandiere"},
}
TOP = ["arts", "humanities", "social-sciences", "natural-sciences", "engineering-technology",
       "medical-health-sciences", "religion", "popular-culture"]
THEME_GROUPS = ["theological-virtues", "cardinal-virtues", "sin-grace-salvation", "human-person",
                "communion-society", "christ-church"]


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
        expected = {TYPO_FIXES.get(x, x) for x in fixture[lang]} - RENAMED[lang]
        assert expected - names == set(), lang


def test_seed_themes():
    themes = {t.key: t.names for t in leaf_themes(load(ROOT))}
    assert SEED_THEMES <= set(themes)
    assert themes["forgiveness"]["en"] == "Forgiveness" and themes["forgiveness"]["it"] == "Perdono"
    assert themes["charity"]["en"] == "Charity" and themes["charity"]["it"] == "Carità"
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
    for group in data["themes"]:
        assert by_key[group["key"]][2] == "themes-root"
        for theme in group["children"]:
            assert by_key[theme["key"]][2] == group["key"]


def test_order_root_fields_themes():
    keys = [n[0] for n in all_nodes()]
    data = load(ROOT)
    assert keys[0] == "root"
    assert keys.index("themes-root") > keys.index(data["fields"][-1]["key"])
    assert keys[-1] == data["themes"][-1]["children"][-1]["key"]


def parent_of(key):
    return {n.key: n.parent_key for n in all_nodes()}[key]


def test_every_node_has_english_definition():
    for key, _names, _parent, definition in all_nodes():
        en = definition.get("en", "").strip()
        assert en and len(en) <= 300, key


def test_tree_size():
    data = load(ROOT)
    fields = [n for n in all_nodes() if n.key not in ("root", "themes-root")
              and n.key not in {t.key for t in leaf_themes(data)} and n.key not in THEME_GROUPS]
    assert 100 <= len(fields) <= 200, len(fields)


def test_top_level_fields():
    assert [n.key for n in field_children(load(ROOT), None)] == TOP


def test_no_formal_sciences():
    keys = {n.key for n in all_nodes()}
    assert "formal-sciences" not in keys

    def under(key, top):
        while key and key != top:
            key = parent_of(key)
        return key == top

    for key in ("history", "philosophy", "theology"):
        assert under(key, "humanities"), key
    for key in ("mathematics", "computer-science"):
        assert under(key, "natural-sciences"), key
    assert under("medicine", "medical-health-sciences")


def test_renames():
    en = {n.names["en"] for n in all_nodes()}
    assert {"Sculpture", "Medicine", "Video games"} <= en
    assert not en & {"Sculpting", "Medicinal science", "Video gaming"}


def test_theme_groups():
    data = load(ROOT)
    assert [g["key"] for g in data["themes"]] == THEME_GROUPS
    for theme in leaf_themes(data):
        assert theme.parent_key in THEME_GROUPS


def test_leaf_themes_excludes_groups():
    keys = {t.key for t in leaf_themes(load(ROOT))}
    assert not keys & set(THEME_GROUPS) and "themes-root" not in keys


def test_every_node_has_an_item():
    import re
    data = load(ROOT)
    dicts = [data["root"], data["themes_root"]]

    def walk(items):
        for item in items:
            dicts.append(item)
            walk(item.get("children", []))
    walk(data["fields"])
    walk(data["themes"])
    ids = [d.get("item", "") for d in dicts]
    assert all(re.fullmatch(r"Q\d+", i) for i in ids), [d.get("key") for d in dicts if "item" not in d][:5]
    assert len(set(ids)) == len(ids)
