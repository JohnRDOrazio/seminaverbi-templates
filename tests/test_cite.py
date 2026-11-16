from lua_env import LuaEnv


def render(lang="en", **args):
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/Cite", "_render", env.lua.table_from(args), lang)


def test_web_minimal():
    assert render(url="https://x.org", title="T") == '<cite class="sv-cite">"[https://x.org T]".</cite>'


def test_book_full():
    out = render(type="book", author="Humphrey Carpenter", date="1991", title="La vita di J.R.R. Tolkien",
                 publisher="Ares", isbn="8881550431", pages="12-14", language="it")
    assert out == ('<cite class="sv-cite">Humphrey Carpenter (1991). \'\'La vita di J.R.R. Tolkien\'\'. Ares. '
                   'ISBN 8881550431. pp. 12-14. (it)</cite>')


def test_web_with_work_archive_and_access():
    out = render(url="https://a.org/x", title="Tolkien e la fede", work="Avvenire", **{
        "archive-url": "https://web.archive.org/a", "archive-date": "2020-09-16", "access-date": "2026-11-14"})
    assert out == ('<cite class="sv-cite">"[https://a.org/x Tolkien e la fede]". \'\'Avvenire\'\'. '
                   'Archived [https://web.archive.org/a j F Y|2020-09-16]. Accessed j F Y|2026-11-14.</cite>')


def test_iso_dates_localized():
    out = render(lang="it", url="https://x.org", title="T", **{"access-date": "2026-11-14"})
    assert "Consultato il j F Y|2026-11-14." in out


def test_quote():
    assert render(title="T", quote="Not all those who wander are lost").endswith(
        '"T". «Not all those who wander are lost»</cite>')


def test_url_encoded_and_bracket_in_link_text():
    out = render(url="https://x.org/a]b c", title="A [B] C")
    assert "[https://x.org/a%5Db%20c A [B&#93; C]" in out


def test_text_params_stay_wikitext():
    # MediaWiki's Sanitizer makes text safe; escaping it again breaks entities and strip markers
    marker = "\x7f'\"`UNIQ--nowiki-00000001-QINU`\"'\x7f"
    out = render(title='A "B" <i>x</i>', pages="12&ndash;15", quote=marker, author="X&nbsp;Y")
    assert 'A "B" <i>x</i>' in out
    assert "12&ndash;15" in out
    assert marker in out
    assert "X&nbsp;Y" in out


def test_missing_title_is_error():
    out = render(url="https://x.org")
    assert out.startswith('<strong class="error">')
