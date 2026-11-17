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


def test_a_wikipedia_link_changed_after_the_bot_is_kept():
    # the bot wrote dewiki=Kategorie:Musik over Kategorie:Original; an editor then corrected it
    before = dict(WIKI, dewiki="Kategorie:Original")
    current = dict(WIKI, dewiki="Kategorie:Corrected")
    plan = plan_item(current, before=before, titles=TITLES, written=dict(WIKI))
    assert "dewiki" not in plan.sitelinks
    assert ("dewiki", "Kategorie:Corrected", "Kategorie:Musik") in plan.kept


def test_a_link_added_after_the_bot_on_a_bot_created_item_is_kept():
    written = dict(WIKI)
    del written["frwiki"]
    plan = plan_item(dict(WIKI), before=None, titles=TITLES, written=written)
    assert "frwiki" not in plan.sitelinks and plan.sitelinks["enwiki"]["remove"] == ""


class Repo:
    """One item; the first save answers editconflict when `conflict` is set."""

    def __init__(self, conflict=False):
        self.conflict, self.saves, self.reads = conflict, [], 0

    def _call(self, method, **p):
        if p.get("action") == "wbeditentity":
            self.saves.append(p)
            if self.conflict:
                self.conflict = False
                from sync.wiki import SyncError
                raise SyncError("en: editconflict: Edit conflict.")
            return {"success": 1}
        if p.get("action") == "wbgetentities":
            self.reads += 1
            return {"entities": {"Q1": {"lastrevid": 40 + self.reads,
                                        "sitelinks": {s: {"title": t} for s, t in WIKI.items()}}}}
        if p.get("list") == "usercontribs":
            return {"query": {"usercontribs": [{"title": "Item:Q1", "revid": 7, "parentid": 0, "new": ""}]}}
        if p.get("prop") == "revisions":
            import json
            content = json.dumps({"sitelinks": {s: {"title": t} for s, t in WIKI.items()}})
            return {"query": {"pages": [{"revisions": [{"slots": {"main": {"content": content}}}]}]}}
        raise AssertionError(p)

    def _dig(self, data, *keys):
        for k in keys:
            data = data[k]
        return data

    def _token(self, kind):
        return "t"

    def login(self, *a):
        pass


def test_save_carries_the_base_revision_and_replans_after_a_conflict(monkeypatch):
    from tools import fix_sitelinks
    monkeypatch.setenv("SV_EDITOR_USER", "u")
    monkeypatch.setenv("SV_EDITOR_PASSWORD", "p")
    repo = Repo(conflict=True)
    assert fix_sitelinks.main(["--apply"], repo=repo, data=None) == 0
    assert [s["baserevid"] for s in repo.saves] == ["41", "42"]
