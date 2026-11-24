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
    assert len(p.split()) <= 25 and "Silence" in p
    assert p.startswith("…") and p.endswith("…")
    assert len(p.replace("…", "").split()) == 25
    assert passage("Silence is short", "Silence") == "Silence is short"
    assert passage(" ".join(["w"] * 100), "absent").replace("…", "").split() == ["w"] * 25
    long_needle = " ".join(f"n{i}" for i in range(40))
    assert len(passage("x " + long_needle, long_needle).split()) <= 25


def test_opening_is_title_plus_first_words():
    page = Page("Titolo", None, "it", "u", [" ".join(["w"] * 200)], [])
    o = opening(page)
    assert o.startswith("Titolo ") and len(o.split()) == 150


def test_every_cut_stays_within_25_raw_whitespace_tokens():
    body = ["w"] * 60
    cases = [
        " ".join(["Silence"] + body),            # cut at the end only
        " ".join(body + ["Silence"]),            # cut at the start only
        " ".join(body + ["Silence"] + body),     # both ends
        "Silence w w",                           # no cut
    ]
    for para in cases:
        p = passage(para, "Silence")
        assert len(p.split()) <= 25 and "Silence" in p


def test_passage_is_accepted_by_the_store():
    import sqlite3

    from pipeline.press.store import Mention, PressStore
    from pipeline.queue import Queue  # noqa: F401

    s = PressStore(sqlite3.connect(":memory:"))
    s.add_article("u", "sir", "it")
    p = passage(" ".join(["w"] * 100 + ["Silence"] + ["w"] * 100), "Silence")
    s.add_mention(Mention("u", "Q1", "Silence", "work", "film", 0.7, [p], "candidate"))


def test_window_of_a_long_paragraph_is_centred_on_the_mention():
    """A mention late in a long paragraph is inside what Jev reads."""
    para = " ".join(f"w{i}" for i in range(400)) + " Silence " + " ".join(f"v{i}" for i in range(400))
    w = window([para], 0, needle="Silence")
    assert "Silence" in w and len(w.split()) == 150
    assert w.split().index("Silence") in (74, 75)
    late = " ".join(f"w{i}" for i in range(300)) + " *The Mission*, alla fine."
    w = window([late], 0, needle="The Mission")
    assert "*The Mission*," in w and len(w.split()) <= 150
    assert window([para], 0).split()[0] == "w0"  # no needle: the first words, as before
    assert window([para], 0, needle="absent").split()[0] == "w0"


def test_passage_anchors_on_the_whole_title_not_a_stray_word():
    para = " ".join(["the"] + [f"w{i}" for i in range(60)] + ["*The", "Mission*,"] + [f"v{i}" for i in range(60)])
    p = passage(para, "The Mission")
    assert "*The Mission*," in p and len(p.split()) <= 25
    assert "«Silenzio»" in passage(" ".join(["w"] * 60 + ["«Silenzio»"] + ["w"] * 60), "Silenzio")
    assert "dell'Infinito" in passage(" ".join(["w"] * 60 + ["dell'Infinito"] + ["w"] * 60), "Infinito")
    # truly absent: the paragraph's first words
    assert passage(" ".join(["the"] + ["w"] * 60), "The Mission").startswith("the w")


def test_an_inline_script_keeps_the_paragraph():
    page = extract("<html><body><p>Review <script>var x = 1;</script>of <i>Silence</i>.</p></body></html>",
                   "https://example.test/s", "en")
    assert page.paragraphs == ["Review of Silence."]
    assert [(s.text, s.kind) for s in page.spans] == [("Silence", "italic")]


def test_a_skipped_block_still_ends_the_paragraph():
    page = extract("<html><body><p>One<nav>menu</nav>two</p></body></html>", "https://example.test/n", "en")
    assert page.paragraphs == ["One"]


def test_locate_counts_casefold_expansion():
    from pipeline.press.extract import locate
    assert locate(["Straße", "dell'Infinito"], "Infinito") == (1, 1)


