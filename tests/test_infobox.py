import pytest

from claims import item, mono, text, time
from lua_env import LuaEnv, tag


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


def test_person_place_climbs_to_the_town():
    # Q3467 neighborhood, Q7267 street: classes in the place rows' "climb" list
    person = {"Q2": {"labels": {"en": "Bowie"}, "claims": {"P818": [item("Q40")], "P915": [item("Q42")]}},
              "Q40": {"labels": {"en": "Brixton"}, "claims": {"P2016": [item("Q3467")], "P294": [item("Q41")]}},
              "Q41": {"labels": {"en": "London"}, "sitelinks": {"enwiki": "London"}},
              "Q42": {"labels": {"en": "Lafayette Street"}, "claims": {"P2016": [item("Q7267")]}}}
    out = render(kind="person", lang="en", page_entity="Q2", entities=person)
    assert row("birth_place", "[[wikipedia:en:London|London]]") in out
    # no located-in statement: the place as stored
    assert row("death_place", "Lafayette Street") in out


def test_two_places_climbing_to_one_town_show_it_once():
    person = {"Q2": {"labels": {"en": "X"}, "claims": {"P818": [item("Q40"), item("Q43")]}},
              "Q40": {"labels": {"en": "Brixton"}, "claims": {"P2016": [item("Q3467")], "P294": [item("Q41")]}},
              "Q43": {"labels": {"en": "Soho"}, "claims": {"P2016": [item("Q3467")], "P294": [item("Q41")]}},
              "Q41": {"labels": {"en": "London"}, "sitelinks": {"enwiki": "London"}}}
    out = render(kind="person", lang="en", page_entity="Q2", entities=person)
    assert row("birth_place", "[[wikipedia:en:London|London]]") in out


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


def categories(lang="it", page_entity="Q4178", entities=None, namespace=0, **args):
    env = LuaEnv(lang=lang, entities=entities if entities is not None else cat_entities(),
                 page_entity=page_entity, page_title="Pagina", namespace=namespace)
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


def test_person_occupation_capped_at_three():
    # David Bowie's item has 22 occupations: the row shows the first three, in item order
    jobs = {f"Q{n}": {"labels": {"en": f"job{n}"}} for n in range(300, 305)}
    person = {"Q2": {"labels": {"en": "Bowie"}, "claims": {"P63": [item(q) for q in jobs]}}, **jobs}
    out = render(kind="person", lang="en", page_entity="Q2", entities=person)
    assert row("occupation", "job300, job301, job302") in out
    assert "job303" not in out


JULIAN = "http://www.wikidata.org/entity/Q1985786"
GREGORIAN = "http://www.wikidata.org/entity/Q1985727"


def cal_time(iso, precision, model):
    statement = time(iso, precision)
    statement["mainsnak"]["datavalue"]["value"]["calendarmodel"] = model
    return statement


def julian_row(iso, precision=11, model=JULIAN):
    out = render(entities=with_claims(P4788=[cal_time(iso, precision, model)]))
    return out


def test_julian_day_after_reform_shown_gregorian():
    # Bach's preferred birth date on the local item: +1685-03-21 in the Julian calendar = 31 March 1685
    assert row("data di pubblicazione", "j F Y|1685-03-31") in julian_row("+1685-03-21T00:00:00Z")
    # from the stored Julian date 1582-10-15 on, and a month rollover across a Julian-only leap day
    assert row("data di pubblicazione", "j F Y|1582-10-25") in julian_row("+1582-10-15T00:00:00Z")
    assert row("data di pubblicazione", "j F Y|1700-03-11") in julian_row("+1700-02-29T00:00:00Z")
    assert row("data di pubblicazione", "j F Y|1918-01-14") in julian_row("+1918-01-01T00:00:00Z")


def test_julian_before_reform_or_coarse_shown_as_stored():
    assert row("data di pubblicazione", "j F Y|1300-03-12") in julian_row("+1300-03-12T00:00:00Z")
    assert row("data di pubblicazione", "j F Y|1582-10-14") in julian_row("+1582-10-14T00:00:00Z")
    assert row("data di pubblicazione", "F Y|1685-03-01") in julian_row("+1685-03-00T00:00:00Z", 10)
    assert row("data di pubblicazione", "1685") in julian_row("+1685-00-00T00:00:00Z", 9)
    # a Gregorian value is never converted
    assert row("data di pubblicazione", "j F Y|1685-03-21") in julian_row("+1685-03-21T00:00:00Z", model=GREGORIAN)


