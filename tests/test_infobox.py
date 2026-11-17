from lua_env import LuaEnv, tag


def item(qid):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}}}}


def time(iso, precision):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": iso, "precision": precision}}}}


def text(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": value}}}


def mono(t, lang):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "monolingualtext", "value": {"text": t, "language": lang}}}}


LOTR = {
    "labels": {"en": "The Lord of the Rings", "it": "Il Signore degli Anelli"},
    "sitelinks": {"it": "Il Signore degli Anelli", "itwiki": "Il Signore degli Anelli"},
    "claims": {
        "P725": [text("El Señor de los Anillos lectura.jpg")],
        "P458": [mono("The Lord of the Rings", "en"), mono("Il Signore degli Anelli", "it")],
        "P3990": [item("Q10")],
        "P4788": [time("+1954-07-29T00:00:00Z", 11)],
        "P501": [item("Q11")],
    },
}
ENTITIES = {
    "Q4178": LOTR,
    "Q10": {"labels": {"en": "J. R. R. Tolkien", "it": "J. R. R. Tolkien"}, "sitelinks": {"itwiki": "J. R. R. Tolkien"}},
    "Q11": {"labels": {"it": "Il Silmarillion"}, "sitelinks": {"it": "Il Silmarillion"}},
    "P3990": {"labels": {"en": "author", "it": "autore"}},
    "P4788": {"labels": {"en": "publication date", "it": "data di pubblicazione"}},
    "P501": {"labels": {"en": "followed by", "it": "seguito da"}},
}


def render(kind="work", lang="it", page_entity="Q4178", entities=ENTITIES, **args):
    env = LuaEnv(lang=lang, entities=entities, page_entity=page_entity, page_title="Pagina")
    ctx = env.lua.table_from({"lang": lang, "entity_id": page_entity, "frame": env.frame()})
    return env.call("Module:SV/Infobox", "_render", kind, env.lua.table_from(args), ctx)


def row(label, value):
    return f"<tr><th>{label}</th><td>{value}</td></tr>"


def test_book_from_item():
    out = render()
    assert '<table class="sv-infobox sv-infobox-book">' in out
    assert row("autore", "[[wikipedia:it:J. R. R. Tolkien|J. R. R. Tolkien]]") in out
    assert "[[File:El Señor de los Anillos lectura.jpg|frameless|upright=1.15]]" in out
    assert "''The Lord of the Rings''" in out
    assert "Category:" not in out


def test_local_sitelink_wins():
    assert row("seguito da", "[[Il Silmarillion|Il Silmarillion]]") in render()


def test_param_overrides_item():
    out = render(author="[[Tolkien]]")
    assert row("autore", "[[Tolkien]]") in out
    assert "wikipedia:it:J. R. R. Tolkien" not in out


def test_time_precision():
    assert row("data di pubblicazione", "j F Y|1954-07-29") in render()
    year = dict(ENTITIES, Q4178=dict(LOTR, claims={"P4788": [time("+1954-00-00T00:00:00Z", 9)]}))
    assert row("data di pubblicazione", "1954") in render(entities=year)


def test_no_item_still_renders():
    out = render(page_entity=None, entities={}, name="X", genre="fantasy")
    assert "X" in out and row("genre", "fantasy") in out
    assert "[[Category:Infobox SV con dati mancanti]]" in out


def test_empty_item_gets_category():
    out = render(entities={"Q4178": {"labels": {"it": "Vuoto"}, "claims": {}}})
    assert "Vuoto" in out
    assert "[[Category:Infobox SV con dati mancanti]]" in out


def test_label_fallback():
    out = render(lang="pt")
    assert row("author", "J. R. R. Tolkien") in out
    assert "The Lord of the Rings" in out
    bare = dict(ENTITIES, Q10={})
    assert row("author", "Q10") in render(lang="pt", entities=bare)


def test_cast_max():
    film = {"Q1": {"labels": {"en": "Film"}, "claims": {"P552": [item(f"Q{n}") for n in range(100, 108)]}}}
    out = render(lang="en", page_entity="Q1", entities=film, type="film")
    assert out.count("Q10") == 6


def test_person():
    person = {"Q2": {"labels": {"en": "Bach"}, "claims": {
        "P4712": [time("+1685-03-31T00:00:00Z", 11)], "P818": [item("Q3")]}},
        "Q3": {"labels": {"en": "Eisenach"}, "sitelinks": {"enwiki": "Eisenach"}}}
    out = render(kind="person", lang="en", page_entity="Q2", entities=person)
    assert '<table class="sv-infobox sv-infobox-person">' in out
    assert row("birth_date", "j F Y|1685-03-31") in out
    assert row("birth_place", "[[wikipedia:en:Eisenach|Eisenach]]") in out


