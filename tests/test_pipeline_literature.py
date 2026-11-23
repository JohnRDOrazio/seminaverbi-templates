import pytest

from pipeline.domains import DOMAINS, common, literature
from pipeline.queue import Queue, Row
from test_pipeline_film import (FakeSparql, FakeWikidata, FakeWikidataEntities, FakeWikipedia, entity, item,
                                wiki_for_pages)

CFG = {"sitelink_floor": 15}


def lrow(sources, count, instance_of=("Q7725634",), folded_into=None, qid="Q1", calibration=False):
    data = {"count": count, "instance_of": list(instance_of)}
    if folded_into:
        data["folded_into"] = folded_into
    return Row(qid, "literature", sources, {}, {}, calibration, data={0: data})


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


def test_registry_has_literature():
    assert DOMAINS["literature"] is literature


def test_editions_are_dropped():
    assert literature.triage(lrow(["author"], 40, ["Q3331189"]), CFG) == (
        "drop", "an edition or translation, not a work of its own")


def test_floor_applies_to_author_works_and_facets():
    assert literature.triage(lrow(["author"], 2), CFG) == ("drop", "2 sitelinks, under the floor of 15")
    assert literature.triage(lrow(["facet"], 14), CFG)[0] == "drop"
    assert literature.triage(lrow(["illustrissimi:author"], 40), CFG)[0] == "pass"
    assert literature.triage(lrow(["illustrissimi:author"], 3), CFG)[0] == "drop"


def test_works_a_source_names_directly_skip_the_floor():
    assert literature.triage(lrow(["illustrissimi", "facet"], 1), CFG)[0] == "pass"
    assert literature.triage(lrow(["francis"], 0), CFG)[0] == "pass"