def test_caption_names_the_layout_in_the_wiki_language():
    assert '<caption><span class="sv-infobox-kind">Libro</span>Il Signore degli Anelli</caption>' in render()
    assert '<span class="sv-infobox-kind">Book</span>' in render(lang="en")


def test_footer_links_the_item_on_the_repository():
    out = render()
    assert ('<tr><td colspan="2" class="sv-infobox-source">Dati dall\'elemento '
            '[[seminaverbi:Item:Q4178|Q4178]]</td></tr>') in out
    assert "[[Item:Q4178|Q4178]]" in render(lang="en")


def test_no_footer_without_an_item():
    assert "sv-infobox-source" not in render(page_entity=None)


def test_every_layout_has_a_kind_label_and_an_accent():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    css = (root / "templates/SV infobox/styles.css").read_text(encoding="utf-8")
    for layout in json.loads((root / "config/infobox.json").read_text(encoding="utf-8")):
        assert f".sv-infobox-{layout} " in css or f".sv-infobox-{layout}," in css, layout
        for lang in ("en", "it", "es", "fr", "de", "pt"):
            strings = json.loads((root / f"i18n/{lang}.json").read_text(encoding="utf-8"))
            assert strings.get(f"infobox_kind_{layout}"), (lang, layout)
            assert "$1" in strings.get("infobox_source", ""), lang


def test_an_unknown_or_wrong_case_type_shows_no_message_key():
    out = render(type="Romanzo")
    assert "infobox_kind_" not in out and '<table class="sv-infobox sv-infobox-book">' in out
    assert '<table class="sv-infobox sv-infobox-film">' in render(type="Film")


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def test_white_on_every_band_colour_meets_wcag_aa_and_the_kind_label_is_not_dimmed():
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "templates/SV infobox/styles.css").read_text(encoding="utf-8")
    kind = re.search(r"\.sv-infobox-kind\s*\{([^}]*)\}", css).group(1)
    # opacity lowers the white label's contrast on the band; hierarchy comes from size and spacing
    assert "opacity" not in kind
    bands = set(re.findall(r"caption\s*\{[^}]*?background:\s*(#[0-9a-fA-F]{6})", css))
    assert len(bands) >= 6
    for band in bands:
        assert 1.05 / (_luminance(band) + 0.05) >= 4.5, band


def occupation_person(gender=None, lang="it"):
    claims = {"P63": [item("Q50")]}
    if gender:
        claims["P1018"] = [item(gender)]
    return render(kind="person", lang=lang, page_entity="Q2", entities={
        "Q2": {"labels": {"it": "X", "en": "X"}, "claims": claims},
        "Q50": {"labels": {"it": "cantautore", "en": "singer-songwriter"},
                "sitelinks": {"itwiki": "Cantautore", "enwiki": "Singer-songwriter"},
                "claims": {"P1451": [mono("cantautrice", "it")]}},
        "Q99": {"labels": {"it": "non binario"}}})


def test_occupation_takes_the_female_form_for_a_woman():
    assert "[[wikipedia:it:Cantautore|cantautrice]]" in occupation_person("Q247")


def test_occupation_keeps_the_label_for_a_man_or_no_stated_gender():
    assert "[[wikipedia:it:Cantautore|cantautore]]" in occupation_person("Q246")
    assert "[[wikipedia:it:Cantautore|cantautore]]" in occupation_person()


def test_occupation_without_a_shared_form_falls_back_to_the_label():
    # the infobox is a data row, not a sentence: it still names the occupation
    assert "[[wikipedia:it:Cantautore|cantautore]]" in occupation_person("Q99")


def test_english_occupation_without_female_form_keeps_the_label():
    assert "[[wikipedia:en:Singer-songwriter|singer-songwriter]]" in occupation_person("Q247", lang="en")


