import re

import mwparserfromhell
import pytest

from sync.wiki import SyncError
from tools import cleanup_articles as ca

CONF = ca.load_config()


def clean(text, lang="en", missing=()):
    return ca.clean(text, lang, CONF, frozenset(missing))


# --- 1. Bible quotes ---

def test_en_bible_quote_becomes_sv_and_drops_the_default_version():
    out = clean("A {{Bible quote|version=NABRE|ref=Matthew 11:28}} b.")
    assert out.text == "A {{SV Bible quote|ref=Matthew 11:28}} b."
    assert out.converted == 1 and out.reported == []


def test_en_bible_quote_keeps_inline_and_a_non_default_version():
    out = clean("{{bible quote|ref=Romans 8:28|inline=false}} {{Bible_quote|version=NVBSE|ref=1 John 4:7-8|inline=0}}")
    assert out.text == ("{{SV Bible quote|ref=Romans 8:28|inline=false}} "
                        "{{SV Bible quote|version=NVBSE|ref=1 John 4:7-8|inline=0}}")
    assert out.converted == 2


def test_it_citazione_bibbia_maps_rif_and_drops_the_default_versione():
    out = clean("{{Citazione bibbia|versione=CEI2008|rif=Ef 3,14-15}} {{citazione bibbia|rif=Gv1:29-34|inline=0}}"
                " {{Citazione bibbia|versione=LUZZI|rif=Lc 1,17}}", lang="it")
    assert out.text == ("{{SV Bible quote|ref=Ef 3,14-15}} {{SV Bible quote|ref=Gv1:29-34|inline=0}}"
                        " {{SV Bible quote|version=LUZZI|ref=Lc 1,17}}")
    assert out.converted == 3


def test_default_versions_come_from_the_bible_config():
    assert CONF["defaults"] == {"en": "NABRE", "it": "CEI2008", "es": "BLPD", "fr": "NVBSE", "de": "NVBSE",
                                "pt": "NVBSE"}
    # the it default is not the en one: version=NABRE on it stays
    out = clean("{{Citazione bibbia|versione=NABRE|rif=Gv 3,16}}", lang="it")
    assert out.text == "{{SV Bible quote|version=NABRE|ref=Gv 3,16}}"


def test_whitespace_in_the_call_is_kept():
    out = clean("{{Bible quote\n| ref = Ex 9:8-12\n| inline = false\n}}")
    assert out.text == "{{SV Bible quote\n| ref = Ex 9:8-12\n| inline = false\n}}"
    out = clean("{{Citazione bibbia | rif= Gv 3,16 }}", lang="it")
    assert out.text == "{{SV Bible quote | ref= Gv 3,16 }}"


def test_bible_quote_nested_in_a_ref_or_a_template_is_converted():
    text = "<ref>See {{Bible quote|ref=John 8:7}}.</ref> {{Quote|text=x {{Bible quote|ref=John 3:16}}}}"
    out = clean(text)
    assert out.text == ("<ref>See {{SV Bible quote|ref=John 8:7}}.</ref> "
                        "{{Quote|text=x {{SV Bible quote|ref=John 3:16}}}}")
    assert out.converted == 2


@pytest.mark.parametrize("call, why", [
    ("{{Bible quote|ref=John 3:16|lang=en}}", "lang"),
    ("{{Bible quote|John 3:16}}", "1"),
    ("{{Bible quote|ref=}}", "ref"),
    ("{{Bible quote|inline=0}}", "ref"),
    ("{{Bible quote|ref=John 3:16|inline=no}}", "inline"),
    ("{{Bible quote|ref=John {{!}} 3:16}}", "ref"),
])
def test_bible_quote_the_sv_template_cannot_take_is_left_and_reported(call, why):
    out = clean(f"a {call} b")
    assert out.text == f"a {call} b" and out.converted == 0
    assert len(out.reported) == 1 and why in out.reported[0]


def test_both_rif_and_ref_are_left_and_reported():
    call = "{{Citazione bibbia|rif=Gv 3,16|ref=Gv 3,17}}"
    out = clean(call, lang="it")
    assert out.text == call and len(out.reported) == 1


