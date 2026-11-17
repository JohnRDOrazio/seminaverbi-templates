import json

import pytest

from conftest import ROOT
from sync.categories import load, nodes
from sync.wiki import SyncError
from tools import migrate_categories as mig
from tools.jev import Decision

LANGS = ["en", "it", "es", "fr", "de", "pt"]


def names(base, it=None):
    return {lang: (it if lang == "it" and it else base if lang == "en" else f"{base}-{lang}") for lang in LANGS}


def node(key, base, children=None, it=None):
    n = {"key": key, "names": names(base, it), "definition": {"en": f"{base} def."}}
    if children:
        n["children"] = children
    return n


DATA = {
    "root": {"names": {lang: "Seeds" for lang in LANGS}, "definition": {"en": "Root def."}},
    "themes_root": {"names": names("Themes"), "definition": {"en": "Themes def."}},
    "fields": [node("arts", "Arts", [node("literature", "Literature", [node("novels", "Novels"), node("poetry", "Poetry")]),
                                     node("film", "Film", [node("television", "Television")])])],
    "themes": [node("virtues", "Virtues", [node("family", "Family")])],
}


class Fake:
    """A wiki: pages {title: text}, members {category title: [(title, ns)]}; records moves and edits."""

    def __init__(self, lang="en", pages=None, members=None, redirects=()):
        self.lang, self.pages, self.members_of = lang, dict(pages or {}), dict(members or {})
        self.redirects = set(redirects)
        self.moves, self.edits, self.fail_edit = [], [], set()

    def category_namespaces(self):
        return ["Category", "Categoria"]

    def exists(self, title):
        return title in self.pages

    def is_redirect(self, title):
        return title in self.redirects

    def wikitext(self, title):
        return self.pages.get(title)

    def members(self, category_title):
        return list(self.members_of.get(category_title, []))

    def move(self, old, new, reason, tags=None):
        self.moves.append((old, new, tags))
        self.pages[new] = self.pages.pop(old)
        self.pages[old] = f"#REDIRECT [[{new}]]"
        self.members_of[new] = self.members_of.pop(old, [])

    def edit(self, title, text, summary, tags=None, createonly=False):
        if title in self.fail_edit:
            raise SyncError(f"{self.lang}: edit of {title} failed")
        self.edits.append((title, text, tags))
        self.pages[title] = text


class FakeJev:
    def __init__(self, choice="novels", outcome="applied"):
        self.result = Decision("classify", None, {}, {choice: 0.9}, 0.9, choice, outcome)
        self.calls = 0

    def classify(self, text, data):
        self.calls += 1
        return self.result


def run(fake, mapping, jev=None, dry_run=False):
    steps = mig.plan(fake.lang, DATA, mapping, fake, jev or FakeJev())
    report = mig.execute(fake, steps, dry_run=dry_run, lang=fake.lang)
    return steps, report


# -- refile_text -------------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "[[Category:Literary arts]]", "[[category:literary arts]]", "[[Category:Literary_arts]]",
    "[[ Category : Literary arts ]]", "[[Categoria:Literary arts]]",
])
def test_refile_forms(line):
    text, changed = mig.refile_text(f"Intro.\n{line}\n", "Literary arts", "Literature", ["Category", "Categoria"])
    assert changed and text == "Intro.\n[[Category:Literature]]\n"


def test_refile_keeps_sortkey():
    text, changed = mig.refile_text("[[Category:Literary arts|Hobbit, The]]", "Literary arts", "Literature", ["Category"])
    assert text == "[[Category:Literature|Hobbit, The]]"


def test_refile_does_not_touch_inline_links_or_others():
    src = "See [[:Category:Literary arts]]. [[Category:Literary arts history]]"
    assert mig.refile_text(src, "Literary arts", "Literature", ["Category"]) == (src, False)


def test_refile_removes_duplicate_when_new_already_present():
    text, changed = mig.refile_text("[[Category:Literature]]\n[[Category:Literary arts]]", "Literary arts",
                                    "Literature", ["Category"])
    assert changed and text.count("[[Category:Literature]]") == 1


# -- planning and execution --------------------------------------------------------------------

def test_rename_moves_refiles_and_sets_text():
    fake = Fake(pages={"Category:Literary arts": "old", "Hobbit": "x [[Category:Literary arts|Hobbit]]"},
                members={"Category:Literary arts": [("Hobbit", 0)]})
    _, report = run(fake, {"Literary arts": "literature"})
    assert fake.moves == [("Category:Literary arts", "Category:Literature", "seminaverbi-mcp")]
    assert fake.pages["Hobbit"] == "x [[Category:Literature|Hobbit]]"
    assert fake.pages["Category:Literature"] == "Literature def.\n\n[[Category:Arts]]"
    assert not report.unmatched and not report.author


def test_same_name_only_sets_text():
    fake = Fake(pages={"Category:Film": "old text"})
    run(fake, {"Film": "film"})
    assert fake.moves == [] and fake.pages["Category:Film"] == "Film def.\n\n[[Category:Arts]]"


