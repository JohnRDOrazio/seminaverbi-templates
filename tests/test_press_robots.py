"""robots.txt as RFC 9309 reads it: its own examples, and the cases urllib.robotparser got wrong."""
import sqlite3

import pytest

from pipeline.press.fetch import PoliteClient
from pipeline.press.robots import MAX_BYTES, Robots
from pipeline.press.store import PressStore
from tests.test_press_fetch import UA, Clock, FakeSession, Resp

BOT = "SeminaVerbi-press/1.0 (+https://example/bot)"


def allowed(text, path, agent=BOT):
    return Robots(text, agent).allowed(path)


def star(*rules):
    return "User-agent: *\n" + "\n".join(rules) + "\n"


# ---- RFC 9309 section 5.1, simple example ----

SIMPLE = """User-Agent: *
Disallow: *.gif$
Disallow: /example/
Allow: /publications/

User-Agent: foobot
Disallow:/
Allow:/example/page.html
Allow:/example/allowed.gif

User-Agent: barbot
User-Agent: bazbot
Disallow: /example/page.html

User-Agent: quxbot
"""


@pytest.mark.parametrize("agent,path,want", [
    ("other/1", "/a.gif", False), ("other/1", "/a.gif?x", True), ("other/1", "/example/x", False),
    ("other/1", "/publications/x", True), ("other/1", "/", True),
    ("foobot/2", "/", False), ("foobot/2", "/example/page.html", True), ("foobot/2", "/example/allowed.gif", True),
    ("foobot/2", "/example/other", False),
    ("barbot", "/example/page.html", False), ("bazbot", "/example/page.html", False), ("barbot", "/example/", True),
    ("quxbot", "/example/page.html", True), ("quxbot", "/a.gif", True),
])
def test_rfc_simple_example(agent, path, want):
    assert allowed(SIMPLE, path, agent) is want


def test_rfc_longest_match():  # section 5.2
    text = "User-Agent: foobot\nAllow: /example/page/\nDisallow: /example/page/disallowed.gif\n"
    assert allowed(text, "/example/page/disallowed.gif", "foobot") is False
    assert allowed(text, "/example/page/other", "foobot") is True


def test_rfc_groups_of_one_agent_are_combined():  # section 2.2.1
    text = ("User-Agent: ExampleBot\nDisallow: /foo\nDisallow: /bar\n\n"
            "User-Agent: ExampleBot\nDisallow: /baz\n")
    assert [allowed(text, p, "examplebot/1") for p in ("/foo", "/bar", "/baz", "/qux")] == [False, False, False, True]
    star_twice = "User-agent: *\nDisallow: /a\nUser-agent: x\nDisallow: /\nUser-agent: *\nDisallow: /b\n"
    assert [allowed(star_twice, p) for p in ("/a", "/b", "/c")] == [False, False, True]


def test_no_group_for_us_and_no_star_allows():
    assert allowed("User-agent: other\nDisallow: /\n", "/x") is True


@pytest.mark.parametrize("rule,path,matches", [  # section 2.2.2's table, and 2.2.3's
    ("/foo/bar?baz=quz", "/foo/bar?baz=quz", True),
    ("/foo/bar?baz=https://foo.bar", "/foo/bar?baz=https%3A%2F%2Ffoo.bar", True),
    ("/foo/bar?baz=https%3A%2F%2Ffoo.bar", "/foo/bar?baz=https://foo.bar", True),
    ("/foo/bar/ツ", "/foo/bar/%E3%83%84", True),
    ("/foo/bar/%E3%83%84", "/foo/bar/%e3%83%84", True),
    ("/foo/bar/%E3%83%84", "/foo/bar/ツ", True),
    ("/foo/bar/%62%61%7A", "/foo/bar/baz", True),
    ("/foo/bar/baz", "/foo/bar/%62%61%7A", True),
    ("/a%2Fb", "/a/b", False),
    ("/a%2fb", "/a%2Fb", True),
    ("/path/file-with-a-%2A.html", "/path/file-with-a-*.html", True),
    ("/path/file-with-a-%2A.html", "/path/file-with-a-x.html", False),
    ("/path/foo-%24", "/path/foo-$", True),
    ("/a$b", "/a$b", True),
    ("/a b", "/a%20b", True),
    ("/%7Euser", "/~user", True),
    ("/%zz", "/%zz", True),
])
def test_percent_encoding(rule, path, matches):
    assert allowed(star(f"Disallow: {rule}"), path) is not matches


@pytest.mark.parametrize("rule,path,matches", [
    ("/fish", "/fish.html", True), ("/fish", "/Fish", False), ("/fish", "/a/fish", False),
    ("/fish*", "/fishheads", True), ("/fish/", "/fish", False),
    ("/*.php", "/x/y.php?a=1", True), ("/*.php$", "/y.php", True), ("/*.php$", "/y.php?a", False),
    ("/*.php$", "/a.php/b.php", True), ("/fish*.php", "/fishheads/c.php?p", True), ("/fish*.php", "/Fish.php", False),
    ("/*/private", "/a/private/x", True), ("/*/private", "/private", False),
    ("/$", "/", True), ("/$", "/a", False), ("/*$", "/a", True), ("*", "/a", True), ("/a**b", "/a-b", True),
    ("/ab*ba$", "/aba", False), ("/ab*ba$", "/abba", True), ("/a*a$", "/a", False), ("/*?s=", "/x?s=1", True),
    ("/x", "/x", True), ("", "/x", False), ("private", "/private", False), ("$", "/", False),
])
def test_wildcards(rule, path, matches):
    assert allowed(star(f"Disallow: {rule}"), path) is not matches