def test_positional_bibleverse_is_left_and_reported():
    call = "{{Bibleverse||Genesis|2:9}}"
    out = clean(f"x {call}")
    assert out.text == f"x {call}" and out.converted == 0
    assert len(out.reported) == 1 and "Bibleverse" in out.reported[0]


@pytest.mark.parametrize("lang, call, other", [
    ("it", "{{Citazione bibbia|rif=Mark 13:33}}", "en"),
    ("en", "{{Bible quote|ref=Marc 8:27-28}}", "fr"),
    ("en", "{{Bible quote|ref=Genesis 2:8;13:10;Ezechiele 31:9}}", "it"),
])
def test_a_reference_in_another_language_is_reported_not_converted(lang, call, other):
    out = clean(call, lang=lang)
    assert out.text == call and out.converted == 0
    assert len(out.reported) == 1 and other in out.reported[0]


def test_an_unknown_book_is_reported_not_converted():
    call = "{{Bible quote|ref=Hezekiah 3:1}}"
    out = clean(call)
    assert out.text == call and "Hezekiah" in out.reported[0]


@pytest.mark.parametrize("lang, ref", [
    ("en", "Genesis 2:8;13:10;Ezekiel 31:9"), ("en", "2 Kings 23:29;2Chronicles 35:20-27"), ("en", "Apocalypse 16:16"),
    ("en", "Acts of the Apostles 17:16-34"), ("en", "Psalm 103"), ("en", "Matthew 24:4–5,11,23–26"),
    ("en", "1 Peter 2:17"), ("en", "Dt 6:5"), ("it", "Gen2:7"), ("it", "Giona 3:10;4"), ("it", "2Re1:2"),
    ("it", "Ger 1,1.4-10"), ("it", "Atti degli Apostoli 17,16-34"), ("it", "Qoelet 10:1"), ("it", "Mt. 5,45"),
])
def test_references_in_the_wikis_language_convert(lang, ref):
    name = "Bible quote|ref" if lang == "en" else "Citazione bibbia|rif"
    out = clean("{{" + name + "=" + ref + "}}", lang=lang)
    assert out.converted == 1 and out.reported == [], out.reported


# --- 2. Wikipedia leftovers ---

def test_leftovers_are_removed_with_their_newline():
    text = ("{{Short description|American TV series}}\n{{use_dmy dates|date=May 2020}}\n{{pp-semi-indef}}\n"
            "{{Use British English}}\n'''X''' is a show.\n\n{{Authority control}}\n{{1970s-drama-film-stub}}\n"
            "{{Good article}}\n[[Category:Film]]")
    out = clean(text)
    assert out.text == "'''X''' is a show.\n\n[[Category:Film]]"
    assert out.removed == ["Short description", "Use dmy dates", "Pp-semi-indef", "Use British English",
                           "Authority control", "1970s-drama-film-stub", "Good article"]


def test_it_leftovers():
    text = "Testo.\n\n{{Portale|musica|cinema}}\n{{S|film}}\n{{stub}}\n[[Categoria:Film]]"
    out = clean(text, lang="it")
    assert out.text == "Testo.\n\n[[Categoria:Film]]"
    # Portale is it's name: on en it is not on the list
    assert clean("x\n{{Portale|musica}}\n").removed == []


def test_a_bullet_holding_only_a_leftover_goes_with_it_and_an_emptied_section_too():
    text = ("Text.\n\n==External links==\n* {{Official website}}\n* {{IMDb title}}\n\n"
            "{{Netflix original continuation series}}\n\n==Other==\nMore.\n")
    out = clean(text)
    assert out.text == "Text.\n\n==External links==\n\n{{Netflix original continuation series}}\n\n==Other==\nMore.\n"
    assert out.removed == ["Official website", "IMDb title"]
    text = "Text.\n\n==External links==\n* {{Official website}}\n* {{IMDb title}}\n\n==Other==\nMore.\n"
    assert clean(text).text == "Text.\n\n==Other==\nMore.\n"
    text = "Text.\n\n==External links==\n* {{Official website}}\n* [https://x.org X]\n"
    assert clean(text).text == "Text.\n\n==External links==\n* [https://x.org X]\n"


