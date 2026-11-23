from pathlib import Path

from pipeline.press.extract import Page, extract, opening, passage, window

FIX = Path(__file__).parent / "fixtures" / "press"


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_title_date_lang_and_paragraphs():
    page = extract(load("article-it.html"), "https://example.test/x", "en")
    assert page.title == "Il cinema e la fede"
    assert page.published == "2026-11-02T08:30:00+01:00"
    assert page.lang == "it"
    assert len(page.paragraphs) == 3
    assert page.paragraphs[0].startswith("Nel nuovo libro di Tolkien, Il Signore degli Anelli,")
    assert not any("Menu" in p or "Copyright" in p or "Pubblicità" in p for p in page.paragraphs)


def test_fallbacks_title_date_lang_and_all_paragraphs():
    page = extract(load("article-en.html"), "https://example.test/en", "it")
    assert page.title == "Faith on screen"
    assert page.published == "2026-09-15"
    assert page.lang == "en"
    assert len(page.paragraphs) == 2


def test_jsonld_date_and_default_lang():
    html = ('<html><head><script type="application/ld+json">'
            '{"datePublished": "2026-01-05"}</script></head><body><p>x</p></body></html>')
    page = extract(html, "https://e.test/", "es")
    assert page.published == "2026-01-05" and page.lang == "es"


def test_extract_prefers_canonical():
    page = extract(load("article-it.html"), "https://example.test/x?utm=1", "en")
    assert page.canonical == "https://example.test/cultura/il-cinema-e-la-fede"
    fr = extract(load("article-fr.html"), "https://example.test/x", "en")
    assert fr.canonical == "https://example.test/arts/la-foi-au-cinema"
    en = extract(load("article-en.html"), "https://example.test/en", "en")
    assert en.canonical == "https://example.test/en"


def test_italic_quoted_and_wikipedia_spans():
    page = extract(load("article-it.html"), "https://example.test/x", "en")
    got = {(s.kind, s.text, s.paragraph, s.wiki) for s in page.spans}
    assert got == {
        ("italic", "Il Signore degli Anelli", 0, None),
        ("quoted", "Silence", 1, None),
        ("wikilink", "Scorsese", 1, ("it", "Martin Scorsese")),
    }


def test_quoted_rules_and_other_languages():
    en = extract(load("article-en.html"), "https://e.test/", "en")
    assert {(s.kind, s.text) for s in en.spans} == {
        ("italic", "The Mission"), ("quoted", "Moving and quiet")}
    fr = extract(load("article-fr.html"), "https://e.test/", "en")
    assert ("wikilink", "ce film", 0, ("en", "Of Gods and Men")) in {
        (s.kind, s.text, s.paragraph, s.wiki) for s in fr.spans}
    assert ("quoted", "Léon") in {(s.kind, s.text) for s in fr.spans}
    assert not any(s.text == "pas de majuscule" for s in fr.spans)


def test_malformed_markup_gives_best_effort_page():
    for html in ["", "<<<>>><p>unclosed <em>x", "<p><a href=>y</a></p></div></article>",
                 "<html lang><title>t", "\x00<p>bin\x00ary</p>"]:
        page = extract(html, "https://e.test/", "en")
        assert isinstance(page, Page)


def test_window_never_exceeds_150_words():
    paras = [" ".join(f"a{i}_{j}" for j in range(100)) for i in range(5)]
    for i in range(5):
        w = window(paras, i)
        assert len(w.split()) <= 150
    assert "a2_0" in window(paras, 2)
    short = ["one two", "three four", "five six"]
    assert window(short, 1) == "one two three four five six"
    assert len(window([" ".join(["w"] * 400)], 0).split()) == 150
    assert window([], 0) == "" and window(short, 9) == ""


def test_passage_is_at_most_25_words_and_keeps_the_needle():
    p = passage(" ".join(["w"] * 100 + ["Silence"] + ["w"] * 100), "Silence")
    assert len(p.replace("…", "").split()) <= 25 and "Silence" in p
    assert p.startswith("…") and p.endswith("…")
    assert passage("Silence is short", "Silence") == "Silence is short"
    assert passage(" ".join(["w"] * 100), "absent").replace("…", "").split() == ["w"] * 25
    long_needle = " ".join(f"n{i}" for i in range(40))
    assert len(passage("x " + long_needle, long_needle).replace("…", "").split()) <= 25


def test_opening_is_title_plus_first_words():
    page = Page("Titolo", None, "it", "u", [" ".join(["w"] * 200)], [])
    o = opening(page)
    assert o.startswith("Titolo ") and len(o.split()) == 150