def test_a_folded_volume_is_dropped():
    assert literature.triage(lrow(["author"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")


def test_calibration_rows_always_pass():
    assert literature.triage(lrow(["calibration"], 1, ["Q3331189"], calibration=True), CFG) == (
        "pass", "calibration")


def test_domain_config_overrides_only_shared_keys():
    cfg = {"sitelink_floor": 15, "literature": {"sitelink_floor": 8, "authors": []}}
    merged = common.domain_config(cfg, "literature")
    assert merged["sitelink_floor"] == 8 and "authors" not in merged
    assert merged["literature"]["authors"] == [] and cfg["sitelink_floor"] == 15
    assert common.domain_config({"sitelink_floor": 15}, "film") == {"sitelink_floor": 15}


def test_calibration_skips_people(q, capsys):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            return [item("Q1", types="Q7725634")] if "wd:Q7725634 ." in query else []

    assert literature.add_calibration(q, Sparql(), ["Q1", "Q5"]) == 1
    assert "literature calibration: Q5 skipped (not a work)" in capsys.readouterr().out
    r = q.row("Q1")
    assert r.calibration and r.sources == ["calibration"] and "series" not in r.data[0]


def test_calibration_marks_series(q):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            return [item("Q7", types="Q1667921")] if "wd:Q1667921" in query else []

    assert literature.add_calibration(q, Sparql(), ["Q7"]) == 1
    assert q.row("Q7").data[0]["series"] is True


def test_questions_have_the_three_keys():
    assert set(literature.QUESTIONS) == {"plausible", "connection", "connections"}


CFG_FULL = {
    "sitelink_floor": 15,
    "literature": {
        "authors": [{"qid": "Q_SEED", "name": "Seed"}],
        "francis_2024": [{"qid": "Q_FA", "name": "FA", "kind": "author"},
                         {"qid": "Q_FW", "name": "FW", "kind": "work"}],
        "subjects": ["Q302"], "book_class": "Q29154430", "biblical_classes": ["Q12405827"], "genres": ["Q1084059"],
        "series_classes": ["Q1667921", "Q277759"],
        "illustrissimi_page": "Illustrissimi", "illustrissimi_section": "I personaggi",
        "religious_classes": ["Q179461", "Q29154430"], "bible": "Q1845", "music_class": "Q2188189",
    },
}


class FakeItWikipedia:
    def __init__(self, titles):
        self.titles, self.asked = titles, []

    def section_links(self, page, heading):
        self.asked.append((page, heading))
        return self.titles


def run(q, answers, cfg=CFG_FULL, titles=(), wikidata=None):
    sparql = FakeSparql(answers)
    it = FakeItWikipedia(list(titles))
    n = literature.candidates(q, sparql, FakeWikipedia([]), {**cfg, "_it_wikipedia": it},
                              wikidata or FakeWikidata({}))
    return n, sparql, it


def test_illustrissimi_addressees_lead_to_works(q, capsys):
    wikidata = FakeWikidata({"Charles Dickens": "Q_D", "Pinocchio": "Q_PINOCCHIO", "Guglielmo Marconi": "Q_M"})
    answers = {
        "VALUES ?author { wd:Q_D }": [item("Q_A", "Oliver", 40, "Q7725634")],
        "VALUES ?entry { wd:Q_PINOCCHIO }": [item("Q_P", "Pinocchio book", 90, "Q7725634")],
    }
    n, _, it = run(q, answers, titles=["Charles Dickens", "Pinocchio", "Guglielmo Marconi", "Davide"], wikidata=wikidata)
    assert n == 2
    assert it.asked == [("Illustrissimi", "I personaggi")]
    assert wikidata.calls == [("itwiki", ["Charles Dickens", "Pinocchio", "Guglielmo Marconi", "Davide"])]
    assert q.row("Q_A").sources == ["illustrissimi:author"]
    assert q.row("Q_P").sources == ["illustrissimi"]
    out = capsys.readouterr().out
    assert "illustrissimi: no literary work for 'Guglielmo Marconi'" in out
    assert "illustrissimi: no Wikidata item for 'Davide'" in out
    assert "no literary work for 'Charles Dickens'" not in out and "no literary work for 'Pinocchio'" not in out


def test_seed_author_works_are_tagged_author(q):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            return [item("Q_W", "Work", 30, "Q7725634")] if "VALUES ?author { wd:Q_SEED }" in query \
                and "?item wdt:P31/wdt:P279* wd:Q7725634 ." in query else []

    n = literature.candidates(q, Sparql(), FakeWikipedia([]), {**CFG_FULL, "_it_wikipedia": FakeItWikipedia([])},
                              FakeWikidata({}))
    assert n == 1 and q.row("Q_W").sources == ["author"]
    assert q.row("Q_W").data[0].get("series") is None


def test_francis_entries_by_kind(q):
    answers = {"VALUES ?author { wd:Q_FA }": [item("Q_X", "X", 5)],
               "VALUES ?entry { wd:Q_FW }": [item("Q_FW", "FW", 1)]}
    run(q, answers)
    assert q.row("Q_X").sources == ["francis:author"]
    assert q.row("Q_FW").sources == ["francis"]


def test_series_hits_are_marked(q):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            if "VALUES ?author { wd:Q_SEED }" in query:
                return [item("Q_S", "Saga", 50, "Q1667921")] if "wd:Q1667921 ." in query else []
            return []

    sparql = Sparql()
    literature.candidates(q, sparql, FakeWikipedia([]), {**CFG_FULL, "_it_wikipedia": FakeItWikipedia([])},
                          FakeWikidata({}))
    assert q.row("Q_S").data[0]["series"] is True


def test_facets_run_for_works_and_series(q):
    _, sparql, _ = run(q, {})
    for marker in ("VALUES ?subject { wd:Q302 }", "wd:Q29154430", "wdt:P411", "VALUES ?class { wd:Q12405827 }",
                   "VALUES ?genre { wd:Q1084059 }"):
        for cls in ("Q7725634", "Q1667921", "Q277759"):
            assert any(marker in s and f"?item wdt:P31/wdt:P279* wd:{cls} ." in s for s in sparql.queries), (marker, cls)


def test_every_source_goes_through_add_rows(q, monkeypatch):
    seen = []
    monkeypatch.setattr(literature, "_add_rows", lambda queue, rows, source, series=False, **_: seen.append(source) or 0)
    wikidata = FakeWikidata({"Charles Dickens": "Q_D"})
    run(q, {"VALUES ?author": [item("Q_1")], "VALUES ?entry": [item("Q_2")], "VALUES ?subject": [item("Q_3")]},
        titles=["Charles Dickens"], wikidata=wikidata)
    assert {"author", "illustrissimi:author", "francis:author", "francis", "facet"} <= set(seen)


class FoldSparql:
    """series_of answers (`?volume` queries) from `links` {volume: [series]}; the series details (`VALUES ?item`)
    from `details` {series: row}."""

    def __init__(self, links, details):
        self.links, self.details, self.queries = links, details, []

    def select(self, query):
        self.queries.append(query)
        if "?volume" in query:
            return [{"volume": v, "series": s} for v, ss in self.links.items() for s in ss if f"wd:{v}" in query]
        return [r for qid, r in self.details.items() if f"wd:{qid}" in query]


def vol(qid, label, n):
    return item(qid, label, n, "Q7725634")


FCFG = {"literature": {"series_classes": ["Q1667921", "Q277759"]}}


def folding(q, volumes, links, details, source="author"):
    sparql = FoldSparql(links, details)
    return literature.fold(q, sparql, volumes, source, FCFG), sparql


def test_a_volume_becomes_its_series(q):
    n, sparql = folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, {"Q_S": item("Q_S", "Saga", 12, "Q1667921")})
    assert n == 1 and q.row("Q_V") is None
    s = q.row("Q_S")
    assert s.sources == ["author"]
    assert s.data[0]["series"] is True and s.data[0]["count"] == 30
    assert s.data[0]["volumes"] == {"Q_V": {"sitelinks": {"enwiki": "Volume"}, "count": 30}}
    assert sum("?volume" in x for x in sparql.queries) == 1 and sum("VALUES ?item" in x for x in sparql.queries) == 1


def test_a_later_volume_extends_the_series(q):
    details = {"Q_S": item("Q_S", "Saga", 12, "Q1667921")}
    folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, details)
    n, _ = folding(q, [vol("Q_V2", "Volume two", 50)], {"Q_V2": ["Q_S"]}, details, "facet")
    s = q.row("Q_S")
    assert n == 0 and s.sources == ["author", "facet"]
    assert set(s.data[0]["volumes"]) == {"Q_V", "Q_V2"} and s.data[0]["count"] == 50


def test_a_publisher_series_does_not_fold(q):
    n, _ = folding(q, [vol("Q_V", "Volume", 30)], {}, {})
    assert n == 1 and q.row("Q_V").sources == ["author"] and "volumes" not in q.row("Q_V").data[0]


def test_a_volume_in_two_series_stays_itself_and_is_reported(q, capsys):
    n, _ = folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S", "Q_T"]},
                   {"Q_S": item("Q_S"), "Q_T": item("Q_T")})
    assert n == 1 and q.row("Q_V") is not None and q.row("Q_S") is None
    assert "literature candidates: Q_V in 2 series, kept on its own" in capsys.readouterr().out


