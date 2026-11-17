import json

from conftest import ROOT
from lua_env import LuaEnv, tag

DATA = json.loads((ROOT / "config" / "categories.json").read_text(encoding="utf-8"))
I18N = {lang: json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in ("en", "it", "fr")}


def tree(lang="en"):
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/Categories", "tree", env.frame({}, {}))


def link(name):
    return f"[[:Category:{name}|{name}]]"


def field(key):
    def find(items):
        for item in items:
            if item["key"] == key:
                return item
            hit = find(item.get("children", []))
            if hit:
                return hit
    return find(DATA["fields"])


def test_tree_styles():
    assert tree().startswith(tag("templatestyles", src="SV category tree/styles.css"))


def test_tree_top_level_links_localized():
    assert link("Arti letterarie") in tree("it")
    assert link(field("literary-arts")["names"]["fr"]) in tree("fr")


def test_tree_nesting():
    out = tree()
    start = out.index('<div class="sv-cattree-node mw-collapsible">' + "\n" + '<div class="sv-cattree-title">'
                      + link("Visual arts"))
    end = out.index(link("Formal sciences"))
    block = out[start:end]
    assert link("Painting") in block and link("Fresco painting") in block
    assert block.index(link("Painting")) < block.index(link("Fresco painting"))
    # a leaf is a list item, not a collapsible block
    assert "* " + link("Dance") in out
    assert '<div class="sv-cattree-title">' + link("Dance") not in out


def test_tree_themes_heading_last():
    out = tree("it")
    heading = '<div class="sv-cattree-themes-heading">'
    assert out.count(heading) == 1
    assert I18N["it"]["cattree_themes_heading"] + "]]</div>" in out
    last_field = link(field("flags")["names"]["it"])
    assert out.index(last_field) < out.index(heading)
    for theme in DATA["themes"]:
        assert out.index(heading) < out.index(link(theme["names"]["it"]))


def test_tree_parentheses():
    assert link("Drama (Literature)") in tree()
