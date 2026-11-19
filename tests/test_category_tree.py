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
    assert link("Arti visive") in tree("it")
    assert link(field("literature")["names"]["fr"]) in tree("fr")


def test_tree_nesting():
    out = tree()
    start = out.index('<div class="sv-cattree-node mw-collapsible mw-collapsed">' + "\n"
                      + '<div class="sv-cattree-title">' + link("Visual arts"))
    end = out.index(link("Crafts and decorative arts"))
    block = out[start:end]
    assert link("Painting") in block and link("Fresco") in block
    assert block.index(link("Painting")) < block.index(link("Fresco"))
    # a leaf is a list item, not a collapsible block
    assert '* <span class="sv-cattree-empty">' + link("Dance") in out
    assert '<div class="sv-cattree-title">' + link("Dance") not in out


def test_tree_themes_heading_last():
    out = tree("it")
    heading = '<div class="sv-cattree-themes-heading">'
    assert out.count(heading) == 1
    assert I18N["it"]["cattree_themes_heading"] + "]]</div>" in out
    last = DATA["fields"][-1]
    while last.get("children"):
        last = last["children"][-1]
    last_field = link(last["names"]["it"])
    assert out.index(last_field) < out.index(heading)
    for group in DATA["themes"]:
        for theme in group["children"]:
            assert out.index(heading) < out.index(link(theme["names"]["it"]))


def test_tree_apostrophe_and_accents():
    assert link("Children's literature") in tree()
    assert link("Peinture à l'huile") in tree("fr")


def test_tree_theme_groups_hold_their_themes():
    out = tree()
    group = DATA["themes"][0]
    start = out.index('<div class="sv-cattree-group-title">' + link(group["names"]["en"]))
    leaf = group["children"][0]["names"]["en"]
    assert out.index('<span class="sv-cattree-chip">', start) < out.index(link(leaf), start)


# --- cards, chips and article counts ---

def leaves(items):
    for item in items:
        if item.get("children"):
            yield from leaves(item["children"])
        else:
            yield item


def counted(lang="en", counts=None):
    env = LuaEnv(lang=lang, category_counts=counts or {})
    return env.call("Module:SV/Categories", "tree", env.frame({}, {})), env


def test_each_top_level_field_is_a_card():
    out = tree()
    assert out.count('<div class="sv-cattree-card">') == len(DATA["fields"])
    assert '<div class="sv-cattree-card-title">' + link("Arts") in out


def test_branches_below_a_card_start_collapsed():
    out = tree()
    assert ('<div class="sv-cattree-node mw-collapsible mw-collapsed">' + "\n"
            + '<div class="sv-cattree-title">' + link("Visual arts")) in out


def test_themes_are_chips_grouped_by_their_theme_group():
    out = tree()
    group = DATA["themes"][0]
    start = out.index('<div class="sv-cattree-group-title">' + link(group["names"]["en"]))
    chip = out.index('<span class="sv-cattree-chip">', start)
    assert chip < out.index(link(group["children"][0]["names"]["en"]), start)


def nodes(items):
    for item in items:
        yield item
        yield from nodes(item.get("children", []))


def test_every_field_category_and_every_theme_is_counted():
    _, env = counted()
    names = [name for name, _ in env.category_calls]
    expected = [n["names"]["en"] for n in nodes(DATA["fields"])] + \
               [t["names"]["en"] for g in DATA["themes"] for t in g["children"]]
    assert sorted(names) == sorted(expected)
    assert all(which == "pages" for _, which in env.category_calls)
    assert len(names) <= 300  # $wgExpensiveParserFunctionLimit on the farm


def test_a_branch_counts_the_articles_filed_directly_under_it():
    out, _ = counted(counts={"Painting": 3, "Fresco": 2})
    assert link("Painting") + ' <span class="sv-cattree-count">5</span>' in out


def test_a_failed_lookup_is_unknown_not_zero():
    env = LuaEnv(category_counts={"Fresco": 2, "Dance": 1})
    env.lua.execute("""
        local real = mw.site.stats.pagesInCategory
        mw.site.stats.pagesInCategory = function(name, which)
            if name == 'Oil painting' then error('Too many expensive function calls') end
            return real(name, which)
        end""")
    out = env.call("Module:SV/Categories", "tree", env.frame({}, {}))
    # the failed leaf: no badge and not dimmed; its ancestors: no (incomplete) total
    assert "* " + link("Oil painting") + "\n" in out
    assert '<span class="sv-cattree-empty">' + link("Oil painting") not in out
    assert '<div class="sv-cattree-title">' + link("Painting") + "</div>" in out
    assert '<div class="sv-cattree-card-title">' + link("Arts") + "</div>" in out
    # a sibling that was counted keeps its badge
    assert link("Fresco") + ' <span class="sv-cattree-count">2</span>' in out


def test_an_empty_theme_chip_is_dimmed():
    theme = DATA["themes"][0]["children"][0]["names"]["en"]
    out, _ = counted()
    assert '<span class="sv-cattree-chip"><span class="sv-cattree-empty">' + link(theme) + "</span></span>" in out


def test_a_leaf_shows_its_count_and_a_branch_the_sum_of_its_leaves():
    out, _ = counted(counts={"Fresco": 2, "Oil painting": 5, "Dance": 1})
    assert "* " + link("Dance") + ' <span class="sv-cattree-count">1</span>' in out
    assert link("Painting") + ' <span class="sv-cattree-count">7</span>' in out
    assert link("Visual arts") + ' <span class="sv-cattree-count">7</span>' in out
    assert link("Arts") + ' <span class="sv-cattree-count">8</span>' in out


def test_an_empty_category_has_no_count_and_is_dimmed():
    out, _ = counted()
    assert '<span class="sv-cattree-count">' not in out
    assert '<span class="sv-cattree-empty">' + link("Dance") + "</span>" in out


def test_a_theme_chip_shows_its_count():
    theme = DATA["themes"][0]["children"][0]["names"]["en"]
    out, _ = counted(counts={theme: 4})
    assert '<span class="sv-cattree-chip">' + link(theme) + ' <span class="sv-cattree-count">4</span></span>' in out


def test_the_tree_survives_the_expensive_function_limit():
    env = LuaEnv(category_counts={"Dance": 1, "Fresco": 2})
    env.lua.execute("""
        local real, calls = mw.site.stats.pagesInCategory, 0
        mw.site.stats.pagesInCategory = function(...)
            calls = calls + 1
            if calls > 1 then error('Too many expensive function calls') end
            return real(...)
        end""")
    out = env.call("Module:SV/Categories", "tree", env.frame({}, {}))
    assert link("Arts") in out and '<span class="sv-cattree-chip">' in out