def test_a_volume_in_no_series_is_silent(q, capsys):
    folding(q, [vol("Q_V", "Volume", 30)], {}, {})
    assert capsys.readouterr().out == ""


def test_an_existing_volume_row_is_marked_folded(q):
    q.add("Q_V", "literature", "author", {"en": "V"}, {"enwiki": "V"}, data={"count": 30})
    n, _ = folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, {"Q_S": item("Q_S", "Saga", 12, "Q1667921")},
                   "facet")
    v = q.row("Q_V")
    assert v.data[0]["folded_into"] == "Q_S" and v.data[0]["count"] == 30 and v.sources == ["author"]
    assert q.row("Q_S").data[0]["volumes"]["Q_V"]["count"] == 30 and n == 1


@pytest.mark.parametrize("status", ["approved", "created", "declined"])
def test_a_closed_series_absorbs_nothing(q, status):
    details = {"Q_S": item("Q_S", "Saga", 12, "Q1667921")}
    folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, details)
    q.set_status("Q_S", status)
    n, _ = folding(q, [vol("Q_V2", "Volume two", 50)], {"Q_V2": ["Q_S"]}, details)
    assert n == 0 and q.row("Q_V2") is None
    assert set(q.row("Q_S").data[0]["volumes"]) == {"Q_V"} and q.row("Q_S").data[0]["count"] == 30


def test_a_series_without_wikipedia_borrows_its_best_volume(q):
    bare = {"item": "Q_S", "n": "12", "types": "Q1667921"}
    folding(q, [vol("Q_A", "Minor volume", 10), vol("Q_B", "Best volume", 30)],
            {"Q_A": ["Q_S"], "Q_B": ["Q_S"]}, {"Q_S": bare})
    s = q.row("Q_S").data[0]
    assert s["lead_sitelinks"] == {"enwiki": "Best volume"} and s["count"] == 30


def test_a_series_with_its_own_wikipedia_borrows_nothing(q):
    folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, {"Q_S": item("Q_S", "Saga", 12, "Q1667921")})
    assert "lead_sitelinks" not in q.row("Q_S").data[0]


def test_series_of_asks_for_a_shared_author_and_a_series_class():
    sparql = FoldSparql({"Q_V": ["Q_S"]}, {})
    assert literature.series_of(sparql, ["Q_V"], ["Q1667921", "Q277759"]) == {"Q_V": ["Q_S"]}
    query = sparql.queries[0]
    assert "wdt:P179" in query and "wdt:P50" in query and "wd:Q1667921" in query and "wd:Q277759" in query


def test_a_work_query_folds_its_volumes(q):
    class Sparql(FoldSparql):
        def select(self, query):
            if "?kind" in query:  # the exclusion query: nothing religious or musical here
                return []
            if "?volume" in query or "VALUES ?item" in query:
                return super().select(query)
            return [vol("Q_V", "Volume", 30)] if "VALUES ?author { wd:Q_SEED }" in query \
                and "wd:Q7725634 ." in query else []

    sparql = Sparql({"Q_V": ["Q_S"]}, {"Q_S": item("Q_S", "Saga", 12, "Q1667921")})
    literature.candidates(q, sparql, FakeWikipedia([]), {**CFG_FULL, "_it_wikipedia": FakeItWikipedia([])},
                          FakeWikidata({}))
    assert q.row("Q_V") is None and q.row("Q_S").data[0]["volumes"]["Q_V"]["count"] == 30


