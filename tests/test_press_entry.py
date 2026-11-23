"""Articles read from a feed entry (a blog whose pages are drawn by script), and the encoding of pages."""
import sqlite3
from html import escape

import pytest

from pipeline.press.extract import extract_entry
from pipeline.press.stages import Pages
from pipeline.press.store import PressStore
from tests.test_press_fetch import Resp
from tests.test_press_stages import ROBOTS, client

URL = "https://x.example/2024/04/un-film.html"
ATOM = "http://www.w3.org/2005/Atom"


def entry(content, url=URL, title="Un film", published="2024-04-24T15:45:00.000+02:00"):
    return (f"<entry xmlns='{ATOM}'><published>{published}</published><updated>2025-01-01T00:00:00Z</updated>"
            f"<title type='text'>{escape(title)}</title><content type='html'>{escape(content)}</content>"
            f"<link rel='self' href='https://x.example/feeds/1'/><link rel='alternate' href='{url}'/></entry>")


def feed(*entries):
    return f"<?xml version='1.0'?><feed xmlns='{ATOM}'><title>Blog</title>{''.join(entries)}</feed>"


def test_an_entry_gives_title_date_paragraphs_and_spans():
    content = ("<p>di Autore<o:p></o:p></p><div><span>Il film </span><i>Foglie al vento</i> è bello.</div>testo sciolto"
               "<br />con un a capo<br /><br /><div><div>Ultimo «Grande Film».</div></div><script>no</script>")
    page = extract_entry(feed(entry("altro", url="https://x.example/other.html"), entry(content)), URL, "it")
    assert (page.title, page.published, page.lang, page.canonical) == (
        "Un film", "2024-04-24T15:45:00.000+02:00", "it", URL)
    assert page.paragraphs == ["di Autore", "Il film Foglie al vento è bello.", "testo sciolto con un a capo",
                               "Ultimo «Grande Film»."]
    assert [(s.text, s.paragraph, s.kind) for s in page.spans] == [
        ("Foglie al vento", 1, "italic"), ("Grande Film", 3, "quoted")]
    assert page.article


def test_a_span_open_across_blocks_is_kept_per_paragraph():
    page = extract_entry(entry("<i>Uno<br /><br />Due</i> tre"), URL, "it")
    assert page.paragraphs == ["Uno", "Due tre"]
    assert [(s.text, s.paragraph) for s in page.spans] == [("Uno", 0), ("Due", 1)]


def test_the_entry_must_be_the_articles():
    assert extract_entry(feed(entry("x", url="https://x.example/other.html")), URL, "it") is None
    assert extract_entry(feed(), URL, "it") is None
    with pytest.raises(ValueError):
        extract_entry("<html>oops", URL, "it")
    with pytest.raises(ValueError):
        extract_entry("<!DOCTYPE feed><feed/>", URL, "it")


OUTLETS = [{"id": "o", "article_feed": "https://x.example/feeds/posts/default?path={path}"}]
FEED = "https://x.example/feeds/posts/default?path=/2024/04/un-film.html"


def pages(resp, outlets=OUTLETS):
    store = PressStore(sqlite3.connect(":memory:"))
    store.add_article(URL, "o", "it")
    return Pages(client({ROBOTS: Resp(""), FEED: resp, URL: resp}), outlets), store.article(URL)


def test_an_article_feed_outlet_is_read_through_its_feed():
    p, art = pages(Resp(feed(entry("<div>Testo.</div>"))))
    page, skip = p.get(art)
    assert skip is None and page.paragraphs == ["Testo."]
    assert [u for u, _ in p.client.session.calls] == [ROBOTS, FEED]


def test_an_article_the_feed_no_longer_has_is_skipped_for_good():
    p, art = pages(Resp(feed()))
    assert p.get(art) == (None, "not in the feed")


def test_a_broken_feed_is_tried_again_next_run(capsys):
    p, art = pages(Resp("<html>oops"))
    assert p.get(art) == (None, None)
    assert "not a feed" in capsys.readouterr().err


class Bytes(Resp):
    def __init__(self, content, ctype):
        super().__init__(content.decode("latin-1"), headers={"Content-Type": ctype})
        self.content = content


@pytest.mark.parametrize("ctype,body", [("text/html", "perché"), ("text/html; charset=UTF-8", "perchÃ©")])
def test_utf8_html_without_a_charset_is_not_read_as_latin1(ctype, body):
    """requests reads text/html with no charset as Latin-1 (L'Osservatore Romano's pages are UTF-8)."""
    p, art = pages(Bytes("<html><body><p>perché</p></body></html>".encode(), ctype), outlets=())
    assert p.get(art)[0].paragraphs == [body]


def test_latin1_bytes_stay_latin1():
    p, art = pages(Bytes("<html><body><p>perché</p></body></html>".encode("latin-1"), "text/html"), outlets=())
    assert p.get(art)[0].paragraphs == ["perché"]


def test_a_self_closing_block_ends_a_paragraph():
    page = extract_entry(entry("Prima<hr/>Seconda<p/><i>Terza</i>"), URL, "it")
    assert page.paragraphs == ["Prima", "Seconda", "Terza"]
    assert [(s.text, s.paragraph) for s in page.spans] == [("Terza", 2)]
