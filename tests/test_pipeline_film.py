import pytest

from pipeline.domains import DOMAINS, film
from pipeline.domains.film import calibration_qids, candidates, triage
from pipeline.queue import Queue, Row
from sync.wiki import SyncError

CFG = {
    "sitelink_floor": 15,
    "film": {
        "seeds": {"vatican_page": "Vatican's list of films", "awards": ["Q2503453"]},
        "subjects": ["Q302"],
        "book_class": "Q29154430",
        "biblical_classes": ["Q12405827"],
        "genres": ["Q9259727"],
    },
}


def item(qid, label="X", n=20, types="Q11424"):
    return {"item": qid, "l_en": label, "s_en": f"https://en.wikipedia.org/wiki/{label}", "n": str(n),
            "types": types}


class FakeSparql:
    """Answers the first query that contains one of the markers; every other query gets no rows."""

    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.queries = answers or {}, error, []

    def select(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        for marker, rows in self.answers.items():
            if marker in query:
                return rows
        return []


class FakeWikipedia:
    def __init__(self, links):
        self._links, self.asked = links, []

    def links(self, title):
        self.asked.append(title)
        return self._links


class FakeWikidata:
    def __init__(self, links):
        self.links, self.calls = links, []

    def by_sitelink(self, site, titles):
        self.calls.append((site, list(titles)))
        return {t: self.links[t] for t in titles if t in self.links}


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


def test_domains_registry():
    assert DOMAINS == {"film": film}


def test_vatican_links_resolve_to_qids_and_unresolved_are_reported(q, capsys):
    wikipedia = FakeWikipedia(["Ben-Hur", "Nosuch", "Not a film"])
    wikidata = FakeWikidata({"Ben-Hur": "Q1", "Not a film": "Q9"})
    sparql = FakeSparql({"VALUES ?item": [item("Q1", "Ben-Hur", n=2)]})  # Q9 is no film: the query drops it
    added = candidates(q, sparql, wikipedia, CFG, wikidata=wikidata)
    assert added == 1
    assert wikipedia.asked == ["Vatican's list of films"]
    assert wikidata.calls == [("enwiki", ["Ben-Hur", "Nosuch", "Not a film"])]
    rows = q.pending(1)
    assert [(r.qid, r.sources) for r in rows] == [("Q1", ["vatican"])]
    assert rows[0].labels == {"en": "Ben-Hur"}
    assert rows[0].sitelinks == {"enwiki": "Ben-Hur"}
    assert rows[0].data[0] == {"count": 2, "instance_of": ["Q11424"]}
    out = capsys.readouterr().out
    assert "Nosuch" in out and "Ben-Hur" not in out
    vatican_query = next(s for s in sparql.queries if "VALUES ?item" in s)
    assert "wd:Q1" in vatican_query and "wd:Q9" in vatican_query and "wd:Q11424" in vatican_query


def test_award_and_facet_sources_merge_on_one_row(q):
    sparql = FakeSparql({"wd:Q2503453": [item("Q1", "Ordet", n=30)],
                         "wdt:P921": [item("Q1", "Ordet", n=30), item("Q3", "Other", n=18)]})
    added = candidates(q, sparql, FakeWikipedia([]), CFG, wikidata=FakeWikidata({}))
    assert added == 2
    by_qid = {r.qid: r.sources for r in q.pending(1)}
    assert by_qid == {"Q1": ["award:Q2503453", "facet"], "Q3": ["facet"]}


def test_facet_queries_are_bounded_and_split_film_from_series(q):
    sparql = FakeSparql()
    candidates(q, sparql, FakeWikipedia([]), CFG, wikidata=FakeWikidata({}))
    facet = [s for s in sparql.queries if "wdt:P921" in s]
    assert facet and all("LIMIT" in s for s in sparql.queries)
    for s in facet:  # one class per query: film or series, never both
        assert ("wd:Q11424" in s) != ("wd:Q5398426" in s)
    assert any("wd:Q5398426" in s for s in facet) and any("wd:Q11424" in s for s in facet)
    assert any("wdt:P411" in s for s in facet) and any("wd:Q29154430" in s for s in facet)
    assert any("wdt:P136" in s and "wd:Q9259727" in s for s in sparql.queries)
    assert any("wd:Q12405827" in s for s in facet)


def test_a_failing_query_is_logged_and_propagates(q, capsys):
    with pytest.raises(SyncError):
        candidates(q, FakeSparql(error=SyncError("boom")), FakeWikipedia([]), CFG, wikidata=FakeWikidata({}))
    assert "award" in capsys.readouterr().out


def row(sources, n, instance_of=("Q11424",), qid="Q1", calibration=False):
    return Row(qid, "film", sources, {}, {}, calibration,
               data={0: {"count": n, "instance_of": list(instance_of)}})


def test_triage_floor_applies_to_facet_rows_only():
    assert triage(row(["facet"], 14), CFG)[0] == "drop"
    assert triage(row(["facet"], 15), CFG)[0] == "pass"
    assert triage(row(["vatican"], 2), CFG)[0] == "pass"
    assert triage(row(["facet", "award:Q2503453"], 2), CFG)[0] == "pass"


def test_triage_drops_episodes():
    outcome, reason = triage(row(["vatican"], 40, instance_of=["Q21191270"]), CFG)
    assert outcome == "drop" and "episode" in reason
    assert triage(row(["facet"], 40, instance_of=["Q128696755"]), CFG)[0] == "drop"
    assert triage(row(["facet"], 40, instance_of=["Q622550"]), CFG)[0] == "drop"


def test_site_ids_and_decoded_titles(q):
    r = item("Q1", "X")
    r["s_it"] = "https://it.wikipedia.org/wiki/Mission_(film_1986)"
    r["s_en"] = "https://en.wikipedia.org/wiki/Am%C3%A9lie_(film)"
    sparql = FakeSparql({"wdt:P921": [r]})
    candidates(q, sparql, FakeWikipedia([]), CFG, wikidata=FakeWikidata({}))
    assert q.pending(1)[0].sitelinks == {"enwiki": "Amélie (film)", "itwiki": "Mission (film 1986)"}


def test_a_full_result_warns_of_truncation(q, capsys, monkeypatch):
    monkeypatch.setattr(film, "LIMIT", 1)
    candidates(q, FakeSparql({"wd:Q2503453": [item("Q1")]}), FakeWikipedia([]), CFG, wikidata=FakeWikidata({}))
    out = capsys.readouterr().out
    assert "award Q2503453" in out and "truncated" in out


def test_calibration_rows_always_pass_triage():
    assert triage(row(["calibration"], 1, instance_of=["Q21191270"], calibration=True), CFG) == ("pass", "calibration")


def test_calibration_rows_are_flagged(q):
    sparql = FakeSparql({"VALUES ?item": [item("Q1", "Ordet", n=3)]})
    added = film.add_calibration(q, sparql, ["Q1"])
    assert added == 1
    r = q.pending(1)[0]
    assert r.calibration is True and r.sources == ["calibration"]
    assert triage(r, CFG)[0] == "pass"  # no floor for a calibration row


class FakeWiki:
    def __init__(self, members, items, links):
        self._members, self._items, self._links = members, items, links
        self.asked = []

    def members(self, category):
        self.asked.append(category)
        return self._members.get(category, [])

    def item_for(self, site, title):
        return self._items.get(title)

    def item_sitelinks(self, item):
        return 1, self._links.get(item, {})


def test_calibration_qids_map_through_the_local_item_and_report_unmapped():
    wikis = {
        "en": FakeWiki({"Category:Feature films": [("Ordet", 0), ("Category:Sub", 14), ("Lonely", 0), ("NoLink", 0)],
                        "Category:Television": [("Show", 0)]},
                       {"Ordet": "Q10", "Show": "Q11", "NoLink": "Q12"},
                       {"Q10": {"en": "Ordet", "enwiki": "Ordet (film)"}, "Q11": {"enwiki": "Show"}, "Q12": {"en": "NoLink"}}),
        "it": FakeWiki({"Category:Film drammatici": [("Ordet", 0)]}, {"Ordet": "Q10"},
                       {"Q10": {"enwiki": "Ordet (film)"}}),
    }
    wikidata = FakeWikidata({"Ordet (film)": "Q500", "Show": "Q501"})
    tree = {"root": {"names": {}}, "fields": [
        {"key": "film", "names": {"en": "Film", "it": "Cinema"}, "children": [
            {"key": "feature-films", "names": {"en": "Feature films", "it": "Film drammatici"}}]},
        {"key": "television", "names": {"en": "Television", "it": "Televisione"}}], "themes_root": {"names": {}}, "themes": []}
    qids, unmapped = calibration_qids(wikis, wikidata=wikidata, tree=tree)
    assert qids == ["Q500", "Q501"]
    assert unmapped == ["en:Lonely", "en:NoLink"]
    assert "Category:Film" in wikis["en"].asked and "Category:Cinema" in wikis["it"].asked


# ---- stage 4: the dossier ----

def claim(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def entity(label=None, **claims):
    return {"labels": {"en": {"value": label}} if label else {}, "claims": {p: [claim(q) for q in qs] for p, qs in claims.items()}}


class FakeWikidataEntities:
    def __init__(self, entities):
        self.entities, self.asked = entities, []

    def entity(self, qid):
        self.asked.append(qid)
        return self.entities.get(qid, {})


class FakeSections:
    def __init__(self, lang, project, pages):
        self.lang, self.project, self.pages = lang, project, pages

    def sections(self, title):
        if title not in self.pages:
            raise SyncError("missingtitle")
        return self.pages[title]

    def url(self, title):
        return f"https://{self.lang}.{self.project}.org/wiki/{title.replace(' ', '_')}"


def wiki_for_pages(pages):
    """pages: {(lang, project): {title: [(heading, text)]}}"""
    return lambda lang, project="wikipedia": FakeSections(lang, project, pages.get((lang, project), {}))


def dossier_row(sitelinks, instance_of=("Q11424",), calibration=False):
    return Row("Q1", "film", ["facet"], {}, sitelinks, calibration,
               data={0: {"count": 30, "instance_of": list(instance_of)}})


DCFG = {"dossier_languages": 3}
EN_PAGE = [("Plot", "A man dies."), ("Production", "Shot in Spain."), ("Critical reception", "Praised."),
           ("<i>Themes</i> and analysis", "Grace.")]


def test_dossier_keeps_plot_and_reception_sections_only():
    wikidata = FakeWikidataEntities({"Q1": entity()})
    d = film.dossier(dossier_row({"enwiki": "Ben-Hur"}), DCFG, wiki_for_pages({("en", "wikipedia"): {"Ben-Hur": EN_PAGE}}),
                     wikidata)
    assert [n["heading"] for n in d["notes"]] == ["Plot", "Critical reception", "Themes and analysis"]
    assert d["languages"] == {"en": 3}


def test_dossier_languages_start_with_original_language():
    wikidata = FakeWikidataEntities({"Q1": entity(P364=["Q652"])})
    pages = {("en", "wikipedia"): {"B": [("Plot", "en plot")]}, ("it", "wikipedia"): {"B it": [("Trama", "it trama")]},
             ("fr", "wikipedia"): {"B fr": [("Intrigue", "fr")]}}
    row_ = dossier_row({"enwiki": "B", "itwiki": "B it", "frwiki": "B fr"})
    d = film.dossier(row_, {"dossier_languages": 2}, wiki_for_pages(pages), wikidata)
    assert list(d["languages"]) == ["it", "en"]
    d = film.dossier(dossier_row({"enwiki": "B", "frwiki": "B fr"}), DCFG, wiki_for_pages(pages),
                     FakeWikidataEntities({"Q1": entity(P364=["Q150"])}))
    assert list(d["languages"]) == ["fr", "en"]


def test_a_language_without_a_sitelink_is_never_used():
    d = film.dossier(dossier_row({"enwiki": "B"}), DCFG, wiki_for_pages({("en", "wikipedia"): {"B": EN_PAGE}}),
                     FakeWikidataEntities({"Q1": entity(P364=["Q652"])}))
    assert list(d["languages"]) == ["en"]


def test_series_gets_one_note_per_season():
    page = [("Premise", "x"), ("Season 1", "S1 text"), ("Season 2", "S2 text"), ("Plot", "p")]
    wiki_for = wiki_for_pages({("en", "wikipedia"): {"S": page}})
    d = film.dossier(dossier_row({"enwiki": "S"}, instance_of=("Q5398426",)), DCFG, wiki_for,
                     FakeWikidataEntities({"Q1": entity()}))
    assert [n["heading"] for n in d["notes"]] == ["Season 1", "Season 2", "Plot"]
    d = film.dossier(dossier_row({"enwiki": "S"}), DCFG, wiki_for, FakeWikidataEntities({"Q1": entity()}))
    assert [n["heading"] for n in d["notes"]] == ["Plot"]


def test_every_note_keeps_its_url():
    d = film.dossier(dossier_row({"enwiki": "Ben-Hur"}), DCFG, wiki_for_pages({("en", "wikipedia"): {"Ben-Hur": EN_PAGE}}),
                     FakeWikidataEntities({"Q1": entity()}))
    assert {n["url"] for n in d["notes"]} == {"https://en.wikipedia.org/wiki/Ben-Hur"}
    assert all(n["lang"] == "en" and n["text"] for n in d["notes"])


def test_wikiquote_keeps_only_sections_naming_a_creator():
    wikidata = FakeWikidataEntities({"Q1": entity(P57=["Q9"]), "Q9": entity("William Wyler")})
    quotes = [("William Wyler", "Interview quote."), ("Cast", "Someone: hi."), ("Dialogue", "d")]
    pages = {("en", "wikipedia"): {"B": [("Plot", "p")]}, ("en", "wikiquote"): {"B": quotes}}
    d = film.dossier(dossier_row({"enwiki": "B"}), DCFG, wiki_for_pages(pages), wikidata)
    quote_notes = [n for n in d["notes"] if "wikiquote" in n["url"]]
    assert [n["heading"] for n in quote_notes] == ["William Wyler"]


def test_wikiquote_is_skipped_without_creators_or_page():
    pages = {("en", "wikipedia"): {"B": [("Plot", "p")]}, ("en", "wikiquote"): {"B": [("Dialogue", "d")]}}
    d = film.dossier(dossier_row({"enwiki": "B"}), DCFG, wiki_for_pages(pages), FakeWikidataEntities({"Q1": entity()}))
    assert [n["heading"] for n in d["notes"]] == ["Plot"]
    d = film.dossier(dossier_row({"enwiki": "B"}), DCFG, wiki_for_pages({("en", "wikipedia"): {"B": [("Plot", "p")]}}),
                     FakeWikidataEntities({"Q1": entity(P57=["Q9"]), "Q9": entity("W")}))
    assert len(d["notes"]) == 1


def test_dossier_stage_drops_an_empty_dossier_but_not_a_calibration_row():
    wiki_for = wiki_for_pages({("en", "wikipedia"): {"B": [("Production", "x")]}})
    wd = FakeWikidataEntities({"Q1": entity()})
    assert film.dossier_stage(dossier_row({"enwiki": "B"}), DCFG, wiki_for, wd) == ("drop", {"reason": "empty dossier"})
    outcome, data = film.dossier_stage(dossier_row({"enwiki": "B"}, calibration=True), DCFG, wiki_for, wd)
    assert outcome == "pass" and data["notes"] == []
    wiki_for = wiki_for_pages({("en", "wikipedia"): {"B": EN_PAGE}})
    assert film.dossier_stage(dossier_row({"enwiki": "B"}), DCFG, wiki_for, wd)[0] == "pass"