def test_rows_from_a_series_query_are_not_folded(q):
    sparql = FoldSparql({"Q_S": ["Q_T"]}, {})
    literature._add_rows(q, [item("Q_S", "Saga", 50, "Q1667921")], "author", sparql=sparql,
                         series_classes=["Q1667921"], series=True)
    assert q.row("Q_S").data[0]["series"] is True and sparql.queries == []


def test_a_series_covered_through_a_volume_is_a_translation_suggestion():
    from test_pipeline_stages import FakeRepo, wikis

    volumes = {"Q10": {"sitelinks": {"enwiki": "The Hobbit"}, "count": 90},
               "Q11": {"sitelinks": {"enwiki": "Other"}, "count": 5}}
    series = Row("Q5", "literature", ["author"], {}, {"enwiki": "Middle-earth"}, False,
                 data={0: {"count": 1, "series": True, "volumes": volumes}})
    repo = FakeRepo({("enwiki", "The Hobbit"): "Q9"}, {"Q9": {"en": "The Hobbit", "fr": "Le Hobbit"}})
    assert literature.covered(series, repo, wikis()) == (
        "translation", {"have": ["en", "fr"], "missing": ["it", "es", "de", "pt"],
                        "note": "covered through The Hobbit"})


def test_a_series_with_no_covered_volume_passes():
    from test_pipeline_stages import FakeRepo, wikis

    series = Row("Q5", "literature", ["author"], {}, {}, False,
                 data={0: {"volumes": {"Q10": {"sitelinks": {"enwiki": "The Hobbit"}, "count": 90}}}})
    assert literature.covered(series, FakeRepo(), wikis()) == ("pass", {})


def _series_with_hobbit(calibration):
    return Row("Q5", "literature", ["calibration"], {}, {"enwiki": "Middle-earth"}, calibration,
               data={0: {"volumes": {"Q10": {"sitelinks": {"enwiki": "The Hobbit"}, "count": 90}}}})


def test_a_calibration_series_keeps_its_own_coverage_despite_a_covered_volume():
    from test_pipeline_stages import FakeRepo, wikis

    repo = FakeRepo({("enwiki", "Middle-earth"): "Q5", ("enwiki", "The Hobbit"): "Q9"},
                    {"Q5": {"it": "Terra di Mezzo"}, "Q9": {"en": "The Hobbit", "fr": "Le Hobbit"}})
    assert literature.covered(_series_with_hobbit(True), repo, wikis()) == (
        "pass", {"have": ["it"], "missing": ["en", "es", "fr", "de", "pt"]})


def test_a_series_itself_covered_is_returned_unchanged_despite_a_covered_volume():
    from test_pipeline_stages import FakeRepo, wikis

    repo = FakeRepo({("enwiki", "Middle-earth"): "Q5", ("enwiki", "The Hobbit"): "Q9"},
                    {"Q5": {"it": "Terra di Mezzo"}, "Q9": {"en": "The Hobbit", "fr": "Le Hobbit"}})
    assert literature.covered(_series_with_hobbit(False), repo, wikis()) == (
        "translation", {"have": ["it"], "missing": ["en", "es", "fr", "de", "pt"]})


# ---- the dossier ----

DC = {"dossier_languages": 3}


def drow(sitelinks, series=False, volumes=None, lead_sitelinks=None, calibration=False):
    data = {"count": 30, "instance_of": ["Q7725634"], "series": series}
    if volumes is not None:
        data["volumes"] = volumes
    if lead_sitelinks is not None:
        data["lead_sitelinks"] = lead_sitelinks
    return Row("Q1", "literature", ["author"], {}, sitelinks, calibration, data={0: data})


def test_dossier_starts_with_the_original_language():
    wd = FakeWikidataEntities({"Q1": entity(P407=["Q652"])})
    pages = {("en", "wikipedia"): {"B": [("Plot", "en")]}, ("it", "wikipedia"): {"B it": [("Trama", "it")]}}
    d = literature.dossier(drow({"enwiki": "B", "itwiki": "B it"}), DC, wiki_for_pages(pages), wd)
    assert list(d["languages"]) == ["it", "en"]
    assert d["notes"][0]["lang"] == "it"


def test_a_non_six_original_language_falls_back_to_the_six():
    wd = FakeWikidataEntities({"Q1": entity(P407=["Q7737"])})
    pages = {("de", "wikipedia"): {"B de": [("Handlung", "de")]}, ("pt", "wikipedia"): {"B pt": [("Enredo", "x")]}}
    d = literature.dossier(drow({"dewiki": "B de", "ptwiki": "B pt"}), DC, wiki_for_pages(pages), wd)
    assert list(d["languages"]) == ["de", "pt"]


