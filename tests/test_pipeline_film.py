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
