import json

from conftest import ROOT
from lua_env import LuaEnv, tag

LANGS = ("en", "it", "es", "fr", "de", "pt")
CATEGORIES = json.loads((ROOT / "config" / "categories.json").read_text(encoding="utf-8"))
KEYS = ("proposal_title", "proposal_status_open", "proposal_status_approved", "proposal_status_created",
        "proposal_status_declined", "proposal_connection_direct", "proposal_connection_seeds",
        "proposal_connection_entertainment", "proposal_connection_none", "proposal_field", "proposal_themes",
        "proposal_outline", "proposal_sources", "proposal_first_wiki", "proposal_reason", "proposal_category")

BASE = {
    "qid": "Q42", "label_en": "The Gospel According to St. Matthew", "label_it": "Il Vangelo secondo Matteo",
    "domain": "film", "connection": "direct", "connection_p": "0.83", "field": "audiovisual-media",
    "field_p": "0.91", "theme1": "faith", "theme1_p": "0.72", "theme1_status": "applied",
    "theme1_evidence": "Pasolini filmed the text verbatim.", "theme1_source": "https://en.wikipedia.org/wiki/X",
    "theme2": "cross", "theme2_p": "0.55", "theme2_status": "author", "theme2_evidence": "", "theme2_source": "",
    "outline": "Plot ; Reception ; Gospel", "sources": "https://a.example/x https://b.example/y",
    "first_wiki": "it",
}


def i18n(lang):
    return json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def names(key, lang):
    def find(items):
        for item in items:
            if item["key"] == key:
                return item["names"][lang]
            hit = find(item.get("children", []))
            if hit:
                return hit
    return find(CATEGORIES["fields"]) or find(CATEGORIES["themes"])


def render(lang="en", **overrides):
    args = {**BASE, **overrides}
    args = {k: v for k, v in args.items() if v is not None}
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/Proposal", "main", env.frame({}, args))


def test_open_proposal_has_the_vote_button():
    out = render()
    assert tag("vote") in out
    assert 'class="sv-proposal notheme"' in out
    assert "[https://www.wikidata.org/wiki/Q42 Q42]" in out
    assert tag("templatestyles", src="SV proposal/styles.css") in out
    assert i18n("en")["proposal_status_open"] in out
    assert "[[Category:SV proposals (open)]]" in out


def test_declined_shows_reason_and_no_button():
    out = render(status="declined", reason="Not a film [[x]]")
    assert tag("vote") not in out
    assert i18n("en")["proposal_reason"] in out
    assert "Not a film &#91;&#91;x&#93;&#93;" in out
    assert "[[Category:SV proposals (declined)]]" in out


def test_approved_has_no_button():
    out = render(status="approved")
    assert tag("vote") not in out and "[[Category:SV proposals (approved)]]" in out


def test_created_links_the_article_on_the_first_wiki():
    out = render(status="created", article="Il Vangelo secondo Matteo (film 1964)")
    assert tag("vote") not in out
    assert "https://it.seminaverbi.bibleget.io/wiki/Il_Vangelo_secondo_Matteo_(film_1964)" in out
    assert "[[Category:SV proposals (created)]]" in out


def test_theme_names_come_from_the_data_in_page_language():
    out = render("it")
    assert names("faith", "it") == "Fede"
    assert "Fede" in out and "La Croce" in out
    assert names("audiovisual-media", "it") in out
    assert "Faith" not in out


def test_category_uses_english_status_on_every_wiki():
    for lang in LANGS:
        assert "[[Category:SV proposals (open)]]" in render(lang)


def test_label_falls_back_to_en():
    out = render("fr")
    assert "The Gospel According to St. Matthew" in out
    assert "Il Vangelo secondo Matteo" not in out
    assert "Il Vangelo secondo Matteo" in render("it")


def test_unknown_status_is_treated_as_open():
    out = render(status="bogus")
    assert tag("vote") in out and "[[Category:SV proposals (open)]]" in out


def test_probabilities_are_percentages():
    out = render()
    assert "83%" in out and "91%" in out and "72%" in out and "55%" in out
    assert "0.83" not in out


def test_theme_without_source_has_no_link_and_empty_excerpt_renders_nothing():
    out = render()
    assert "[https://en.wikipedia.org/wiki/X]" in out
    assert "Pasolini filmed the text verbatim." in out
    assert out.count("sv-proposal-excerpt") == 1  # only theme 1 has an excerpt


def test_excerpt_without_source_is_not_linked():
    out = render(theme1_source=None)
    assert "Pasolini filmed the text verbatim." in out and "wikipedia.org" not in out


def test_outline_and_sources():
    out = render()
    for section in ("Plot", "Reception", "Gospel"):
        assert f"<li>{section}</li>" in out
    assert "[https://a.example/x" in out and "[https://b.example/y" in out


def test_connection_is_localised():
    assert i18n("it")["proposal_connection_direct"] in render("it")
    assert i18n("en")["proposal_connection_direct"] in render("en")


def test_markup_in_excerpt_is_escaped():
    out = render(theme1_evidence="a {{evil}} [[b]]")
    assert "{{evil}}" not in out and "[[b]]" not in out


def test_every_language_has_the_keys():
    for lang in LANGS:
        strings = i18n(lang)
        assert [k for k in KEYS if not strings.get(k)] == []
    assert "$1" in i18n("en")["proposal_category"]


def test_unknown_field_and_theme_keys_are_shown_literally():
    out = render(field="[[Category:Foo]]", theme1="[[File:X.png]]", theme2="''x'' __NOINDEX__")
    assert "[[Category:Foo]]" not in out and "[[File:X.png]]" not in out
    assert "''x''" not in out and "__NOINDEX__" not in out
    assert out.count("[[Category:") == 1  # only the proposal category
