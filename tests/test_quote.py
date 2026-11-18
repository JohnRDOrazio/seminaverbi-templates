import json

from conftest import ROOT
from lua_env import LuaEnv


def quote(lang="en", **args):
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/Quote", "quote", env.frame({}, args))


def msg(lang, key):
    return json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))[key]


def test_quote_basic():
    out = quote(text="T", author="A", source="[https://x S]")
    assert '<blockquote class="sv-quote">' in out
    assert "T" in out
    src = out[out.index('<div class="sv-quote-source">'):]
    assert src.index("A") < src.index("[https://x S]")


def test_quote_text_is_wikitext():
    assert "'''bold''' [[Link]]" in quote(text="'''bold''' [[Link]]")


def test_quote_original():
    out = quote(text="T", original="O", original_lang="it")
    orig = '<div class="sv-quote-original" lang="it" dir="ltr">O</div>'
    assert orig in out
    assert out.index(orig) < out.index("T</div>")


def test_quote_rtl():
    assert 'lang="he" dir="rtl"' in quote(text="T", original="O", original_lang="he")


def test_quote_bad_lang_dropped():
    out = quote(text="T", original="O", original_lang='x" onclick="y')
    assert "lang=" not in out and "onclick" not in out
    assert ">O</div>" in out


def test_quote_no_source_line_without_author_or_source():
    assert "sv-quote-source" not in quote(text="T")


def test_quote_missing_text():
    out = quote("it", author="A")
    assert out.startswith('<strong class="error">')
    assert msg("it", "quote_missing_text") in out
    assert "blockquote" not in out


def test_quote_accepts_multi_subtag_and_numeric_region():
    assert 'lang="he-hebr-il" dir="rtl"' in quote(text="T", original="O", original_lang="he-Hebr-IL")
    assert 'lang="es-419" dir="ltr"' in quote(text="T", original="O", original_lang="es-419")


def test_quote_rejects_attribute_breakers():
    for bad in ('he" dir="ltr', "he'x", "he x", "he-<b>", "-he", "he--il"):
        out = quote(text="T", original="O", original_lang=bad)
        assert "lang=" not in out, bad


def test_a_quote_with_its_original_names_each_language_below_it():
    out = quote(lang="it", text="Fu la Pietà", original="It was Pity", original_lang="en", author="Gandalf")
    original, translation = out.index("It was Pity"), out.index("Fu la Pietà")
    en_label = out.index('<div class="sv-quote-lang">inglese</div>')
    it_label = out.index('<div class="sv-quote-lang">italiano</div>')
    assert original < en_label < translation < it_label < out.index("Gandalf")


def test_language_names_are_in_the_wikis_language():
    out = quote(lang="de", text="Es war Mitleid", original="It was Pity", original_lang="en")
    assert '<div class="sv-quote-lang">Englisch</div>' in out and '<div class="sv-quote-lang">Deutsch</div>' in out


def test_a_translation_in_another_language_than_the_wiki():
    out = quote(lang="it", text="Pietas", text_lang="la", original="Pity", original_lang="en")
    assert '<div class="sv-quote-lang">latino</div>' in out and "italiano" not in out
    assert '<div class="sv-quote-text" lang="la" dir="ltr">' in out


def test_a_quote_without_original_has_no_language_labels():
    assert "sv-quote-lang" not in quote(lang="it", text="T")


def test_an_unknown_language_gets_no_label():
    out = quote(lang="it", text="T", original="O", original_lang="xx")
    assert out.count("sv-quote-lang") == 1  # only the translation's "italiano"


def test_quote_styles_load_through_templatestyles():
    from lua_env import tag
    assert quote(text="T").startswith(tag("templatestyles", src="SV quote/styles.css"))