def test_a_section_that_was_already_empty_keeps_its_heading():
    text = "Text.\n\n==Empty==\n\n==Links==\n{{Commons category|X}}\n"
    assert clean(text).text == "Text.\n\n==Empty==\n\n"


def test_a_section_with_subsections_keeps_its_heading():
    text = "==Media==\n{{Portal bar|Film}}\n===Sub===\nText.\n"
    assert clean(text).text == "==Media==\n===Sub===\nText.\n"


def test_names_compare_without_comments_case_of_the_first_letter_and_underscores():
    assert ca.normal(" short_description ") == "Short description"
    assert ca.normal("Template:Reflist") == "Reflist"
    assert ca.normal("Infobox comics <!--WikiProject Comics-->") == "Infobox comics"
    assert clean("{{Short_description <!-- x -->|y}}\nText.").text == "Text."


def test_other_templates_and_inactive_text_stay():
    text = ("{{Netflix original continuation series}}\n<!-- {{Short description|x}} -->\n"
            "<nowiki>{{Reflist}} {{Bible quote|ref=John 3:16}}</nowiki>\n{{Cite web|title=a|note={{Use mdy dates}}}}\n")
    out = clean(text)
    assert out.text == text and out.removed == [] and out.converted == 0


def test_inline_leftover_inside_a_line_is_removed_alone():
    text = "Text {{Portal|Film}} more.\n"
    assert clean(text).text == "Text  more.\n"


def test_missing_templates_are_reported_not_removed():
    text = "Text.\n{{Johnny Cash}}\n{{All Things Must Pass}}\n{{Johnny Cash}}\n{{1960s-country-album-stub}}\n"
    out = clean(text, missing={"Johnny Cash", "All Things Must Pass", "1960s-country-album-stub"})
    assert out.text == "Text.\n{{Johnny Cash}}\n{{All Things Must Pass}}\n{{Johnny Cash}}\n"
    assert out.removed == ["1960s-country-album-stub"]
    assert [r for r in out.reported if "missing template" in r] == [
        "missing template {{All Things Must Pass}} (1 call) left", "missing template {{Johnny Cash}} (2 calls) left"]


# --- 3. references ---

def test_reflist_under_its_own_heading_becomes_sv_references():
    text = "Text.<ref>a</ref>\n\n==References==\n{{reflist}}\n\n==External links==\n* x\n"
    out = clean(text)
    assert out.text == "Text.<ref>a</ref>\n\n{{SV references}}\n\n==External links==\n* x\n"
    assert out.references == "converted"


@pytest.mark.parametrize("old", ["{{Reflist|30em}}", "{{Reflist|colwidth=30em}}", "<references />", "<references/>",
                                 "<references responsive />", "{{References}}"])
def test_reference_lists_convert(old):
    out = clean(f"Text.<ref>a</ref>\n\n== Note ==\n{old}\n", lang="it")
    assert out.text == "Text.<ref>a</ref>\n\n{{SV references}}\n"


def test_a_list_without_heading_is_replaced_in_place():
    out = clean("Text.<ref>a</ref>\n<references />\n[[Category:Film]]")
    assert out.text == "Text.<ref>a</ref>\n{{SV references}}\n[[Category:Film]]"


def test_group_is_kept():
    assert clean("x<ref group=a>n</ref>\n==Notes==\n{{Reflist|group=a|45em}}\n").text == \
        "x<ref group=a>n</ref>\n{{SV references|group=a}}\n"
    assert clean("x\n<references group=\"a\" />\n").text == "x\n{{SV references|group=a}}\n"


def test_page_with_sv_references_loses_the_old_list():
    text = "Text.<ref>a</ref>\n\n==References==\n{{Reflist}}\n\n==Links==\n* x\n\n{{SV references}}\n"
    out = clean(text)
    assert out.text == "Text.<ref>a</ref>\n\n==Links==\n* x\n\n{{SV references}}\n"
    assert out.references == "removed"


def test_the_it_alias_counts_as_sv_references():
    text = "Testo.<ref>a</ref>\n<references/>\n\n{{SV note}}\n"
    assert clean(text, lang="it").text == "Testo.<ref>a</ref>\n\n{{SV note}}\n"


