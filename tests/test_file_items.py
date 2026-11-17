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


def test_template_only_category_page_gets_the_module_call():
    # the item's themes are the union over all wikis: every page must call the module to show them
    p = plan_item("Q1", [page("en", ["Film"], text="{{Infobox film}} Intro.\n[[Category:Other]]")], {}, "film",
                  DATA, TYPES, NS, NODE_ITEM)
    assert p.add == [("sv_field", "Q_film")]
    assert p.edits["en"][1] == "{{Infobox film}} Intro.\n{{SV categories}}\n[[Category:Other]]"


def test_page_already_calling_the_module_is_not_edited():
    p = plan_item("Q1", [page("en", ["Film"], text="{{SV infobox}} Intro.")], {}, "film", DATA, TYPES, NS, NODE_ITEM)
    assert p.edits == {}


def test_module_call_appended_when_no_category_line():
    p = plan_item("Q1", [page("en", ["Film"], text="Intro.")], {}, "film", DATA, TYPES, NS, NODE_ITEM)
    assert p.edits["en"][1] == "Intro.\n\n{{SV categories}}"


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


# --- execution (Task 6) ---

import re as _re

import pytest

from sync.wiki import SyncError
from tools import file_items as fi

PROPS = {"sv_field": "P10768", "sv_theme": "P10769", "instance_of": "P2016"}
CAT_LINE = _re.compile(r"\[\[Category:([^\]|]+)(?:\|[^\]]*)?\]\]")


class Repo:
    def __init__(self, statements=None, instance_of=None):
        self.statements = statements or {}   # item -> {prop: [Q]}
        self.instance_of = instance_of or {}
        self.calls = []

    def item_values(self, item, prop):
        if prop == PROPS["instance_of"]:
            return list(self.instance_of.get(item, []))
        return list(self.statements.get(item, {}).get(prop, []))

    def add_item_claim(self, item, prop, value, tags=None, summary=None):
        self.calls.append(("claim", item, prop, value))
        self.statements.setdefault(item, {}).setdefault(prop, []).append(value)

    def items_for(self, site, titles):
        pages = self.clients[site].pages
        return {t: pages[t][0] for t in titles if pages[t][0]}

    def label(self, item, lang="en"):
        return {"Q_tv": "television series"}.get(item)


class Client:
    """One wiki: pages are (item, revid, text, template-added categories)."""

    def __init__(self, lang, repo, pages, log):
        self.lang, self.repo, self.pages, self.log = lang, repo, pages, log
        self.conflict = set()
        self.drop_from_preview = set()

    def _cats(self, item, text, template_cats):
        cats = set(template_cats) | {m.group(1).strip() for m in CAT_LINE.finditer(text)}
        if fi.calls_module(text):
            by_item = {n["item"]: n["names"][self.lang] for n in _all_nodes()}
            for prop in ("P10768", "P10769"):
                cats |= {by_item[q] for q in self.repo.statements.get(item, {}).get(prop, [])}
        return cats

    def articles(self):
        return list(self.pages)

    def items_for(self, site, titles):
        raise AssertionError("client wikis have no wbgetentities: ask the repository")

    def revision(self, title):
        _, revid, text, _ = self.pages[title]
        return revid, text

    def page_categories(self, title):
        item, _, text, tc = self.pages[title]
        return self._cats(item, text, tc)

    def preview_categories(self, title, text):
        item, _, _, tc = self.pages[title]
        return self._cats(item, text, tc) - self.drop_from_preview

    def category_namespaces(self):
        return ["Category"]

    def edit(self, title, text, summary, tags=None, createonly=False, baserevid=None):
        self.log.append(("edit", self.lang, title))
        if title in self.conflict:
            raise SyncError(f"{self.lang}: editconflict: Edit conflict.")
        item, revid, _, tc = self.pages[title]
        assert baserevid == revid
        self.pages[title] = (item, revid + 1, text, tc)


def _all_nodes():
    out = [DATA["root"], DATA["themes_root"]]

    def walk(ns):
        for n in ns:
            out.append(n)
            walk(n.get("children", []))
    walk(DATA["fields"])
    walk(DATA["themes"])
    return out


class Jev:
    def __init__(self, choice, outcome):
        self.choice, self.outcome, self.texts = choice, outcome, []


def setup(pages_by_lang, statements=None, instance_of=None):
    repo = Repo(statements, instance_of)
    log = repo.calls
    clients = {lang: Client(lang, repo, dict(pages), log) for lang, pages in pages_by_lang.items()}
    repo.clients = clients
    return repo, clients


