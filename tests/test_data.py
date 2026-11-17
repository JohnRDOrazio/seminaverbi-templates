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