def test_values_are_escaped():
    evil = {"Q5": {"labels": {"en": 'A <b>"x"'}, "claims": {"P3990": [item("Q6")]}}, "Q6": {"labels": {"en": "<i>y"}}}
    out = render(lang="en", page_entity="Q5", entities=evil)
    assert "<b>" not in out and "<i>" not in out


def test_styles_loaded_through_extension_tag():
    # a literal "<templatestyles .../>" returned from Lua shows up as text on the page
    out = render()
    assert out.startswith(tag("templatestyles", src="SV infobox/styles.css"))
    assert "<templatestyles" not in out


def with_claims(**claims):
    return dict(ENTITIES, Q4178=dict(LOTR, claims=claims))


def test_bc_date_is_year_only():
    # formatDate cannot take a negative year (e.g. Virgil, born 15 October 70 BC, day precision)
    out = render(entities=with_claims(P4788=[time("-0070-10-15T00:00:00Z", 11)]))
    assert row("data di pubblicazione", "70 a.C.") in out


def test_early_year_zero_padded():
    out = render(entities=with_claims(P4788=[time("+0354-11-13T00:00:00Z", 11)]))
    assert row("data di pubblicazione", "j F Y|0354-11-13") in out


def test_only_first_image():
    out = render(entities=with_claims(P725=[text("One.jpg"), text("Two.jpg")]))
    assert "[[File:One.jpg|frameless|upright=1.15]]" in out
    assert "Two.jpg" not in out


def ranked(statement, rank):
    return dict(statement, rank=rank)


def test_preferred_rank_wins():
    # Wikibase shows the preferred statements when there are any
    out = render(entities=with_claims(P725=[ranked(text("One.jpg"), "normal"), ranked(text("Two.jpg"), "preferred")]))
    assert "[[File:Two.jpg|frameless|upright=1.15]]" in out
    assert "One.jpg" not in out


def test_julian_leap_day_kept():
    # 29 February 1500 exists in the Julian calendar only; formatDate would roll it to 1 March
    out = render(entities=with_claims(P4788=[time("+1500-02-29T00:00:00Z", 11)]))
    assert row("data di pubblicazione", "29 F Y|1500-02-01") in out


def date_row(iso, precision, lang="it"):
    out = render(lang=lang, entities=with_claims(P4788=[time(iso, precision)]))
    label = "data di pubblicazione" if lang == "it" else "publication date"
    return out, label


def test_decade_century_millennium():
    out, label = date_row("+1950-00-00T00:00:00Z", 8)
    assert row(label, "anni 1950") in out
    out, label = date_row("+1400-00-00T00:00:00Z", 7)
    assert row(label, "XIV secolo") in out
    out, label = date_row("+1301-00-00T00:00:00Z", 7, lang="en")
    assert row(label, "14th century") in out
    out, label = date_row("+2000-00-00T00:00:00Z", 6)
    assert row(label, "II millennio") in out


def test_day_format_per_language():
    out, label = date_row("+1954-07-29T00:00:00Z", 11, lang="de")
    assert row(label, "j. F Y|1954-07-29") in out
    out, label = date_row("+1954-07-29T00:00:00Z", 11, lang="es")
    assert row(label, 'j "de" F "de" Y|1954-07-29') in out
    out, label = date_row("+1954-07-00T00:00:00Z", 10, lang="es")
    assert row(label, 'F "de" Y|1954-07-01') in out


def test_bc_century():
    out, label = date_row("-0500-00-00T00:00:00Z", 7)
    assert row(label, "V secolo a.C.") in out


# --- categories from the item's SV field / SV Gospel theme statements (P10768 / P10769) ---

MUSIC, FAITH, LOVE = "Q47743", "Q47888", None


def cat_entities(field=(MUSIC,), themes=(FAITH,), extra=None):
    claims = {"P10768": [item(q) for q in field], "P10769": [item(q) for q in themes]}
    ents = dict(ENTITIES)
    ents["Q4178"] = dict(LOTR, claims=dict(LOTR["claims"], **claims))
    ents[MUSIC] = {"labels": {"it": "Musica"}, "sitelinks": {"it": "Categoria:Musica", "en": "Category:Music"}}
    ents[FAITH] = {"labels": {"it": "Fede"}, "sitelinks": {"it": "Categoria:Fede"}}
    ents.update(extra or {})
    return ents


