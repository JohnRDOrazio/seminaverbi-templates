import pytest

from pipeline.domains import DOMAINS, common, literature
from pipeline.queue import Queue, Row
from test_pipeline_film import FakeSparql, FakeWikidata, FakeWikipedia, item

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


def test_unfinished_stages_raise():
    with pytest.raises(NotImplementedError):
        literature.dossier(None, {}, None, None)


CFG_FULL = {
    "sitelink_floor": 15,
    "literature": {
        "authors": [{"qid": "Q_SEED", "name": "Seed"}],
        "francis_2024": [{"qid": "Q_FA", "name": "FA", "kind": "author"},
                         {"qid": "Q_FW", "name": "FW", "kind": "work"}],
        "subjects": ["Q302"], "book_class": "Q29154430", "biblical_classes": ["Q12405827"], "genres": ["Q1084059"],
        "series_classes": ["Q1667921", "Q277759"],
        "illustrissimi_page": "Illustrissimi", "illustrissimi_section": "I personaggi",
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
    monkeypatch.setattr(literature, "_add_rows", lambda queue, rows, source, series=False: seen.append(source) or 0)
    wikidata = FakeWikidata({"Charles Dickens": "Q_D"})
    run(q, {"VALUES ?author": [item("Q_1")], "VALUES ?entry": [item("Q_2")], "VALUES ?subject": [item("Q_3")]},
        titles=["Charles Dickens"], wikidata=wikidata)
    assert {"author", "illustrissimi:author", "francis:author", "francis", "facet"} <= set(seen)