def test_series_reads_each_volume_page():
    wd = FakeWikidataEntities({"Q1": entity(P527=["V1", "V2"]), "V1": entity(sitelinks={"enwiki": "Vol 1"}),
                               "V2": entity(sitelinks={"enwiki": "Vol 2"})})
    pages = {("en", "wikipedia"): {"S": [("Plot", "s")], "Vol 1": [("Plot", "p1"), ("Cast", "x")],
                                   "Vol 2": [], "#lead:Vol 2": "lead two"}}
    d = literature.dossier(drow({"enwiki": "S"}, series=True), DC, wiki_for_pages(pages), wd)
    assert [n["heading"] for n in d["notes"]] == ["Plot", "Vol 1 / Plot", "Vol 2"]


def test_a_series_without_its_own_page_reads_its_volumes():
    vols = {"V1": {"sitelinks": {"enwiki": "Vol 1"}, "count": 9}, "V2": {"sitelinks": {"enwiki": "Vol 2"}, "count": 3}}
    wd = FakeWikidataEntities({"Q1": entity()})
    pages = {("en", "wikipedia"): {"Vol 1": [("Plot", "p1")], "Vol 2": [("Plot", "p2")],
                                   "#lead:Vol 1": "lead one"}}
    d = literature.dossier(drow({}, series=True, volumes=vols, lead_sitelinks={"enwiki": "Vol 1"}),
                           DC, wiki_for_pages(pages), wd)
    assert [n["heading"] for n in d["notes"]] == ["Lead", "Plot", "Vol 2 / Plot"]  # Vol 1 is the work page: read once
    assert "V1" not in wd.asked


def test_wikiquote_keeps_sections_naming_the_author():
    wd = FakeWikidataEntities({"Q1": entity(P50=["A1"]), "A1": entity(label="Chesterton")})
    pages = {("en", "wikipedia"): {"B": []},
             ("en", "wikiquote"): {"B": [("Chesterton", "quote"), ("Other", "no")]}}
    d = literature.dossier(drow({"enwiki": "B"}), DC, wiki_for_pages(pages), wd)
    assert [(n["heading"], n["url"]) for n in d["notes"]] == [("Chesterton", "https://en.wikiquote.org/wiki/B")]


def test_wikisource_is_a_link_not_a_note():
    wd = FakeWikidataEntities({"Q1": entity(sitelinks={"enwikisource": "The Man Who Was Thursday",
                                                       "frwikisource": "X"})})
    pages = {("en", "wikipedia"): {"The Man": [("Plot", "p")]}}
    d = literature.dossier(drow({"enwiki": "The Man"}), DC, wiki_for_pages(pages), wd)
    assert d["links"] == ["https://en.wikisource.org/wiki/The_Man_Who_Was_Thursday"]
    assert all("wikisource" not in n["url"] for n in d["notes"])


def test_literature_dossier_stage_drops_empty():
    wd = FakeWikidataEntities({"Q1": entity()})
    assert literature.dossier_stage(drow({"enwiki": "B"}), DC, wiki_for_pages({}), wd) == (
        "drop", {"reason": "empty dossier"})
    assert literature.dossier_stage(drow({"enwiki": "B"}, calibration=True), DC, wiki_for_pages({}), wd)[0] == "pass"


# ---- fixes from the final review ----

def test_a_series_keeps_its_folded_count_when_a_series_query_returns_it_again(q):
    bare = {"item": "Q_S", "n": "3", "types": "Q1667921"}
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, {"Q_S": bare})
    literature._add_rows(q, [bare], "author", series=True)  # the series-class query returns the same series
    s = q.row("Q_S")
    assert s.data[0]["count"] == 40 and set(s.data[0]["volumes"]) == {"Q_V"} and "lead_sitelinks" in s.data[0]
    assert literature.triage(s, CFG)[0] == "pass"


def test_a_human_addressee_with_p1441_and_no_works_is_reported_not_added(q, capsys):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            # a Bible book "present in work" of the human: returned unless the query excludes humans
            if "wdt:P1441" in query and "wd:Q5" not in query:
                return [item("Q_BIBLE", "Genesis", 90, "Q7725634")]
            return []

    wikidata = FakeWikidata({"Gesu": "Q302"})
    literature.candidates(q, Sparql(), FakeWikipedia([]), {**CFG_FULL, "_it_wikipedia": FakeItWikipedia(["Gesu"])},
                          wikidata)
    assert q.row("Q_BIBLE") is None
    assert "illustrissimi: no literary work for 'Gesu'" in capsys.readouterr().out


