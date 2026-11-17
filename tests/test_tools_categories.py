import pytest

from sync.wiki import PageInfo, SyncError
from tools import categories as cat

LANGS = ["en", "it", "es", "fr", "de", "pt"]


def names(base):
    return {lang: base if lang == "en" else f"{base}-{lang}" for lang in LANGS}


DATA = {
    "root": {"names": {lang: "Semina Verbi" for lang in LANGS}},
    "themes_root": {"names": names("Themes")},
    "fields": [{"key": "arts", "names": names("Arts"), "definition": {"en": "The arts.", "fr": "Les arts."},
                "children": [{"key": "music", "names": names("Music"), "definition": {"en": "Music."}}]}],
    "themes": [{"key": "virtues", "names": names("Virtues"), "definition": {"en": "Virtues."},
                "children": [{"key": "love", "names": names("Love"), "definition": {"en": "Love."}}]}],
}


def page(*cats, item=None, redirect=False):
    return PageInfo(True, redirect, list(cats), item)


class Fake:
    def __init__(self, lang, pages=None, items=None, article_cats=()):
        self.lang, self.pages, self.article_cats = lang, dict(pages or {}), set(article_cats)
        self.items = items if items is not None else {}
        self.edits, self.sitelinks, self.new_items, self.fail_edit = [], [], [], set()

    def page_info(self, titles):
        return {t: self.pages.get(t) for t in titles}

    def article_categories(self):
        return set(self.article_cats)

    def edit(self, title, text, summary, tags=None, createonly=False):
        if title in self.fail_edit:
            raise SyncError(f"{self.lang}: edit of {title} failed: abusefilter")
        self.edits.append((title, text, tags, createonly))
        self.pages[title] = PageInfo(True, False, [], None)

    def item_for(self, site, title):
        for item, links in self.items.items():
            if links.get(site) == title:
                return item
        return None

    def sitelink(self, item, site):
        return self.items.get(item, {}).get(site)

    def set_sitelink(self, item, site, title, tags=None):
        self.sitelinks.append((item, site, title, tags))
        self.items[item][site] = title

    def new_item(self, labels, descriptions, sitelinks, tags=None):
        item = f"Q{100 + len(self.items)}"
        self.new_items.append((item, labels, descriptions, sitelinks, tags))
        self.items[item] = dict(sitelinks)
        return item


ALL_FR = {
    "Category:Semina Verbi": page(),
    "Category:Arts-fr": page("Semina Verbi"),
    "Category:Music-fr": page("Arts-fr"),
    "Category:Themes-fr": page("Semina Verbi"),
    "Category:Virtues-fr": page("Themes-fr"),
    "Category:Love-fr": page("Virtues-fr"),
}


def test_check_clean():
    report = cat.check("fr", DATA, Fake("fr", ALL_FR))
    assert report == cat.Report([], [], [], [])


def test_check_missing_redirect_wrong_parent():
    pages = dict(ALL_FR)
    del pages["Category:Music-fr"]
    pages["Category:Love-fr"] = page(redirect=True)
    pages["Category:Arts-fr"] = page("Autre")
    report = cat.check("fr", DATA, Fake("fr", pages))
    assert report.missing == ["music"]
    assert report.redirect == ["love"]
    assert report.wrong_parent == [("arts", ["Autre"])]


def test_check_untracked():
    report = cat.check("fr", DATA, Fake("fr", ALL_FR, article_cats={"Music-fr", "Cantautori britannici"}))
    assert report.untracked == ["Cantautori britannici"]


def test_create_never_edits_existing():
    pages = dict(ALL_FR)
    del pages["Category:Music-fr"]
    pages["Category:Arts-fr"] = page("Autre")          # wrong parent: reported, not fixed
    pages["Category:Love-fr"] = page(redirect=True)     # redirect: reported, not overwritten
    fr = Fake("fr", pages)
    result = cat.create("fr", DATA, fr, Fake("en", items=fr.items), dry_run=False)
    assert [e[0] for e in fr.edits] == ["Category:Music-fr"]
    title, text, tags, createonly = fr.edits[0]
    # no French definition: the English one is used
    assert text == "Music.\n\n[[Category:Arts-fr]]" and tags == "seminaverbi-mcp" and createonly is True
    assert result.created == ["Category:Music-fr"]


def test_create_page_text_definition_and_root():
    fr = Fake("fr", {})
    cat.create("fr", DATA, fr, Fake("en", items=fr.items), dry_run=False)
    texts = {t: text for t, text, _, _ in fr.edits}
    assert texts["Category:Semina Verbi"] == "{{SV category tree}}"
    assert texts["Category:Arts-fr"] == "Les arts.\n\n[[Category:Semina Verbi]]"
    assert texts["Category:Love-fr"] == "Love.\n\n[[Category:Virtues-fr]]"