@pytest.mark.parametrize("text", [
    "x\n==References==\n{{reflist|refs=\n<ref name=\"a\">A</ref>\n}}\n",
    "x\n==References==\n<references>\n<ref name=\"a\">A</ref>\n</references>\n",
    "x\n==References==\n{{Reflist|liststyle=upper-roman}}\n",
    "x\n==Notes==\n{{Reflist|group=a}}\n==References==\n{{Reflist}}\n",
    "x\n==References==\nSee below.\n{{Reflist}}\n",
    "x\n==References==\n{{Reflist}}\n* {{Cite book|title=B}}\n",
    "x\n===References===\n{{Reflist}}\n",
    "x\n<div>{{Reflist}}</div>\n",
    "==History==\n===Early years===\nText.<ref>a</ref>\n<references />\n===Later years===\nMore.\n",
    "Text.<ref>a</ref>\n<references />\n===Later years===\nMore.\n",
])
def test_reference_lists_that_cannot_convert_are_left_and_reported(text):
    out = clean(text)
    assert out.text == text and out.references is None
    assert any("references" in r for r in out.reported)


def test_clean_is_idempotent():
    text = ("{{Short description|x}}\n'''X'''.<ref>{{Bible quote|ref=John 3:16}}</ref>\n\n==References==\n"
            "{{Reflist}}\n\n{{Authority control}}\n")
    once = clean(text)
    assert once.text == "'''X'''.<ref>{{SV Bible quote|ref=John 3:16}}</ref>\n\n{{SV references}}\n\n"
    again = clean(once.text)
    assert again.text == once.text and again.converted == 0 and again.removed == [] and again.references is None


# --- parse checks ---

def parsed(links=(), templates=(), categories=(), html=""):
    return {"text": html,
            "links": [{"ns": 0, "title": t, "exists": e} for t, e in links],
            "templates": [{"ns": 10, "title": "Template:" + t, "exists": e} for t, e in templates],
            "categories": [dict(category=c.replace(" ", "_"), **({"hidden": True} if h else {})) for c, h in categories]}


def test_checks_count_what_can_break():
    c = ca.checks(parsed(links=[("A", False), ("B", True), ("C", False)], templates=[("X", False), ("Y", True)],
                         categories=[("Film", False), ("Pages with reference errors", False),
                                     ("Articles with short description", True)],
                         html='<span class="error mw-ext-cite-error">x</span><span class="bibleQuoteRefBroken">'),
                  CONF)
    assert c.red_links == {"A", "C"} and c.missing_templates == {"X"} and sum(c.error_marks.values()) == 2
    assert c.tracking == {"Pages with reference errors", "Articles with short description"}


def test_a_failed_bibleget_quote_is_an_error_mark():
    c = ca.checks(parsed(html='<span class="bibleget-failed">Jn 3,16</span><sup class="bibleget-failed-marker"'
                              ' title="BibleGet does not know this Bible version.">[!]</sup>'), CONF)
    assert dict(c.error_marks) == {("bibleget-failed", "Jn 3,16"): 1}


def test_regressions():
    old = ca.checks(parsed(links=[("A", False)], categories=[("Bible quote template", False)]), CONF)
    same = ca.checks(parsed(links=[("A", False)]), CONF)
    assert ca.regressions(old, same) == []
    worse = ca.checks(parsed(links=[("A", False), ("B", False)], templates=[("Z", False)],
                             categories=[("Template Citazione bibbia con errori", False)],
                             html='<strong class="error">x</strong>'), CONF)
    found = ca.regressions(old, worse)
    assert len(found) == 4 and any("red link" in r and "B" in r for r in found)
    assert any("Template Citazione bibbia con errori" in r for r in found)


# --- run ---

CAT = re.compile(r"\[\[Category:([^\]|]+)")
LINK = re.compile(r"\[\[([^\]|:]+)")