def _existing_volume(q, status=None):
    q.add("Q_V", "literature", "author", {"en": "V"}, {"enwiki": "V"}, data={"count": 30})
    q.set_outcome("Q_V", 1, "pass")
    q.set_outcome("Q_V", 2, "pass")
    if status:
        q.set_status("Q_V", status)
    return folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, {"Q_S": item("Q_S", "Saga", 12, "Q1667921")})


def test_folding_a_volume_with_no_status_reruns_its_triage(q):
    _existing_volume(q)
    assert 2 not in q.row("Q_V").outcomes and 1 in q.row("Q_V").outcomes
    assert literature.triage(q.row("Q_V"), CFG)[0] == "drop"


@pytest.mark.parametrize("status", ["open", "approved", "created", "declined"])
def test_folding_a_volume_with_a_status_leaves_it_alone(q, capsys, status):
    _existing_volume(q, status)
    assert 2 in q.row("Q_V").outcomes
    assert f"literature candidates: Q_V folded into Q_S but already {status}, left alone" in capsys.readouterr().out


def test_lead_sitelinks_prefer_a_volume_with_a_wikipedia_page(q):
    wikisource_only = {"item": "Q_A", "n": "99", "types": "Q7725634"}
    folding(q, [wikisource_only, vol("Q_B", "Has page", 5)], {"Q_A": ["Q_S"], "Q_B": ["Q_S"]},
            {"Q_S": {"item": "Q_S", "n": "1", "types": "Q1667921"}})
    assert q.row("Q_S").data[0]["lead_sitelinks"] == {"enwiki": "Has page"}


def test_wikisource_link_quotes_the_title():
    wd = FakeWikidataEntities({"Q1": entity(sitelinks={"enwikisource": "Who Was It?"})})
    d = literature.dossier(drow({"enwiki": "B"}), DC, wiki_for_pages({("en", "wikipedia"): {"B": [("Plot", "p")]}}), wd)
    assert d["links"] == ["https://en.wikisource.org/wiki/Who_Was_It%3F"]


def test_a_qid_already_in_another_domain_is_skipped(q, capsys):
    q.add("Q_F", "film", "facet", {}, {}, data={"count": 50})
    assert literature._insert(q, item("Q_F", "Film", 90, "Q7725634"), "author") == 0
    assert q.row("Q_F").domain == "film" and q.row("Q_F").sources == ["facet"] and q.row("Q_F").data[0]["count"] == 50
    assert "literature candidates: Q_F is already a film candidate, skipped" in capsys.readouterr().out


def test_a_series_qid_in_another_domain_is_not_extended(q, capsys):
    q.add("Q_S", "film", "facet", {}, {}, data={"count": 50})
    n, _ = folding(q, [vol("Q_V", "Volume", 30)], {"Q_V": ["Q_S"]}, {"Q_S": item("Q_S", "Saga", 12, "Q1667921")})
    assert n == 0 and "volumes" not in q.row("Q_S").data[0] and q.row("Q_S").sources == ["facet"]
    assert "literature candidates: Q_S is already a film candidate, skipped" in capsys.readouterr().out


def test_a_series_keeps_its_count_when_a_work_query_returns_it_later(q):
    bare = {"item": "Q_S", "n": "3", "types": "Q1667921"}
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, {"Q_S": bare})
    literature._insert(q, bare, "author")  # fold() inserts a row in no series this way
    s = q.row("Q_S")
    assert s.data[0]["count"] == 40 and set(s.data[0]["volumes"]) == {"Q_V"}
    assert literature.triage(s, CFG)[0] == "pass"


def test_a_fictional_character_still_reaches_its_work_through_p1441(q):
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            return [item("Q_P", "Pinocchio book", 90, "Q7725634")] if "wdt:P1441" in query else []

    sparql = Sparql()
    literature.candidates(q, sparql, FakeWikipedia([]),
                          {**CFG_FULL, "_it_wikipedia": FakeItWikipedia(["Pinocchio"])},
                          FakeWikidata({"Pinocchio": "Q_PINOCCHIO"}))
    assert q.row("Q_P").sources[0] == "illustrissimi"
    assert any("wdt:P1441 ?item FILTER NOT EXISTS { ?entry wdt:P31 wd:Q5 }" in s for s in sparql.queries)


# ---- PR review ----

def test_each_direct_branch_binds_its_own_entries():
    pattern = literature._direct_pattern(["Q_A", "Q_B"])
    first, second = pattern.split("UNION")
    assert "VALUES ?entry { wd:Q_A wd:Q_B }" in first and "wdt:P1441 ?item" in first
    assert "VALUES ?item { wd:Q_A wd:Q_B }" in second and "BIND" not in pattern


