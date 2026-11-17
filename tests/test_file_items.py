from tools.file_items import PageFacts, fits, plan_item, rewrite, tree_keys

LANGS = ["en", "it", "es", "fr", "de", "pt"]


def node(key, children=None, item=None):
    n = {"key": key, "item": item or f"Q_{key}", "names": {lang: key.replace("-", " ").title() for lang in LANGS},
         "definition": {"en": key}}
    if children:
        n["children"] = children
    return n


DATA = {
    "root": {"item": "Q_root", "names": {lang: "Root" for lang in LANGS}, "definition": {"en": "all"}},
    "themes_root": {"item": "Q_themes", "names": {lang: "Themes" for lang in LANGS}, "definition": {"en": "t"}},
    "fields": [node("arts", [node("literature", [node("novels")]),
                             node("audiovisual-media", [node("film"), node("television"), node("animation")]),
                             node("music")])],
    "themes": [node("virtues", [node("faith"), node("love"), node("hope")])],
}
TYPES = {"types": {"Q_tv": "tv"}, "fit": {"tv": ["television", "animation"], "book": ["literature"]}}
NS = {lang: ["Category", "Categoria"] for lang in LANGS}
NODE_ITEM = {}


def _items(nodes_):
    for n in nodes_:
        NODE_ITEM[n["key"]] = n["item"]
        _items(n.get("children", []))


_items(DATA["fields"] + DATA["themes"])


def page(lang, cats, text=None, revid=1, title="X"):
    if text is None:
        text = "Intro.\n" + "\n".join(f"[[Category:{c}]]" for c in cats)
    return PageFacts(lang, title, revid, text, set(cats))


def test_tree_keys():
    assert tree_keys("en", {"Film", "Faith", "Virtues", "Root", "Other"}, DATA) == ({"film"}, {"faith"})


def test_union_of_themes_one_field():
    p = plan_item("Q1", [page("en", ["Film", "Faith", "Love"]), page("it", ["Film", "Faith", "Hope"])],
                  {}, "film", DATA, TYPES, NS, NODE_ITEM)
    assert p.stop is None and p.field == "film"
    assert p.themes == ["faith", "love", "hope"]
    assert p.add == [("sv_field", "Q_film"), ("sv_theme", "Q_faith"), ("sv_theme", "Q_love"), ("sv_theme", "Q_hope")]
    assert set(p.edits) == {"en", "it"}


def test_field_disagreement_stops():
    p = plan_item("Q1", [page("en", ["Film"]), page("it", ["Television"])], {}, "work", DATA, TYPES, NS, NODE_ITEM)
    assert p.stop and "en=film" in p.stop and "it=television" in p.stop
    assert p.add == [] and p.edits == {}


def test_two_fields_on_one_page_stop():
    p = plan_item("Q1", [page("en", ["Film", "Music"])], {}, "work", DATA, TYPES, NS, NODE_ITEM)
    assert p.stop and p.add == [] and p.edits == {}


def test_existing_statements_not_added_again():
    p = plan_item("Q1", [page("en", ["Film", "Faith"])], {"sv_field": ["Q_film"], "sv_theme": ["Q_faith"]},
                  "film", DATA, TYPES, NS, NODE_ITEM)
    assert p.add == []


def test_item_with_a_different_field_stops():
    p = plan_item("Q1", [page("en", ["Film"])], {"sv_field": ["Q_music"]}, "work", DATA, TYPES, NS, NODE_ITEM)
    assert p.stop and "music" in p.stop and p.add == []


def test_template_only_category_no_edit():
    p = plan_item("Q1", [page("en", ["Film"], text="{{Infobox film}} Intro.")], {}, "film", DATA, TYPES, NS, NODE_ITEM)
    assert p.add == [("sv_field", "Q_film")] and p.edits == {}


def test_edits_carry_title_text_and_base_revision():
    p = plan_item("Q1", [page("it", ["Film"], text="A\n[[Categoria:Film]]\nB", revid=77, title="Pagina")],
                  {}, "film", DATA, TYPES, NS, NODE_ITEM)
    assert p.edits["it"] == ("Pagina", "A\n{{SV categories}}\nB", 77)


def test_recheck_when_field_does_not_fit_type():
    assert plan_item("Q1", [page("en", ["Film"])], {}, "tv", DATA, TYPES, NS, NODE_ITEM).recheck
    assert not plan_item("Q1", [page("en", ["Television"])], {}, "tv", DATA, TYPES, NS, NODE_ITEM).recheck


def test_field_override_replaces_the_pages_field():
    p = plan_item("Q1", [page("en", ["Film", "Faith"])], {}, "tv", DATA, TYPES, NS, NODE_ITEM,
                  field_override="television")
    assert p.field == "television" and not p.recheck
    assert ("sv_field", "Q_television") in p.add and ("sv_field", "Q_film") not in p.add
    assert "[[Category:Film]]" not in p.edits["en"][1]


def test_rewrite_sortkey_lowercase_and_english_namespace():
    text = "Intro.\n[[categoria:Musica|Buona novella]]\n[[Category:Fede]]\n[[Categoria:Altro]]"
    out = rewrite(text, ["Musica", "Fede"], ["Categoria", "Category"], has_module=False)
    assert out == "Intro.\n{{SV categories|sortkey=Buona novella}}\n[[Categoria:Altro]]"


def test_rewrite_underscores_and_spaces():
    out = rewrite("[[Category: Feature_films ]]", ["Feature films"], ["Category"], has_module=False)
    assert out == "{{SV categories}}"


def test_rewrite_page_with_sv_infobox_gets_no_second_call():
    text = "{{SV infobox}}\nIntro.\n[[Category:Film|Ring]]"
    out = rewrite(text, ["Film"], ["Category"], has_module=True)
    assert out == "{{SV infobox|sortkey=Ring}}\nIntro."
    keep = rewrite("{{SV infobox|sortkey=Z}}\n[[Category:Film|Ring]]", ["Film"], ["Category"], has_module=True)
    assert keep == "{{SV infobox|sortkey=Z}}"


def test_rewrite_nothing_to_remove_is_unchanged():
    assert rewrite("Intro.", ["Film"], ["Category"], has_module=False) == "Intro."


def test_fits():
    assert fits("television", "tv", DATA, TYPES)
    assert not fits("film", "tv", DATA, TYPES)
    assert fits("film", "person", DATA, TYPES)
    assert fits("novels", "book", DATA, TYPES)
    assert fits("animation", "tv", DATA, TYPES)