def _head(og_type=None, *ld):
    """A page with the measured head signals: og:type and the JSON-LD blocks (synthetic text)."""
    meta = f'<meta property="og:type" content="{og_type}">' if og_type else ""
    scripts = "".join(f'<script type="application/ld+json">{block}</script>' for block in ld)
    return f"<html><head>{meta}{scripts}</head><body><article><p>Testo.</p></article></body></html>"


# the head signals of one article and one section page per outlet, as measured on 2026-11-23
ARTICLES = {
    "aleteia": _head("article", '{"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, '
                                '{"@type": "NewsArticle"}, {"@type": "Organization"}]}'),
    "avvenire": _head("article", '{"@type": "NewsArticle", "author": {"@type": "Person"}}',
                      '{"@type": "BreadcrumbList"}'),
    "famigliacristiana": _head("article", '{"@type": "NewsArticle"}', '{"@type": "BreadcrumbList"}'),
    "kto": _head("video.other", '{"@type": "Organization"}', '{"@type": "VideoObject"}'),
    "ncregister": _head("article", '[{"@type": "NewsMediaOrganization"}, {"@type": "WebSite"}]',
                        '{"@type": "NewsArticle"}'),
    "osservatore": _head("website", '{"@type": "WebSite"}', '{"@type": "NewsArticle"}'),
    "romasette": _head("article", '{"@graph": [{"@type": ["Article", "BlogPosting"]}, {"@type": "WebPage"}]}'),
    "sir": _head("article", '{"@graph": [{"@type": "BlogPosting"}, {"@type": "WebSite"}]}'),
    "vaticannews": _head("article", '{"@type": "http://schema.org/NewsArticle"}'),
}
SECTIONS = {
    "aleteia": _head("website", '{"@type": "WebSite"}', '{"@type": "Organization"}'),
    "avvenire": _head(None, '{"@type": "BreadcrumbList"}'),
    "famigliacristiana": _head("section", '{"@type": "BreadcrumbList"}'),
    "ncregister": _head("website", '[{"@type": "NewsMediaOrganization"}, {"@type": "WebSite"}]'),
    "osservatore": _head("website", '{"@type": "WebSite"}'),
    "romasette": _head("website", '{"@graph": [{"@type": "WebPage"}, {"@type": "WebSite"}]}'),
    "sir": _head(None, '{"@graph": [{"@type": "CollectionPage", "hasPart": [{"@type": "NewsArticle"}]}]}'),
    "vaticannews": _head("website"),
}


def test_an_article_page_is_an_article():
    for outlet, html in ARTICLES.items():
        assert extract(html, "https://e.test/a", "it").article, outlet


def test_a_section_or_index_page_is_not_an_article():
    """Famiglia Cristiana's sitemap listed its sections (/cultura-e-spettacoli/musica, ...), which were filtered
    and linked like articles. A page whose JSON-LD names no article type at its top level (an article listed
    inside a collection does not count), or, without JSON-LD, whose og:type is not an article's, is not one."""
    for outlet, html in SECTIONS.items():
        assert not extract(html, "https://e.test/s", "it").article, outlet


def test_a_page_without_any_signal_counts_as_an_article():
    assert extract("<html><body><p>Testo.</p></body></html>", "https://e.test/a", "it").article
    assert extract(_head(None, "{not json"), "https://e.test/a", "it").article


def test_a_jsonld_block_with_a_raw_control_character_still_counts():
    """L'Osservatore Romano's NewsArticle block carried a raw carriage return inside a string (invalid strict JSON): the
    article was skipped as a section in the 2026-11-23 measurement."""
    html = _head("website", '{"@type": "WebSite"}', '{"@type": "NewsArticle", "headline": "Collodi\ral Tufello"}')
    assert extract(html, "https://e.test/a", "it").article


def test_an_unreadable_jsonld_block_leaves_the_page_an_article():
    """A block that is not JSON at all may be the article's own: the page is not judged a section on the rest."""
    assert extract(_head("website", '{"@type": "WebSite"}', '{"@type": "NewsArticle",'), "https://e.test/a",
                   "it").article


PAGE = "https://e.test/cinema"


def _graph(*nodes, context='"https://schema.org"'):
    import json
    return _head("website", '{"@context": %s, "@graph": %s}' % (context, json.dumps(list(nodes))))