def _failed_series(q, count=10):
    bare = {"item": "Q_S", "n": str(count), "types": "Q1667921"}
    literature._insert(q, bare, "author", series=True)
    for stage, outcome in ((1, "pass"), (2, "drop")):
        q.set_outcome("Q_S", stage, outcome)
    return {"Q_S": item("Q_S", "Saga", count, "Q1667921")}


def test_a_new_volume_clears_the_series_outcomes_from_stage_one(q):
    details = _failed_series(q)
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, details)
    s = q.row("Q_S")
    assert s.outcomes == {0: "pass"} and s.data[0]["count"] == 40
    assert literature.triage(s, CFG)[0] == "pass"


def test_rediscovering_the_same_volume_keeps_the_outcomes(q):
    details = _failed_series(q)
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, details)
    q.set_outcome("Q_S", 1, "pass")
    q.set_outcome("Q_S", 2, "pass")
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, details, "facet")
    assert q.row("Q_S").outcomes == {0: "pass", 1: "pass", 2: "pass"}


def test_a_new_volume_keeps_the_outcomes_of_a_series_with_a_status(q):
    details = _failed_series(q)
    q.set_status("Q_S", "open")
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, details)
    assert q.row("Q_S").outcomes == {0: "pass", 1: "pass", 2: "drop"}


def test_a_series_found_directly_gets_its_volumes_and_is_a_translation_suggestion(q):
    from test_pipeline_stages import FakeRepo, wikis

    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            if "VALUES ?series" in query:
                assert "wdt:P527" in query and "wdt:P179" in query
                return [{**vol("Q_V", "The Hobbit", 90), "series": "Q_S"}]
            if "VALUES ?subject { wd:Q302 }" in query and "wd:Q1667921 ." in query:
                return [item("Q_S", "Middle-earth", 12, "Q1667921")]
            return []

    literature.candidates(q, Sparql(), FakeWikipedia([]), {**CFG_FULL, "_it_wikipedia": FakeItWikipedia([])},
                          FakeWikidata({}))
    s = q.row("Q_S")
    assert s.data[0]["volumes"] == {"Q_V": {"sitelinks": {"enwiki": "The Hobbit"}, "count": 90}}
    assert s.data[0]["count"] == 90 and q.row("Q_V") is None
    repo = FakeRepo({("enwiki", "The Hobbit"): "Q9"}, {"Q9": {"en": "The Hobbit", "fr": "Le Hobbit"}})
    assert literature.covered(s, repo, wikis())[0] == "translation"


# ---- PR re-review ----

def test_a_known_volume_with_a_higher_count_clears_the_series_outcomes(q):
    details = _failed_series(q)
    folding(q, [vol("Q_V", "Volume", 10)], {"Q_V": ["Q_S"]}, details)
    q.set_outcome("Q_S", 1, "pass")
    q.set_outcome("Q_S", 2, "drop")
    folding(q, [vol("Q_V", "Volume", 40)], {"Q_V": ["Q_S"]}, details, "facet")
    s = q.row("Q_S")
    assert s.outcomes == {0: "pass"} and s.data[0]["count"] == 40


def _direct_series(q, members, series_of=()):
    """A directly found series Q_S and the volumes query answer `members` (rows with series and shared);
    `series_of`: the series-of-volumes query answer (rows with volume and series)."""
    class Sparql(FakeSparql):
        def select(self, query):
            self.queries.append(query)
            if "VALUES ?series" in query:
                return members
            return list(series_of) if "VALUES ?volume" in query else []

    literature._insert(q, {"item": "Q_S", "n": "12", "types": "Q1667921"}, "facet", series=True)
    sparql = Sparql()
    literature.attach_volumes(q, sparql, ["Q_S"], ["Q1667921"])
    return sparql


def test_a_member_with_another_author_is_recorded_but_not_folded(q):
    q.add("Q_V", "literature", "author", {"en": "V"}, {"enwiki": "V"}, data={"count": 30})
    q.set_outcome("Q_V", 2, "pass")
    sparql = _direct_series(q, [{**vol("Q_V", "Volume", 30), "series": "Q_S", "shared": "false"}])
    assert "wdt:P50" in sparql.queries[0]
    assert set(q.row("Q_S").data[0]["volumes"]) == {"Q_V"}
    v = q.row("Q_V")
    assert "folded_into" not in v.data[0] and 2 in v.outcomes


def test_a_member_sharing_an_author_is_folded(q):
    q.add("Q_V", "literature", "author", {"en": "V"}, {"enwiki": "V"}, data={"count": 30})
    _direct_series(q, [{**vol("Q_V", "Volume", 30), "series": "Q_S", "shared": "true"}])
    assert q.row("Q_V").data[0]["folded_into"] == "Q_S"