def categories(lang="it", page_entity="Q4178", entities=None, **args):
    env = LuaEnv(lang=lang, entities=entities if entities is not None else cat_entities(),
                 page_entity=page_entity, page_title="Pagina")
    return env.call("Module:SV/Infobox", "categories", env.frame(args))


def msg(lang, key):
    import json
    from conftest import ROOT
    return json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))[key]


def test_field_and_theme_categories():
    out = categories()
    assert "[[Category:Musica]]" in out and "[[Category:Fede]]" in out
    assert out.index("Musica") < out.index("Fede")


def test_infobox_outputs_the_categories_too():
    out = render(entities=cat_entities())
    assert "[[Category:Musica]]" in out and "[[Category:Fede]]" in out


def test_sortkey_param():
    assert "[[Category:Musica|Buona novella]]" in categories(sortkey="Buona novella")


def test_not_tree_value_tracked():
    ents = cat_entities(themes=("Q999",), extra={"Q999": {"labels": {"it": "Altro"}, "sitelinks": {"it": "Categoria:Altro"}}})
    out = categories(entities=ents)
    assert f"[[Category:{msg('it', 'category_not_tree')}]]" in out
    assert "Altro" not in out


def test_theme_value_that_is_a_field_is_not_a_theme():
    out = categories(entities=cat_entities(themes=(MUSIC,)))
    assert f"[[Category:{msg('it', 'category_not_tree')}]]" in out


def test_two_fields_first_used_and_tracked():
    ents = cat_entities(field=(MUSIC, "Q47734"), extra={"Q47734": {"sitelinks": {"it": "Categoria:Arti"}}})
    out = categories(entities=ents)
    assert "[[Category:Musica]]" in out and "Categoria:Arti" not in out and "[[Category:Arti]]" not in out
    assert f"[[Category:{msg('it', 'category_two_fields')}]]" in out


def test_tree_item_without_local_category_tracked():
    ents = cat_entities()
    ents[FAITH] = {"labels": {"it": "Fede"}, "sitelinks": {}}
    out = categories(entities=ents)
    assert f"[[Category:{msg('it', 'category_not_on_wiki')}]]" in out
    assert "Fede]]" not in out.replace(msg('it', 'category_not_on_wiki'), "")


def test_sv_categories_without_item_is_empty():
    assert categories(page_entity=None, entities={}) == ""


def test_item_param_names_the_item():
    assert "[[Category:Musica]]" in categories(page_entity=None, item="Q4178")


# --- {{SV infobox}}: the layout follows the item's instance of (P2016) ---

def auto(instance_of, lang="it", **args):
    ents = dict(ENTITIES)
    ents["Q4178"] = dict(LOTR, claims=dict(LOTR["claims"], P2016=[item(q) for q in instance_of]))
    env = LuaEnv(lang=lang, entities=ents, page_entity="Q4178", page_title="Pagina")
    ctx = env.lua.table_from({"lang": lang, "entity_id": "Q4178", "frame": env.frame()})
    return env.call("Module:SV/Infobox", "_render", "auto", env.lua.table_from(args), ctx)


def test_auto_picks_film_from_instance_of():
    assert '<table class="sv-infobox sv-infobox-film">' in auto(["Q13367"])


def test_auto_type_param_wins():
    assert '<table class="sv-infobox sv-infobox-tv">' in auto(["Q13367"], type="tv")


def test_auto_first_mapped_instance_wins():
    assert '<table class="sv-infobox sv-infobox-person">' in auto(["Q13121", "Q13367"])
    assert '<table class="sv-infobox sv-infobox-film">' in auto(["Q13367", "Q13121"])
    out = auto(["Q999", "Q13121"])  # an unmapped value first is skipped
    assert '<table class="sv-infobox sv-infobox-person">' in out
    assert msg("it", "infobox_unknown_type") not in out


def test_auto_unknown_type_tracked():
    out = auto(["Q999"])
    assert '<table class="sv-infobox sv-infobox-work">' in out
    assert f"[[Category:{msg('it', 'infobox_unknown_type')}]]" in out


def test_auto_no_instance_of_tracked():
    assert f"[[Category:{msg('it', 'infobox_unknown_type')}]]" in auto([])


def test_auto_human_is_person():
    assert '<table class="sv-infobox sv-infobox-person">' in auto(["Q13121"])


def test_auto_group_layout():
    assert '<table class="sv-infobox sv-infobox-group">' in auto(["Q2404"])


def test_sortkey_space_entity_becomes_a_space():
    assert "[[Category:Musica| ]]" in categories(sortkey="&#32;")