def _article(html, url=PAGE):
    return extract(html, url, "it").article


ART1 = {"@type": "NewsArticle", "@id": "https://e.test/a1#article", "url": "https://e.test/a1"}


def test_articles_listed_by_a_collection_in_the_same_graph_do_not_make_it_an_article():
    collection = {"@type": "CollectionPage", "@id": PAGE + "/#page", "url": PAGE + "/"}
    assert not _article(_graph({**collection, "hasPart": [{"@id": ART1["@id"]}]}, ART1))
    items = [{"@type": "ListItem", "position": 1, "item": {"@id": ART1["@id"]}}]
    assert not _article(_graph({**collection, "mainEntity": {"@type": "ItemList", "itemListElement": items}}, ART1))
    # the list by URL, on a page with no node of its own for this URL
    assert not _article(_graph({"@type": "ItemList", "itemListElement": [{"@type": "ListItem",
                                                                           "url": "https://e.test/a1"}]}, ART1))
    # the page's own node is a collection: an article that only says it is part of it does not count
    assert not _article(_graph(collection, {**ART1, "isPartOf": {"@id": collection["@id"]}}))


def test_a_listing_through_graph_level_list_items_is_read():
    entry = {"@type": "ListItem", "@id": "#entry", "item": {"@id": ART1["@id"]}}
    listing = {"@type": "ItemList", "itemListElement": [{"@id": "#entry"}]}
    assert not _article(_graph(listing, entry, ART1))
    # an entry that leads back to its own list ends
    loop = {**entry, "item": {"@type": "ItemList", "itemListElement": [{"@id": "#entry"}, {"@id": ART1["@id"]}]}}
    assert not _article(_graph(listing, loop, ART1))
    # a breadcrumb's entries name the page itself
    own = {"@type": "Article", "@id": PAGE + "#article"}
    crumb = {"@type": "ListItem", "@id": "#entry", "item": {"@id": own["@id"]}}
    assert _article(_graph({"@type": "BreadcrumbList", "itemListElement": [{"@id": "#entry"}]}, crumb, own))


def test_a_collection_listed_in_the_same_graph_as_the_pages_own_article_still_counts():
    own = {"@type": "NewsArticle", "@id": PAGE + "#article", "mainEntityOfPage": {"@id": PAGE + "/#webpage"}}
    section = {"@type": "CollectionPage", "url": "https://e.test/all", "hasPart": [{"@id": own["@id"]}]}
    assert _article(_graph(section, own))


def test_a_webpage_and_its_article_count():
    webpage = {"@type": "WebPage", "@id": PAGE + "/", "url": PAGE + "/"}
    crumbs = {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "item": PAGE + "#article"}]}
    for link in ({"isPartOf": {"@id": PAGE + "/"}}, {"mainEntityOfPage": {"@id": PAGE + "/"}}, {}):
        assert _article(_graph(webpage, crumbs, {"@type": "Article", "@id": PAGE + "#article", **link})), link
    assert _article(_graph({**webpage, "mainEntity": {"@id": PAGE + "#article"}},
                           {"@type": "Article", "@id": PAGE + "#article"}))


def test_a_single_object_graph_and_non_object_entries():
    assert _article(_head("website", '{"@graph": {"@type": "NewsArticle"}}'))
    assert not _article(_head("article", '{"@graph": {"@type": "CollectionPage"}}'))
    assert _article(_head("website", '[3, null, "x", {"@graph": [1, null, "NewsArticle", {"@type": "Article"}]}]'))
    assert not _article(_head("website", '{"@graph": [null, "NewsArticle", {"@type": "WebPage", "@id": 4, '
                                         '"url": ["x"], "mainEntityOfPage": 7, "hasPart": 1}]}'))