class Client:
    """One wiki: pages are title -> (revid, text); `existing` the pages and templates that exist."""

    OLD = {"Bible quote", "Citazione bibbia", "Reflist", "Short description", "Portale"}

    def __init__(self, lang, pages, existing=(), main="Main Page"):
        self.lang, self.pages, self.existing, self.main = lang, dict(pages), set(existing) | self.OLD, main
        self.edits, self.parsed, self.conflict, self.mangle = [], [], set(), None
        self.texts = {}  # revid -> text

    def articles(self):
        return list(self.pages)

    def main_page(self):
        return self.main

    def revision(self, title):
        return self.pages.get(title)

    def revision_text(self, revid):
        return self.texts[revid]

    def parse(self, title, text):
        self.parsed.append(title)
        code = mwparserfromhell.parse(text)
        names = {ca.normal(str(t.name)) for t in code.filter_templates()}
        cats = [(c, False) for c in CAT.findall(text)]
        if "BROKEN" in text:
            cats.append(("Pages with reference errors", False))
        return parsed(links=[(t, t in self.existing) for t in LINK.findall(text)],
                      templates=[(n, n in self.existing or n.startswith("SV ")) for n in names],
                      categories=cats, html=text.replace("SCRIPTERROR", '<strong class="error">x</strong>'))

    def preview_categories(self, title, text):
        return set(CAT.findall(text))

    def page_categories(self, title):
        return set(CAT.findall(self.pages[title][1]))

    def revision_categories(self, revid):
        return set(CAT.findall(self.texts[revid]))

    def edit(self, title, text, summary, tags=None, createonly=False, baserevid=None):
        if title in self.conflict:
            raise SyncError(f"{self.lang}: editconflict: Edit conflict.")
        revid, _ = self.pages[title]
        assert baserevid == revid
        saved = self.mangle(text) if self.mangle else text
        self.pages[title] = (revid + 1, saved)
        self.texts[revid + 1] = saved
        self.edits.append((title, text, summary, tags))
        return {"result": "Success", "newrevid": revid + 1}


PAGE = ("{{Short description|A show}}\n'''X''' is a show.<ref>a</ref> {{Bible quote|ref=John 3:16}}\n\n"
        "==References==\n{{Reflist}}\n\n[[Category:Film]]")
CLEAN = "'''X''' is a show.<ref>a</ref> {{SV Bible quote|ref=John 3:16}}\n\n{{SV references}}\n\n[[Category:Film]]"
TREE = {"en": {"Film"}, "it": {"Film"}}


def run(clients, apply=True, pages=None):
    lines = []
    report = ca.run(clients, CONF, apply, pages=pages, tree=TREE, out=lines.append)
    return report, "\n".join(lines)


def test_dry_run_prints_a_diff_and_writes_nothing():
    en = Client("en", {"X": (10, PAGE)})
    report, text = run({"en": en}, apply=False)
    assert en.edits == [] and report.changed == 1 and report.saved == 0
    assert "-{{Short description|A show}}" in text and "+{{SV references}}" in text
    assert report.converted == 1 and report.removed == 1 and report.references == 1


def test_apply_saves_on_the_base_revision_with_a_summary_and_verifies():
    en = Client("en", {"X": (10, PAGE)})
    report, text = run({"en": en})
    assert report.saved == 1 and report.exit_code == 0
    title, sent, summary, tags = en.edits[0]
    assert sent == CLEAN and en.pages["X"] == (11, CLEAN) and tags == ca.TAG
    assert "SV Bible quote" in summary and "Short description" in summary and "SV references" in summary


def test_it_summary_is_in_italian():
    it = Client("it", {"X": (10, "Testo.\n{{Citazione bibbia|rif=Gv 3,16}}\n{{Portale|cinema}}\n")})
    run({"it": it})
    summary = it.edits[0][2]
    assert "Portale" in summary and "citazioni bibliche" in summary


def test_refused_when_red_links_increase():
    page = "{{Short description|x}}\nText [[Somewhere]]."
    en = Client("en", {"X": (10, page)})
    en.parse = (lambda real: lambda title, text: (
        parsed(links=[("Somewhere", False), ("Elsewhere", False)]) if "Short" not in text else real(title, text)))(en.parse)
    report, text = run({"en": en})
    assert en.edits == [] and report.refused and report.refused[0][:2] == ("en", "X")
    assert "red link" in report.refused[0][2] and "Elsewhere" in report.refused[0][2] and report.exit_code == 1


