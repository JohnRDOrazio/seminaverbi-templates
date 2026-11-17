import json

from conftest import ROOT
from lua_env import LuaEnv
from sync.pages import compile_data

LANGS = ["en", "it", "es", "fr", "de", "pt"]
REQUIRED = [
    "infobox_missing_category", "bible_note", "bible_unsupported", "bible_missing_ref",
    "discernment_heading", "references_heading", "wip_notice", "wip_category", "managed_notice",
    "cite_accessed", "cite_archived", "cite_pages", "cite_missing_title", "bc_year",
    "date_decade", "date_century", "date_millennium", "cattree_themes_heading",
    "quote_missing_text", "cathopedia_note", "cathopedia_category", "cathopedia_missing_title",
]


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_compile_is_stable():
    assert compile_data(ROOT) == compile_data(ROOT)


def test_every_lang_has_required_keys():
    for lang in LANGS:
        strings = load(f"i18n/{lang}.json")
        missing = [k for k in REQUIRED if not strings.get(k)]
        assert missing == [], f"{lang} lacks {missing}"


def test_infobox_props_resolve():
    props = load("config/wikibase.json")
    for kind, fields in load("config/infobox.json").items():
        for field in fields:
            assert field["prop"] in props, f"{kind}.{field['key']}"


def test_bible_defaults_cover_all_wikis():
    bible = load("config/bible.json")
    assert sorted(bible["defaults"]) == sorted(LANGS)
    assert all(v in bible["versions"] for v in bible["defaults"].values())
    assert sorted(bible["version_lang"]) == sorted(bible["versions"])


def test_data_loads_in_lua():
    data = LuaEnv().require("Module:SV/Data")
    assert data.bible.defaults.fr == "NVBSE"
    assert data.wikibase.author == "P3990"
    assert data.i18n.it.discernment_heading == "Semi del Verbo e punti di attenzione"


def test_categories_compiled():
    data = LuaEnv().require("Module:SV/Data")
    assert data.categories.root.names.en == "Seeds of the Word"  # the existing en root category


def test_infobox_types_resolve():
    from sync.categories import nodes
    types = json.loads((ROOT / "config" / "infobox_types.json").read_text(encoding="utf-8"))
    layouts = json.loads((ROOT / "config" / "infobox.json").read_text(encoding="utf-8"))
    keys = {n.key for n in nodes(json.loads((ROOT / "config" / "categories.json").read_text(encoding="utf-8")))}
    assert set(types["types"].values()) <= set(layouts)
    assert set(types["fit"]) <= set(layouts)
    assert {k for ks in types["fit"].values() for k in ks} <= keys
    assert {"work", "group"} <= set(layouts)


SURVEY = {
    "en": ["Infobox song", "Infobox television", "Infobox film", "Infobox album", "Infobox person",
           "Infobox musical artist", "Infobox stage production", "Infobox saint", "Infobox food",
           "Infobox musical composition", "Infobox scientist", "Infobox comics species and title",
           "Infobox software", "Infobox file format", "Infobox television episode", "Infobox book series"],
    "it": ["Album", "Artista musicale", "Brano musicale", "FictionTV", "Film", "Personaggio", "Libro",
           "Dramma", "Opera d'arte"],
}


def test_infobox_import_resolves():
    imp = json.loads((ROOT / "config" / "infobox_import.json").read_text(encoding="utf-8"))
    layouts = json.loads((ROOT / "config" / "infobox.json").read_text(encoding="utf-8"))
    rows = {name: {r["key"] for r in rs} for name, rs in layouts.items()}
    for lang, names in SURVEY.items():
        for name in names:
            assert name in imp["templates"][lang], f"{lang}:{name}"
    for lang, tpls in imp["templates"].items():
        for name, spec in tpls.items():
            assert spec["layout"] in layouts, f"{lang}:{name}"
            assert set(spec["params"].values()) <= rows[spec["layout"]], f"{lang}:{name}"
    assert {"Film date", "Album ratings", "Album reviews", "Bandiera"} <= set(imp["helpers"])
    assert set(imp["kinds"]) <= set(layouts)
    assert all(q.startswith("Q") for qs in imp["kinds"].values() for q in qs)
    bio = imp["bio"]["it"]
    assert bio["template"] == "Bio"
    assert set(bio["facts"].values()) <= rows["person"] | {"given_name", "nationality"}
    assert set(bio["free"].values()) <= {"note", "rest", "ref"}