def test_compact_and_full_types_are_resolved():
    for context in ('{"schema": "https://schema.org/"}', '["https://x.test/c", {"schema": {"@id": "http://schema.org/"}}]'):
        assert _article(_graph({"@type": "schema:NewsArticle"}, context=context))
        assert not _article(_graph({"@type": "schema:CollectionPage"}, context=context))
    assert _article(_head("website", '{"@context": {"s": "https://schema.org/"}, "@type": ["s:BlogPosting"]}'))
    assert _article(_graph({"@context": {"n": "https://schema.org/"}, "@type": "n:Article"}))
    assert _article(_head("website", '{"@type": "https://schema.org/Article"}'))
    assert not _article(_graph({"@type": "o:Article"}, context='{"o": "https://other.test/"}'))
    assert _article(_head("website", '{"@type": "schema:NewsArticle"}'))  # the usual prefix, undeclared


def test_mention_texts_are_bounded_spread_and_leave_out_the_opening():
    from pipeline.press.extract import Span, mention_texts
    long = " ".join(["w"] * 160)
    paragraphs = ["Ordet breve"] + [f"Ordet{i} {long}" for i in range(1, 9)]
    page = Page("Titolo " * 40, None, "it", "u", paragraphs,
                [Span("Ordet", 0, "italic")] + [Span(f"Ordet{i}", i, "italic") for i in range(1, 9)])
    texts = mention_texts(page, 3)
    assert len(texts) == 3 and all(len(t.split()) <= 150 for t in texts)
    assert [next(w for w in t.split() if w.startswith("Ordet")) for t in texts] == ["Ordet1", "Ordet5", "Ordet8"]
    assert mention_texts(Page("T", None, "it", "u", ["breve"], [Span("breve", 0, "italic")]), 3) == []
    assert len(mention_texts(page, 1)) == 1

def _og(og, *nodes):
    import json
    return _head(og, *[json.dumps(n) for n in nodes])


def test_the_pages_main_entity_is_followed():
    page = {"@type": "WebPage", "url": PAGE}
    inline = {"@type": "NewsArticle", "mainEntityOfPage": PAGE}
    for entity in (inline, [inline], {"@type": "NewsArticle"}, {"@type": "s:NewsArticle"}):
        node = {"@context": {"s": "https://schema.org/"}, **page, "mainEntity": entity}
        assert _article(_og("website", node)), entity
    own = {"@type": "Article", "@id": PAGE + "#article"}
    for entity in ({"@id": own["@id"]}, [{"@id": own["@id"]}], own["@id"], [own["@id"]]):
        assert _article(_graph({**page, "mainEntity": entity}, own)), entity


def test_several_main_entities_or_an_item_list_are_a_listing():
    page = {"@type": "WebPage", "url": PAGE}
    ref = {"@id": ART1["@id"]}
    items = {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "item": ref}]}
    for entity in ([ref, {"@id": "https://e.test/a2"}], items, [items]):
        assert not _article(_graph({**page, "mainEntity": entity}, ART1)), entity
        assert not _article(_og("article", {**page, "mainEntity": entity}, ART1)), entity
    # articles embedded in a listing are members too, not the page
    for listing in ({"hasPart": [{"@type": "NewsArticle"}]}, {"mainEntity": [{"@type": "Article"}, {"@type": "Article"}]}):
        assert not _article(_og("article", {**page, **listing})), listing


def test_another_pages_main_entity_is_not_followed():
    other = {"@type": "WebPage", "url": "https://e.test/other", "mainEntity": {"@type": "NewsArticle"}}
    assert not _article(_og("website", other))


def test_inconclusive_jsonld_falls_back_to_og_type_then_to_an_article():
    page = {"@type": "WebPage", "url": PAGE}
    assert _article(_og("article", page)) and _article(_og(None, page))
    assert not _article(_og("website", page))
    elsewhere = {"@type": "CollectionPage", "url": "https://e.test/all"}  # the section it belongs to
    assert _article(_og("article", page, elsewhere)) and _article(_og(None, page, elsewhere))
    assert not _article(_og("article", {**elsewhere, "url": PAGE}))
    assert not _article(_og("article", {"@type": "CollectionPage"}))


def test_breadcrumbs_alone_without_og_type_stay_a_section():
    """Avvenire's section pages (/agora/cultura): breadcrumbs, no other JSON-LD and no og:type."""
    crumbs = {"@type": "BreadcrumbList", "itemListElement": []}
    assert not _article(_og(None, crumbs))
    assert _article(_og("article", crumbs))