def test_target_exists_redirect_and_refile_to_target():
    fake = Fake(pages={"Category:Video gaming": "old", "Category:Film": "", "Game": "[[Category:Video gaming]]"},
                members={"Category:Video gaming": [("Game", 0)]})
    run(fake, {"Video gaming": "film"})
    assert fake.moves == []
    assert fake.pages["Category:Video gaming"] == "#REDIRECT [[:Category:Film]]"
    assert fake.pages["Game"] == "[[Category:Film]]"


def test_redirect_entry_refiles_and_redirects():
    fake = Fake(pages={"Category:Sitcom": "", "Show": "[[Category:Sitcom]]"}, members={"Category:Sitcom": [("Show", 0)]})
    _, report = run(fake, {"Sitcom": "redirect:television"})
    assert fake.moves == []
    assert fake.pages["Category:Sitcom"] == "#REDIRECT [[:Category:Television]]"
    assert fake.pages["Show"] == "[[Category:Television]]"
    assert "Category:Sitcom" in report.redirected


def test_split_uses_classify_confident():
    fake = Fake(pages={"Category:Fiction": "", "Book": "[[Category:Fiction]]"}, members={"Category:Fiction": [("Book", 0)]})
    jev = FakeJev("novels")
    run(fake, {"Fiction": "split:literature"}, jev)
    assert jev.calls == 1 and fake.pages["Book"] == "[[Category:Novels]]"
    assert fake.pages["Category:Fiction"] == "#REDIRECT [[:Category:Literature]]"


def test_split_unsure_member_left_for_author():
    fake = Fake(pages={"Category:Fiction": "", "Book": "[[Category:Fiction]]"}, members={"Category:Fiction": [("Book", 0)]})
    _, report = run(fake, {"Fiction": "split:literature"}, FakeJev("poetry", "author"))
    assert fake.pages["Book"] == "[[Category:Fiction]]"
    assert report.author and report.author[0][0] == "Book"
    # the old category keeps its page while a member still needs a decision
    assert fake.pages["Category:Fiction"] == ""


def test_jev_failure_aborts_split_without_edits():
    class Broken:
        def classify(self, text, data):
            raise OSError("no route to host")

    fake = Fake(pages={"Category:Fiction": "", "Book": "[[Category:Fiction]]"}, members={"Category:Fiction": [("Book", 0)]})
    with pytest.raises(OSError):
        mig.plan("en", DATA, {"Fiction": "split:literature"}, fake, Broken())
    assert fake.edits == [] and fake.moves == []


def test_unmatched_member_reported():
    fake = Fake(pages={"Category:Literary arts": "", "Templated": "{{Some template that adds the category}}"},
                members={"Category:Literary arts": [("Templated", 0)]})
    _, report = run(fake, {"Literary arts": "literature"})
    assert report.unmatched == [("Templated", "Category:Literary arts")]


def test_subcategory_members_in_map_are_not_refiled_twice():
    fake = Fake(pages={"Category:Literary arts": "", "Category:Poetry": "[[Category:Literary arts]]"},
                members={"Category:Literary arts": [("Category:Poetry", 14)]})
    run(fake, {"Literary arts": "literature", "Poetry": "poetry"})
    assert fake.pages["Category:Poetry"] == "Poetry def.\n\n[[Category:Literature]]"
    assert [t for t, _, _ in fake.edits].count("Category:Poetry") == 1


def test_old_redirect_or_missing_skipped():
    fake = Fake(pages={"Category:Dance": "#REDIRECT [[Category:Danza]]"}, redirects={"Category:Dance"})
    steps, report = run(fake, {"Dance": "film", "Missing": "film"})
    assert fake.edits == [] and fake.moves == []
    assert set(report.skipped) == {"Category:Dance", "Category:Missing"}


def test_dry_run_no_edits():
    fake = Fake(pages={"Category:Literary arts": "", "Hobbit": "[[Category:Literary arts]]"},
                members={"Category:Literary arts": [("Hobbit", 0)]})
    steps, report = run(fake, {"Literary arts": "literature"}, dry_run=True)
    assert fake.edits == [] and fake.moves == [] and steps


def test_edit_failure_reported_and_continues():
    fake = Fake(pages={"Category:Film": "", "Category:Literary arts": ""})
    fake.fail_edit = {"Category:Film"}
    _, report = run(fake, {"Film": "film", "Literary arts": "literature"})
    assert report.failed and report.failed[0][0] == "Category:Film"
    assert fake.pages["Category:Literature"].startswith("Literature def.")


# -- the real map --------------------------------------------------------------------------------

def test_real_map_is_valid():
    mapping = json.loads((ROOT / "config" / "category_migration.json").read_text(encoding="utf-8"))
    keys = {n.key for n in nodes(load(ROOT))}
    assert set(mapping) == {"en", "it", "fr"}
    for lang, entries in mapping.items():
        for old, target in entries.items():
            kind, _, key = target.rpartition(":")
            assert kind in ("", "redirect", "split"), (lang, old)
            assert key in keys, (lang, old, target)