@pytest.mark.parametrize("rules", [("Allow: /p", "Disallow: /p"), ("Disallow: /p", "Allow: /p")])
def test_allow_wins_a_tie(rules):
    assert allowed(star(*rules), "/page") is True


@pytest.mark.parametrize("rules", [("Allow: /", "Disallow: /p"), ("Disallow: /p", "Allow: /")])
def test_the_longest_match_wins_whatever_the_order(rules):
    assert allowed(star(*rules), "/p") is False and allowed(star(*rules), "/q") is True


def test_robots_txt_itself_is_always_allowed():
    assert allowed(star("Disallow: /"), "/robots.txt") is True


@pytest.mark.parametrize("name", ["SeminaVerbi-press", "seminaverbi-PRESS", "SeminaVerbi-press/1.0", "SeminaVerbi"])
def test_our_group_replaces_the_general_one(name):
    text = star("Disallow: /all") + f"User-agent: {name}\nDisallow: /ours\n"
    assert allowed(text, "/ours") is False and allowed(text, "/all") is True


def test_our_exact_name_beats_its_prefix_and_parts_of_words_do_not_count():
    text = "User-agent: SeminaVerbi\nDisallow: /a\nUser-agent: SeminaVerbi-press\nDisallow: /b\n"
    assert allowed(text, "/a") is True and allowed(text, "/b") is False
    for name in ("Semina", "press", "SeminaVerbi-press-two", "SeminaVerbi-"):
        assert allowed(f"User-agent: {name}\nDisallow: /\n", "/a") is True, name


def test_other_records_and_blank_lines_do_not_end_a_group():
    assert allowed("User-agent: *\n\nSitemap: https://x/s.xml\nUser-agent: b\nCrawl-delay: 3\n\nDisallow: /p\n",
                   "/p") is False  # RFC 2.2.4: the Sitemap line does not separate the two names


def test_rules_before_any_group_and_malformed_lines_are_ignored():
    assert allowed("Disallow: /\nUser-agent: *\nAllow: /\n", "/p") is True
    text = "﻿# c\r\nuser-AGENT :  *  # all\rnonsense\nDisallow /x\nDISALLOW : /p # why\n: /q\nDisallow:/r\n"
    assert [allowed(text, p) for p in ("/x", "/p", "/q", "/r")] == [True, False, True, False]
    assert allowed("<html>\n<body>User-agent: none</body>", "/a") is True


def test_a_name_outside_the_grammar_still_starts_its_own_group():
    for name in ("360Spider", "Some Bot", ""):
        assert allowed(f"User-agent: *\nDisallow: /p\nUser-agent: {name}\nDisallow: /\n", "/a") is True, name


def test_the_size_cap_drops_what_is_beyond_it_and_the_line_it_cuts():
    head = star("Disallow: /in")
    filler = "#" * (MAX_BYTES - len(head) - len("\nAllow: /i"))
    text = head + filler + "\nAllow: /in/open\nDisallow: /out\n"
    assert MAX_BYTES >= 500 * 1024
    assert allowed(text, "/in/open") is False  # not "Allow: /i" either, though that would lose anyway
    assert allowed(text, "/out") is True
    cut = star("Allow: /ab") + "#" * (MAX_BYTES - len(star("Allow: /ab")) - len("\nDisallow: /a")) + "\nDisallow: /abcd\n"
    assert allowed(cut, "/a") is True


@pytest.mark.parametrize("text,delay", [
    (star("Crawl-delay: 30"), 30), (star("Crawl-delay: 2.5"), 2.5), (star("Disallow:"), 0),
    (star("Crawl-delay: soon"), 0), (star("Crawl-delay: -1"), 0), (star("Crawl-delay: nan"), 0),
    (star("Crawl-delay: inf"), 0), (star("Crawl-delay: 5") + star("Crawl-delay: 9"), 9),
    (star("Crawl-delay: 30") + "User-agent: SeminaVerbi-press\nCrawl-delay: 12\n", 12),
    (star("Crawl-delay: 30") + "User-agent: other\nCrawl-delay: 99\n", 30),
    (star("Disallow: /x") + "User-agent: other\nCrawl-delay: 99\n", 0),
])
def test_crawl_delay(text, delay):
    assert Robots(text, BOT).crawl_delay == delay


def test_many_wildcards_are_matched_quickly():
    assert allowed(star("Disallow: /" + "*a" * 500 + "$"), "/" + "a" * 4000 + "b") is True


# ---- in the client ----

X = "https://x.example"


def get(robots, url, routes=None, **kw):
    c = Clock()
    s = FakeSession({f"{X}/robots.txt": robots, **(routes or {})})
    pc = PoliteClient(s, UA, 10, store=PressStore(sqlite3.connect(":memory:")), clock=c, sleep=c.sleep)
    return pc.get("o", url), [u for u, _ in s.calls]


@pytest.mark.parametrize("path", ["/a/private", "/x/../a/private", "/x/%2E%2E/a/private", "/%61/private#f",
                                  "/q?b=/a/private"])
def test_the_client_matches_the_path_it_would_send(path):
    r, calls = get(Resp(star("Allow: /", "Disallow: /*/private")), X + path)
    assert r is None and calls == [f"{X}/robots.txt"]


def test_the_client_reads_robots_as_utf8_whatever_the_header_says():
    robots = Resp(star("Disallow: /perché").encode().decode("latin-1"))
    robots.content = star("Disallow: /perché").encode()
    r, _ = get(robots, f"{X}/perch%C3%A9")
    assert r is None


def test_a_redirect_is_matched_too():
    to = Resp(status=302, headers={"Location": "/a/private"})
    r, calls = get(Resp(star("Disallow: /*/private$")), f"{X}/in", {f"{X}/in": to})
    assert r is None and calls == [f"{X}/robots.txt", f"{X}/in"]
