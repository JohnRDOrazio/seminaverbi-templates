import shutil

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
    shared = [{t: c for t, c in desired_pages(ROOT, lang, "x").items()
               if not c.startswith("#REDIRECT") and not t.startswith(("Category:", "Project:"))}
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


def test_other_files_in_templates_are_ignored(tmp_path):
    for d in ("modules", "templates", "config", "i18n", "project"):
        shutil.copytree(ROOT / d, tmp_path / d)
    (tmp_path / "templates" / ".DS_Store").write_bytes(b"\x00\x05\x16\x07\xff\xfe")
    assert desired_pages(tmp_path, "it", "x") == desired_pages(ROOT, "it", "x")


def test_tracking_categories_are_hidden_on_every_wiki():
    import json
    for lang in LANGS:
        msgs = json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        pages = desired_pages(ROOT, lang, "x")
        for key in ("category_not_on_wiki", "category_not_tree", "category_two_fields",
                    "infobox_missing_category", "infobox_unknown_type"):
            text = pages["Category:" + msgs[key]]
            assert text.startswith("__HIDDENCAT__") and "{{SV managed}}" in text
            assert msgs["tracking_category_text"] in text


def test_cathopedia_category_is_created_hidden_on_every_wiki():
    import json
    for lang in LANGS:
        msgs = json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        text = desired_pages(ROOT, lang, "x")["Category:" + msgs["cathopedia_category"]]
        assert text.startswith("__HIDDENCAT__") and "{{SV managed}}" in text
        assert msgs["cathopedia_category_text"] in text
        assert msgs["tracking_category_text"] not in text


def test_bibleget_source_page_on_every_wiki():
    # The page each quote's source line links to (BibleGet's bibleget-source-page): where the quotes
    # come from, in the wiki's language, with the links off the wiki kept to this one page.
    for lang in LANGS:
        text = desired_pages(ROOT, lang, "x")["Project:BibleGet"]
        assert text.startswith("{{SV managed}}\n"), lang
        assert "https://www.bibleget.io" in text, lang
        assert "https://catholicdigitalcommons.org" in text, lang
        for sigla in ("NABRE", "CEI2008", "BLPD", "NVBSE", "DRB", "LUZZI", "VGCL"):
            assert f"|| {sigla} ||" in text, (lang, sigla)
