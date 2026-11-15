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


def test_title_escaped():
    out = render(url="https://x.org/a]b c", title='A "B" <i>')
    assert "&quot;B&quot; &lt;i&gt;" in out
    assert "[https://x.org/a%5Db%20c " in out
    assert "<i>" not in out


def test_missing_title_is_error():
    out = render(url="https://x.org")
    assert out.startswith('<strong class="error">')