def run(repo, clients, apply=True, jev=None, recorded=None, only_item=None):
    def classify(text):
        jev.texts.append(text)
        return fi.jev.Decision("classify", None, {}, {jev.choice: 0.9}, 0.9, jev.choice, jev.outcome)

    return fi.run(repo, clients, DATA, TYPES, PROPS, apply=apply, only_item=only_item,
                  classify=classify if jev else None,
                  record=(recorded.append if recorded is not None else (lambda d: None)))


FILM_EN = {"Film page": ("Q1", 10, "Intro.\n[[Category:Film]]\n[[Category:Faith]]", set())}
FILM_IT = {"Pagina film": ("Q1", 20, "Intro.\n[[Category:Film]]\n[[Category:Love]]", set())}


def test_dry_run_writes_nothing():
    repo, clients = setup({"en": FILM_EN, "it": FILM_IT})
    report = run(repo, clients, apply=False)
    assert repo.calls == [] and report.planned == 1


def test_statements_before_edits():
    repo, clients = setup({"en": FILM_EN, "it": FILM_IT}, instance_of={"Q1": ["Q_film_type"]})
    report = run(repo, clients)
    kinds = [c[0] for c in repo.calls]
    assert kinds.index("edit") > max(i for i, k in enumerate(kinds) if k == "claim")
    assert repo.statements["Q1"] == {"P10768": ["Q_film"], "P10769": ["Q_faith", "Q_love"]}
    assert clients["it"].pages["Pagina film"][2] == "Intro.\n{{SV categories}}"
    assert report.edited == 2 and not report.unsafe


def test_unsafe_preview_skips_edit():
    repo, clients = setup({"en": FILM_EN})
    clients["en"].drop_from_preview = {"Faith"}
    report = run(repo, clients)
    assert ("edit", "en", "Film page") not in repo.calls
    assert report.unsafe == [("en", "Film page", ["Faith"])] and report.exit_code == 1


def test_editconflict_reported_and_run_continues():
    repo, clients = setup({"en": FILM_EN, "it": FILM_IT})
    clients["en"].conflict = {"Film page"}
    report = run(repo, clients)
    assert report.failed and "editconflict" in report.failed[0][2]
    assert clients["it"].pages["Pagina film"][2] == "Intro.\n{{SV categories}}"


def test_recheck_applied_replaces_field_and_records():
    repo, clients = setup({"en": FILM_EN}, instance_of={"Q1": ["Q_tv"]})
    recorded, jev = [], Jev("television", "applied")
    run(repo, clients, jev=jev, recorded=recorded)
    assert repo.statements["Q1"]["P10768"] == ["Q_television"]
    assert len(recorded) == 1 and jev.texts[0].startswith("Type: television series.")
    assert "Film]]" not in clients["en"].pages["Film page"][2]


def test_recheck_author_stops_item():
    repo, clients = setup({"en": FILM_EN}, instance_of={"Q1": ["Q_tv"]})
    report = run(repo, clients, jev=Jev("film", "author"), recorded=[])
    assert repo.calls == [] and report.stopped and "author" in report.stopped[0][1]


def test_main_page_never_planned():
    repo, clients = setup({"en": {"Main Page": ("Q4", 1, "[[Category:Film]]", set())}})
    report = run(repo, clients)
    assert repo.calls == [] and report.planned == 0


def test_unconnected_articles_reported():
    repo, clients = setup({"en": {"Loose": (None, 1, "[[Category:Film]]", set())}})
    report = run(repo, clients)
    assert report.unconnected == [("en", "Loose")]


def test_second_run_is_a_no_op():
    repo, clients = setup({"en": FILM_EN, "it": FILM_IT})
    run(repo, clients)
    before = list(repo.calls)
    report = run(repo, clients)
    assert repo.calls == before and report.edited == 0


def test_only_item():
    repo, clients = setup({"en": dict(FILM_EN, **{"Other": ("Q2", 5, "[[Category:Music]]", set())})})
    run(repo, clients, only_item="Q2")
    assert [c[1] for c in repo.calls if c[0] == "claim"] == ["Q2"]


def test_rewrite_last_line_after_the_template_leaves_no_blank_line():
    out = rewrite("Intro.\n[[Category:Film]]\n[[Category:Love]]", ["Film", "Love"], ["Category"], has_module=False)
    assert out == "Intro.\n{{SV categories}}"
