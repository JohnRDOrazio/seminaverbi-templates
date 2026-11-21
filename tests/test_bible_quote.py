import pytest

from lua_env import LuaEnv


def render(lang, **args):
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/BibleQuote", "_render", env.lua.table_from(args), lang)


@pytest.mark.parametrize("lang,version", [
    ("en", "NABRE"), ("it", "CEI2008"), ("es", "BLPD"), ("fr", "NVBSE"), ("de", "NVBSE"), ("pt", "NVBSE"),
])
def test_default_version_per_wiki(lang, version):
    assert f'data-version="{version}"' in render(lang, ref="Jn 3,16")


def test_note_only_when_language_differs():
    for lang in ("fr", "de", "pt"):
        assert 'class="sv-bible-note"' in render(lang, ref="Jn 3,16")
    for lang in ("en", "it", "es"):
        assert "sv-bible-note" not in render(lang, ref="Jn 3,16")
    assert 'class="sv-bible-note"' in render("it", ref="Gv 3,16", version="NVBSE")
    assert "(texte en latin ; traduction française pas encore disponible)" in render("fr", ref="Jean 3,16")


def test_no_note_once_a_native_version_is_the_default():
    def add_french(data):
        data.bible.versions[len(data.bible.versions) + 1] = "TOB"
        data.bible.version_lang.TOB = "fr"
        data.bible.defaults.fr = "TOB"

    env = LuaEnv(lang="fr", patch_data=add_french)
    out = env.call("Module:SV/BibleQuote", "_render", env.lua.table_from({"ref": "Jean 3,16"}), "fr")
    assert 'data-version="TOB"' in out
    assert "sv-bible-note" not in out


def test_inline_markup_matches_bibleget_js():
    assert render("en", ref="Jn 3:16") == (
        '<span class="bibleQuoteRef" data-ref="Jn 3:16" data-version="NABRE" data-inline="true">Jn 3:16</span>'
    )


def test_block_markup_matches_bibleget_js():
    out = render("en", ref="Jn 3:16", inline="0")
    assert out.startswith('<div class="bibleQuoteRef" data-ref="Jn 3:16" data-version="NABRE" data-inline="false">')
    assert out.count("<div></div>") == 12


def test_unsupported_version():
    out = render("en", ref="Jn 3:16", version="KJV")
    assert 'class="bibleQuoteRefBroken"' in out
    assert "[!]" in out
    assert "The Bible version 'KJV' is not supported by BibleGet." in out


def test_ref_is_escaped():
    out = render("en", ref='Jn 3,16"><b>')
    assert 'data-ref="Jn 3,16&quot;&gt;&lt;b&gt;"' in out
    assert "<b>" not in out


def test_missing_ref_is_error():
    out = render("it")
    assert out.startswith('<strong class="error">')
    assert "manca il parametro ref" in out


@pytest.mark.parametrize("lang,ref,query", [
    ("de", "Joh 10,11", "Johannes 10,11"),
    ("de", "1 Joh 4,2-3", "1Johannes 4,2-3"),
    ("de", "Offb 3,20", "Offenbarung 3,20"),
    ("pt", "2 Rs 23,29; 2 Cr 35,20-27", "2Reis 23,29; 2 Cr 35,20-27"),
    ("pt", "Jn 3,10", "Jonas 3,10"),
    ("pt", "Jo 4,14", "João 4,14"),
    ("pt", "Jó 15,35", "Job 15,35"),
    ("fr", "He 4,13", "Heb 4,13"),
    ("fr", "Jc 5,16", "Jas 5,16"),
    ("es", "1 R 19,4-8", "1Re 19,4-8"),
])
def test_book_names_bibleget_does_not_know_are_sent_in_a_form_it_does(lang, ref, query):
    out = render(lang, ref=ref)
    assert f'data-ref="{query}"' in out
    assert f">{ref}</span>" in out  # the reader still sees the wiki's own abbreviation


@pytest.mark.parametrize("lang,ref", [
    ("de", "Joel 2,28"),   # starts with "Jo" but is its own book
    ("pt", "Joel 2,28"),
    ("de", "Mk 1,1"),
    ("en", "Jn 3:16"),
])
def test_other_references_are_sent_unchanged(lang, ref):
    assert f'data-ref="{ref}"' in render(lang, ref=ref)