def test_female_form_is_used_when_the_occupation_has_no_label():
    out = render(kind="person", lang="it", page_entity="Q2", entities={
        "Q2": {"labels": {"it": "X"}, "claims": {"P63": [item("Q50")], "P1018": [item("Q247")]}},
        "Q50": {"sitelinks": {"itwiki": "Cantautore"}, "claims": {"P1451": [mono("cantautrice", "it")]}}})
    assert "[[wikipedia:it:Cantautore|cantautrice]]" in out


def test_female_form_markup_is_shown_literally():
    out = render(kind="person", lang="it", page_entity="Q2", entities={
        "Q2": {"labels": {"it": "X"}, "claims": {"P63": [item("Q50")], "P1018": [item("Q247")]}},
        "Q50": {"labels": {"it": "cantautore"}, "sitelinks": {"itwiki": "Cantautore"},
                "claims": {"P1451": [mono("canta]]utrice", "it")]}}})
    assert "[[wikipedia:it:Cantautore|canta&#93;&#93;utrice]]" in out


def test_a_composition_has_its_own_layout_with_the_composer():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    rows = json.loads((root / "config/infobox.json").read_text(encoding="utf-8"))["composition"]
    assert [r["prop"] for r in rows] == ["image", "title", "composer", "lyrics_by", "inception",
                                         "publication_date", "genre"]
    out = render(type="composition", composer="[[Tchaikovsky]]")
    assert '<table class="sv-infobox sv-infobox-composition">' in out
    assert row("composer", "[[Tchaikovsky]]") in out and "performer" not in out


@pytest.mark.parametrize("layout", ["composition", "song"])
def test_a_musical_work_shows_when_it_was_composed(layout):
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    rows = json.loads((root / "config/infobox.json").read_text(encoding="utf-8"))[layout]
    keys = [r["key"] for r in rows]
    assert {"key": "composed", "prop": "inception", "kind": "time"} in rows
    assert keys.index("lyrics_by") < keys.index("composed") < keys.index("publication_date")
    labels = {"en": "composed", "it": "composto", "es": "compuesto", "fr": "composé", "de": "komponiert",
              "pt": "composto"}
    for lang, text in labels.items():
        assert row(text, "1883") in render(type=layout, lang=lang, composed="1883"), lang


def test_a_composed_row_reads_the_inception_of_the_item():
    out = render(type="composition", lang="en", page_entity="Q90", entities={
        "Q90": {"labels": {"en": "Legend"}, "claims": {"P4732": [time("+1883-00-00T00:00:00Z", 9)]}}})
    assert row("composed", "1883") in out


@pytest.mark.parametrize("instance_of, layout", [
    ("Q48432", "composition"),  # musical work/composition (Legend, the Liturgy)
    ("Q48444", "work"),         # Christmas tradition (Christmas tree)
    ("Q47641", "work"),         # fictional extraterrestrial species (New Gods)
    ("Q9210", "work"),          # fictional humanoid species (New Gods)
    ("Q6116", "work"),          # tradition (Easter bread)
    ("Q2259", "song"),          # song
    ("Q6194", "song"),          # single (the Nek and Mannoia songs)
])
def test_auto_maps_the_types_of_existing_articles(instance_of, layout):
    out = auto([instance_of])
    assert f'<table class="sv-infobox sv-infobox-{layout}">' in out
    assert msg("it", "infobox_unknown_type") not in out


def test_tree_categories_only_on_articles():
    """A user sandbox or template page showing an item's infobox must not count as an article."""
    for ns in (2, 4, 10):
        out = categories(namespace=ns)
        assert "[[Category:Musica]]" not in out and "[[Category:Fede]]" not in out
    assert "[[Category:Musica]]" in categories(namespace=0)


def test_tracking_categories_stay_outside_articles():
    ents = cat_entities(themes=("Q999",), extra={"Q999": {"labels": {"it": "Altro"}, "sitelinks": {"it": "Categoria:Altro"}}})
    out = categories(entities=ents, namespace=2)
    assert f"[[Category:{msg('it', 'category_not_tree')}]]" in out
    assert "[[Category:Musica]]" not in out