@pytest.mark.parametrize("marker", ["BROKEN", "SCRIPTERROR", "{{Nowhere}}"])
def test_refused_when_errors_or_missing_templates_appear(marker):
    en = Client("en", {"X": (10, "{{Short description|x}}\nText.")})
    real = en.parse
    en.parse = lambda title, text: real(title, text if "Short" in text else text + marker)
    report, _ = run({"en": en})
    assert en.edits == [] and len(report.refused) == 1


def test_changed_since_read_is_skipped_and_reported():
    en = Client("en", {"X": (10, PAGE)})
    real = en.parse

    def parse_then_someone_edits(title, text):
        out = real(title, text)
        if len(en.parsed) == 2:
            en.pages["X"] = (12, PAGE + "\nAn edit.")
        return out
    en.parse = parse_then_someone_edits
    report, _ = run({"en": en})
    assert en.edits == [] and report.failed and "changed" in report.failed[0][2]


def test_edit_conflict_is_reported_and_the_run_continues():
    en = Client("en", {"X": (10, PAGE), "Y": (20, PAGE)})
    en.conflict = {"X"}
    report, _ = run({"en": en})
    assert report.saved == 1 and report.failed[0][:2] == ("en", "X")


def test_a_saved_text_that_differs_is_reported():
    en = Client("en", {"X": (10, PAGE)})
    en.mangle = lambda text: text + "\nmerged"
    report, _ = run({"en": en})
    assert report.failed and "differs" in report.failed[0][2] and report.exit_code == 1


def test_a_saved_text_that_differs_stops_the_remaining_writes():
    en = Client("en", {"X": (10, PAGE), "Y": (20, PAGE)})
    en.mangle = lambda text: text + "\nmerged"
    report, lines = run({"en": en})
    assert len(en.edits) == 1 and report.saved == 0
    assert "stopped" in lines


def test_a_merged_save_that_lost_a_tree_category_stops_the_remaining_writes():
    en = Client("en", {"X": (10, PAGE), "Y": (20, PAGE)})
    en.mangle = lambda text: text.replace("[[Category:Film]]", "")
    report, lines = run({"en": en})
    assert len(en.edits) == 1 and report.saved == 0 and report.unsafe
    assert "stopped" in lines


def test_lost_tree_category_is_unsafe():
    en = Client("en", {"X": (10, PAGE)})
    en.mangle = lambda text: text.replace("[[Category:Film]]", "")
    report, _ = run({"en": en})
    assert report.failed or report.unsafe


def test_main_page_and_unchanged_pages_are_not_edited():
    en = Client("en", {"Main Page": (1, PAGE), "Clean": (2, CLEAN)})
    report, _ = run({"en": en})
    assert en.edits == [] and report.changed == 0 and "Main Page" not in en.parsed


def test_reported_items_are_printed_without_an_edit():
    en = Client("en", {"X": (1, "Text {{Bible quote|ref=Marc 8:27}}.")})
    report, text = run({"en": en})
    assert en.edits == [] and report.reported == 1 and "Marc 8:27" in text


def test_a_timeout_fails_that_page_and_the_run_continues():
    import requests
    en = Client("en", {"X": (10, PAGE), "Y": (20, PAGE)})
    real = en.parse

    def slow(title, text):
        if title == "X":
            raise requests.ReadTimeout("read timed out")
        return real(title, text)
    en.parse = slow
    report, _ = run({"en": en})
    assert report.failed[0][:2] == ("en", "X") and report.saved == 1


def test_only_the_named_pages():
    en = Client("en", {"X": (10, PAGE), "Y": (20, PAGE)})
    report, _ = run({"en": en}, pages={("en", "Y")})
    assert [e[0] for e in en.edits] == ["Y"]


def test_main_rejects_a_page_that_is_not_an_article(capsys):
    en = Client("en", {"X": (10, PAGE)})
    with pytest.raises(SystemExit):
        ca.main(["--wiki", "en", "--page", "Template:Reflist"], clients={"en": en})


def test_main_needs_credentials_to_apply(monkeypatch):
    monkeypatch.delenv("SV_EDITOR_USER", raising=False)
    monkeypatch.delenv("SV_EDITOR_PASSWORD", raising=False)
    assert ca.main(["--wiki", "en", "--apply"]) == 1