def test_a_shared_author_member_in_two_series_is_recorded_but_not_folded(q):
    q.add("Q_V", "literature", "author", {"en": "V"}, {"enwiki": "V"}, data={"count": 30})
    _direct_series(q, [{**vol("Q_V", "Volume", 30), "series": "Q_S", "shared": "true"}],
                   [{"volume": "Q_V", "series": "Q_S"}, {"volume": "Q_V", "series": "Q_S2"}])
    assert set(q.row("Q_S").data[0]["volumes"]) == {"Q_V"}
    assert "folded_into" not in q.row("Q_V").data[0]


# ---- religious texts and musical works ----

EXCL = {"sitelink_floor": 15, "literature": {"religious_classes": ["Q179461", "Q29154430"], "bible": "Q1845",
                                             "music_class": "Q2188189"}}


def test_an_excluded_work_is_dropped():
    row = lrow(["facet"], 300)
    row.data[0]["excluded"] = "a religious text"
    assert literature.triage(row, CFG) == ("drop", "a religious text, not a literary work")


def _excluding(q, kinds):
    """Run `exclude` with the class query answering `kinds` ({qid: [kind, ...]})."""
    sparql = FakeSparql({"wd:Q2188189": [{"item": qid, "kind": k} for qid, ks in kinds.items() for k in ks]})
    literature.exclude(q, sparql, EXCL)
    return sparql


def test_religious_texts_are_marked_and_musical_works_move_to_music(q, capsys):
    for qid in ("Q_BIBLE", "Q_MUSICAL", "Q_NOVEL"):
        q.add(qid, "literature", "facet", {"en": qid}, {}, data={"count": 40})
    sparql = _excluding(q, {"Q_BIBLE": ["religious"], "Q_MUSICAL": ["music"]})
    assert "wdt:P361+ wd:Q1845" in sparql.queries[0] and "wd:Q179461" in sparql.queries[0]
    assert q.row("Q_BIBLE").data[0]["excluded"] == "a religious text"
    assert q.row("Q_MUSICAL").domain == "music"
    assert "excluded" not in q.row("Q_NOVEL").data[0] and q.row("Q_NOVEL").domain == "literature"
    assert "Q_MUSICAL" in capsys.readouterr().out


def test_a_religious_musical_work_is_excluded_not_moved(q):
    q.add("Q_PSALM", "literature", "illustrissimi:author", {"en": "Psalm 3"}, {}, data={"count": 25})
    _excluding(q, {"Q_PSALM": ["music", "religious"]})
    r = q.row("Q_PSALM")
    assert r.domain == "literature" and r.data[0]["excluded"] == "a religious text"


def test_only_rows_that_can_pass_triage_are_checked(q):
    q.add("Q_LOW", "literature", "facet", {}, {}, data={"count": 3})
    q.add("Q_NAMED", "literature", "illustrissimi", {}, {}, data={"count": 1})
    q.add("Q_CAL", "literature", "calibration", {}, {}, calibration=True, data={"count": 90})
    q.add("Q_FILM", "film", "facet", {}, {}, data={"count": 90})
    sparql = _excluding(q, {})
    assert len(sparql.queries) == 1
    assert "wd:Q_NAMED" in sparql.queries[0]
    assert not any(f"wd:{x} " in sparql.queries[0] for x in ("Q_LOW", "Q_CAL", "Q_FILM"))


def test_an_excluded_row_already_triaged_is_triaged_again(q):
    q.add("Q_BIBLE", "literature", "facet", {}, {}, data={"count": 40})
    q.set_outcome("Q_BIBLE", 1, "pass")
    q.set_outcome("Q_BIBLE", 2, "pass")
    _excluding(q, {"Q_BIBLE": ["religious"]})
    assert 2 not in q.row("Q_BIBLE").outcomes


def test_candidates_run_the_exclusion(q, monkeypatch):
    seen = []
    monkeypatch.setattr(literature, "exclude", lambda queue, sparql, cfg: seen.append(queue))
    literature.candidates(q, FakeSparql(), FakeWikipedia([]), {**CFG_FULL, "_it_wikipedia": FakeItWikipedia([])},
                          FakeWikidata({}))
    assert seen == [q]


def test_a_press_exempt_row_below_the_floor_is_checked(q):
    q.add("Q_PRESS", "literature", "press:sir", {}, {}, data={"count": 2, "press": {"sir": 0.8}})
    q.add("Q_WEAK", "literature", "press:sir", {}, {}, data={"count": 2, "press": {"sir": 0.4}})
    sparql = _excluding(q, {"Q_PRESS": ["religious"]})
    assert "wd:Q_PRESS" in sparql.queries[0] and "wd:Q_WEAK " not in sparql.queries[0]
    assert q.row("Q_PRESS").data[0]["excluded"] == "a religious text"
    assert literature.triage(q.row("Q_PRESS"), CFG)[0] == "drop"
