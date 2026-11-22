import pytest

from lua_env import LuaEnv, tag


def render(lang, **args):
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/BibleQuote", "_render", env.lua.table_from(args), lang, env.frame())


def biblequote(ref, version, inline="true", query=None):
    """What the module emits for a quote: the BibleGet extension's tag, shown text in the body."""
    return tag("biblequote", ref, ref=query or ref, version=version, inline=inline)


DEFAULTS = {"en": "NABRE", "it": "CEI2008", "es": "BLPD", "fr": "NVBSE", "de": "NVBSE", "pt": "NVBSE"}


@pytest.mark.parametrize("lang,version", DEFAULTS.items())
def test_default_version_per_wiki(lang, version):
    assert f"version={version}" in render(lang, ref="Jn 3,16")


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
    out = env.call("Module:SV/BibleQuote", "_render", env.lua.table_from({"ref": "Jean 3,16"}), "fr", env.frame())
    assert "version=TOB" in out
    assert "sv-bible-note" not in out


def test_inline_is_the_extension_tag():
    assert render("en", ref="Jn 3:16") == biblequote("Jn 3:16", "NABRE")


@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_block_is_the_extension_tag(value):
    assert render("en", ref="Jn 3:16", inline=value) == biblequote("Jn 3:16", "NABRE", inline="false")


def test_note_follows_the_tag():
    assert render("fr", ref="Jean 3,16").startswith(biblequote("Jean 3,16", "NVBSE") + ' <small class="sv-bible-note">')


def test_unsupported_version_is_the_extension_fallback_without_a_request():
    out = render("en", ref="Jn 3:16", version="KJV")
    assert "TAG:" not in out
    assert out == (
        '<span class="bibleget-failed">Jn 3:16</span><sup class="bibleget-failed-marker"'
        " title=\"The Bible version 'KJV' is not supported by BibleGet.\">[!]</sup>"
    )


def test_unsupported_version_escapes_the_ref():
    out = render("en", ref='Jn 3,16"><b>', version="KJV")
    assert "<b>" not in out
    assert "Jn 3,16&quot;&gt;&lt;b&gt;" in out


def test_ref_goes_to_the_extension_unescaped():
    # frame:extensionTag hands values to the extension as they are; the extension escapes them.
    out = render("en", ref='Jn 3,16"><b>')
    assert out == biblequote('Jn 3,16"><b>', "NABRE")


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
    # BibleGet gets the form it resolves; the reader still sees the wiki's own abbreviation (the body)
    assert out.startswith(biblequote(ref, DEFAULTS[lang], query=query))


@pytest.mark.parametrize("lang,ref", [
    ("de", "Joel 2,28"),   # starts with "Jo" but is its own book
    ("pt", "Joel 2,28"),
    ("de", "Mk 1,1"),
    ("en", "Jn 3:16"),
    ("en", "1 Thessalonians 5:18"),
    # BibleGet resolves these itself (endpoint migration 061)
    ("en", "1 Thes 4:13-14"),
    ("en", "2 Thes 3:10"),
    ("fr", "Actes des Apôtres 17,16-34"),
    ("pt", "Atos dos Apóstolos 17,16-34"),
    ("pt", "Atos 2,1-4"),
])
def test_other_references_are_sent_unchanged(lang, ref):
    assert f"ref={ref}," in render(lang, ref=ref)
