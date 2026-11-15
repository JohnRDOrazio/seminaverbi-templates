from conftest import ROOT
from sync.pages import LANGS, desired_pages


def test_titles_for_wiki():
    pages = desired_pages(ROOT, "es", "abc1234")
    for title in ["Module:SV/Infobox", "Module:SV/Data", "Module:SV/Args", "Template:SV cite",
                  "Template:SV infobox/styles.css", "Template:SV managed", "Template:SV Bible quote",
                  "Template:SV cita bíblica"]:
        assert title in pages, title
    assert pages["Template:SV cita bíblica"] == "#REDIRECT [[Template:SV Bible quote]]"


def test_same_content_every_wiki():
    shared = [{t: c for t, c in desired_pages(ROOT, lang, "x").items() if not c.startswith("#REDIRECT")}
              for lang in LANGS]
    assert all(s == shared[0] for s in shared)


def test_notices():
    pages = desired_pages(ROOT, "en", "x")
    assert pages["Module:SV/Cite"].startswith("-- This page is managed in git")
    assert "<noinclude>{{SV managed}}" in pages["Template:SV cite"]
    assert "{{SV managed}}" not in pages["Template:SV managed"]
    assert pages["Template:SV infobox/styles.css"].startswith("/* This page is managed in git")


def test_sha_not_in_content():
    assert desired_pages(ROOT, "it", "aaaaaaa") == desired_pages(ROOT, "it", "bbbbbbb")
