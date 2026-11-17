from tools.fix_sitelinks import plan_item

TITLES = {"en": "Category:Music", "it": "Categoria:Musica", "es": "Categoría:Música",
          "fr": "Catégorie:Musique", "de": "Kategorie:Musik", "pt": "Categoria:Música"}
WIKI = {f"{lang}wiki": t for lang, t in TITLES.items()}


def test_bot_created_item_moves_every_link_to_the_seminaverbi_site():
    plan = plan_item(dict(WIKI), before=None, titles=TITLES)
    assert plan.conflicts == []
    for lang, title in TITLES.items():
        assert plan.sitelinks[lang] == {"site": lang, "title": title}
        assert plan.sitelinks[f"{lang}wiki"] == {"site": f"{lang}wiki", "title": "", "remove": ""}


def test_imported_item_gets_its_wikipedia_links_back():
    # Q13282-like: imported from Wikidata, the bot overwrote dewiki only; 'en' was already right
    before = dict(WIKI, dewiki="Kategorie:Naturwissenschaft", en="Category:Music")
    current = dict(WIKI, en="Category:Music")
    plan = plan_item(current, before=before, titles=TITLES)
    assert plan.sitelinks["dewiki"] == {"site": "dewiki", "title": "Kategorie:Naturwissenschaft"}
    assert "enwiki" not in plan.sitelinks and "itwiki" not in plan.sitelinks  # untouched Wikipedia links stay
    assert "en" not in plan.sitelinks                                         # already right
    assert plan.sitelinks["it"] == {"site": "it", "title": "Categoria:Musica"}
    assert plan.sitelinks["de"] == {"site": "de", "title": "Kategorie:Musik"}


def test_a_different_seminaverbi_link_is_a_conflict_and_left_alone():
    plan = plan_item(dict(WIKI, fr="Catégorie:Autre"), before=None, titles=TITLES)
    assert ("fr", "Catégorie:Autre", "Catégorie:Musique") in plan.conflicts
    assert "fr" not in plan.sitelinks
    assert plan.sitelinks["frwiki"]["remove"] == ""  # the wrong-site link still goes


def test_nothing_to_do():
    assert plan_item(dict(TITLES), before=None, titles=TITLES).sitelinks == {}