def test_create_links_existing_item():
    items = {"Q10": {"enwiki": "Category:Music"}}
    fr = Fake("fr", {k: v for k, v in ALL_FR.items() if k != "Category:Music-fr"}, items)
    repo = Fake("en", items=items)
    result = cat.create("fr", DATA, fr, repo, dry_run=False)
    assert ("Q10", "frwiki", "Category:Music-fr", "seminaverbi-mcp") in repo.sitelinks
    assert ("Category:Music-fr", "Q10") in result.linked


def test_create_new_item_when_none():
    items = {}
    fr = Fake("fr", {k: v for k, v in ALL_FR.items() if k != "Category:Music-fr"}, items)
    repo = Fake("en", items=items)
    cat.create("fr", DATA, fr, repo, dry_run=False)
    made = {sitelinks["frwiki"]: (labels, descriptions, tags)
            for _, labels, descriptions, sitelinks, tags in repo.new_items}
    labels, descriptions, tags = made["Category:Music-fr"]
    assert labels == names("Music") and tags == "seminaverbi-mcp"
    assert descriptions["en"] == "Semina Verbi category" and set(descriptions) == set(LANGS)


def test_create_conflict():
    items = {"Q10": {"enwiki": "Category:Music"}, "Q11": {"itwiki": "Category:Music-it"}}
    fr = Fake("fr", ALL_FR, items)
    repo = Fake("en", items=items)
    result = cat.create("fr", DATA, fr, repo, dry_run=False)
    assert result.conflicts == [("music", ["Q10", "Q11"])]
    assert not [s for s in repo.sitelinks if s[2] == "Category:Music-fr"]


def test_create_never_moves_an_existing_sitelink():
    # Q10 already links frwiki to another page: re-pointing it would silently drop that link
    items = {"Q10": {"enwiki": "Category:Music", "frwiki": "Category:Old music"}}
    fr = Fake("fr", ALL_FR, items)
    repo = Fake("en", items=items)
    result = cat.create("fr", DATA, fr, repo, dry_run=False)
    assert result.taken == [("music", "Q10", "Category:Old music")]
    assert repo.sitelinks == [] and items["Q10"]["frwiki"] == "Category:Old music"


def test_cli_reports_taken_sitelink(env, capsys):
    items = {"Q10": {"enwiki": "Category:Music", "frwiki": "Category:Old music"}}
    clients = {"fr": Fake("fr", ALL_FR, items), "en": Fake("en", items=items)}
    for c in clients.values():
        c.login = lambda user, password: None
    assert cat.main(["check", "--wiki", "fr", "--create"], client_factory=clients.__getitem__, data=DATA) == 1
    assert "Q10 already links frwiki to Category:Old music" in capsys.readouterr().out


def test_dry_run_writes_nothing():
    items = {}
    fr = Fake("fr", {}, items)
    repo = Fake("en", items=items)
    result = cat.create("fr", DATA, fr, repo, dry_run=True)
    assert fr.edits == [] and repo.sitelinks == [] and repo.new_items == []
    assert "Category:Music-fr" in result.created


def test_edit_failure_continues():
    fr = Fake("fr", {})
    fr.fail_edit = {"Category:Arts-fr"}
    result = cat.create("fr", DATA, fr, Fake("en", items=fr.items), dry_run=False)
    assert [t for t, _ in result.failed] == ["Category:Arts-fr"]
    assert "Category:Music-fr" in result.created
    # a page that could not be created gets no sitelink
    assert all(sitelinks.get("frwiki") != "Category:Arts-fr" for sitelinks in fr.items.values())


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SV_EDITOR_USER", "SV editor bot@mcp")
    monkeypatch.setenv("SV_EDITOR_PASSWORD", "hunter2-secret")


def test_cli_unknown_wiki_no_login(env):
    made = []
    with pytest.raises(SystemExit) as exc:
        cat.main(["check", "--wiki", "xx"], client_factory=lambda lang: made.append(lang))
    assert exc.value.code == 2 and made == []


def test_cli_missing_env(monkeypatch):
    monkeypatch.delenv("SV_EDITOR_PASSWORD", raising=False)
    assert cat.main(["check", "--wiki", "fr"], client_factory=lambda lang: None) == 2


def test_cli_login_error_hides_password(env, capsys):
    class Failing:
        def __init__(self, lang):
            self.lang = lang

        def login(self, user, password):
            raise SyncError(f"{self.lang}: login failed: WrongPassword")

    assert cat.main(["check", "--wiki", "fr"], client_factory=Failing) == 1
    out, err = capsys.readouterr()
    assert "login failed" in err
    assert "hunter2-secret" not in out + err