def test_main_dry_run(capsys):
    en = Client("en", {"X": (10, PAGE), "Y": (20, CLEAN)})
    assert ca.main(["--wiki", "en", "--page", "X"], clients={"en": en}) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and en.edits == [] and en.parsed == ["X", "X"]


# --- review findings (PR #11) ---

def test_a_link_without_a_namespace_is_visible():
    text = "Text.\n\n==Links==\n{{Portal|Film}}\n[[Category]]\n"
    assert clean(text).text == "Text.\n\n==Links==\n[[Category]]\n"
    text = "Text.\n\n==Links==\n{{Portal|Film}}\n[[:Category:Film]]\n"
    assert clean(text).text == "Text.\n\n==Links==\n[[:Category:Film]]\n"


@pytest.mark.parametrize("ref", ["John 3:16;2. Jean 1:1", "John 3:16;?1"])
def test_a_segment_that_is_neither_a_book_nor_a_chapter_is_reported(ref):
    call = "{{Bible quote|ref=" + ref + "}}"
    out = clean(call)
    assert out.text == call and out.converted == 0 and len(out.reported) == 1


def test_a_list_before_a_subsection_keeps_its_heading():
    text = "x<ref>a</ref>\n==Notes and references==\n{{Reflist}}\n===Primary sources===\n* A\n"
    out = clean(text)
    assert out.text == text and any("references" in r for r in out.reported)
    text = "x<ref>a</ref>\n==Notes and references==\n{{Reflist}}\n===Primary sources===\n* A\n{{SV references}}\n"
    assert clean(text).text == "x<ref>a</ref>\n==Notes and references==\n===Primary sources===\n* A\n{{SV references}}\n"


def test_a_nested_sv_list_does_not_replace_a_top_level_one():
    text = "x<ref group=a>n</ref>\n<references group=\"a\" />\n<includeonly>{{SV references|group=a}}</includeonly>\n"
    out = clean(text)
    assert out.text == text and out.references is None and out.reported


@pytest.mark.parametrize("group", ["A|B", "{{x}}"])
def test_a_group_that_cannot_be_one_parameter_is_reported(group):
    text = f"x\n<references group=\"{group}\" />\n"
    out = clean(text)
    assert out.text == text and out.references is None and out.reported


def test_a_list_with_prose_after_it_in_its_section_is_reported():
    text = "==History==\nBefore.<ref>a</ref>\n<references />\nAfter.\n==Other==\n"
    out = clean(text)
    assert out.text == text and out.references is None and out.reported
    # at the end of the lead, before the first heading, it converts
    assert clean("Lead.<ref>a</ref>\n<references />\n==Other==\n").text == \
        "Lead.<ref>a</ref>\n{{SV references}}\n==Other==\n"


def test_a_different_error_is_a_regression_even_when_the_count_stays():
    old = ca.checks(parsed(html='<strong class="error">Lua error: x</strong>'), CONF)
    new = ca.checks(parsed(html='<span class="bibleQuoteRefBroken" data-ref="Jn">Jn</span>'), CONF)
    assert ca.regressions(old, new)
    assert ca.regressions(old, old) == []


def test_an_edit_without_a_new_revision_is_not_counted_as_saved():
    en = Client("en", {"X": (10, PAGE)})
    real = en.edit

    def nochange(title, text, summary, tags=None, createonly=False, baserevid=None):
        real(title, text, summary, tags, createonly, baserevid)
        return {"result": "Success", "nochange": True}
    en.edit = nochange
    report, text = run({"en": en})
    assert report.saved == 0 and "no new revision" in text


def test_main_rejects_the_main_page(capsys):
    en = Client("en", {"Main Page": (1, PAGE), "X": (10, PAGE)})
    with pytest.raises(SystemExit):
        ca.main(["--wiki", "en", "--page", "Main Page"], clients={"en": en})


def test_a_named_page_gone_from_the_articles_is_reported():
    en = Client("en", {"X": (10, PAGE)})
    report, _ = run({"en": en}, pages={("en", "X"), ("en", "Gone")})
    assert report.failed == [("en", "Gone", "no longer an article")]
