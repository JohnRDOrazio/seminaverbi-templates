import pytest

from pipeline import stages
from pipeline.domains import common, film, literature
from pipeline.press.route import release_waiting, route
from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue, Row

URL = "https://example.org/a"


class FakeWikidata:
    def __init__(self, entities):
        self.entities, self.asked = entities, []

    def entity(self, qid):
        self.asked.append(qid)
        return self.entities[qid]


def ent(label="Ordet", n=3):
    langs = ["en", "it", "es", "fr", "de", "pt"][:n]
    return {"labels": {"en": {"value": label}, "it": {"value": label + " it"}},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q11424"}}}}]},
            "sitelinks": {f"{l}wiki": {"title": f"{label} {l}"} for l in langs}
            | {"commonswiki": {"title": "Category:x"}}}


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


@pytest.fixture
def store(q):
    s = PressStore.of(q)
    s.add_article(URL, "sir", "it", "Titolo", "2026-11-20")
    s.add_article(URL + "2", "avvenire", "it", "Altro", "2026-11-21")
    return s


@pytest.fixture
def uncovered(monkeypatch):
    calls = []
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: (calls.append(row) or ("pass", {})))
    return calls


def mention(qid="Q9", domain="film", p=0.8, url=URL):
    return Mention(url, qid, "Ordet", "work", domain, p, ["un passo"])


def test_covered_item_goes_to_enrichment_not_the_queue(q, store, monkeypatch):
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("drop", {"have": ["en"]}))
    store.add_mention(mention())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"enrichment": 1}
    assert q.row("Q9") is None
    assert [m.qid for m in store.mentions(route="enrichment")] == ["Q9"]


def test_translation_also_goes_to_enrichment(q, store, monkeypatch):
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("translation", {"have": ["en"]}))
    store.add_mention(mention())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"enrichment": 1}


def test_new_film_becomes_a_candidate_with_a_press_source(q, store, uncovered):
    store.add_mention(mention())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"candidate": 1}
    r = q.row("Q9")
    assert r.domain == "film" and r.sources == ["press:sir"]
    assert r.data[0]["press"] == {"sir": 0.8}
    assert r.labels["en"] == "Ordet" and r.sitelinks["enwiki"] == "Ordet en"
    assert "commonswiki" not in r.sitelinks
    assert uncovered[0].qid == "Q9"
    assert store.mentions()[0].route == "candidate"


def test_routing_twice_adds_nothing_more(q, store, uncovered):
    store.add_mention(mention())
    wd = FakeWikidata({"Q9": ent()})
    route(store, q, None, {}, wd)
    store.add_mention(mention())  # re-found: the route survives
    assert route(store, q, None, {}, wd) == {}
    assert q.row("Q9").sources == ["press:sir"]


def test_existing_candidate_gains_the_source(q, store, uncovered):
    q.add("Q9", "film", "facet", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 20, "instance_of": ["Q11424"]})
    store.add_mention(mention(p=0.7))
    store.add_mention(mention(p=0.9, url=URL + "2"))
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    r = q.row("Q9")
    assert r.sources == ["facet", "press:sir", "press:avvenire"]
    assert r.data[0]["press"] == {"sir": 0.7, "avvenire": 0.9}
    assert r.data[0]["count"] == 20


def test_music_waits_until_release(q, store, uncovered):
    store.add_mention(mention("Q7", "waiting:music"))
    assert route(store, q, None, {}, FakeWikidata({"Q7": ent("Canto")})) == {"waiting": 1}
    assert q.row("Q7") is None
    assert release_waiting(store, q, "art", None, {}, FakeWikidata({"Q7": ent()})) == {}
    assert release_waiting(store, q, "music", None, {}, FakeWikidata({"Q7": ent("Canto")})) == {"candidate": 1}
    r = q.row("Q7")
    assert r.domain == "music" and r.sources == ["press:sir"] and r.data[0]["press"] == {"sir": 0.8}
    assert store.mentions()[0].route == "candidate"
    assert release_waiting(store, q, "music", None, {}, FakeWikidata({})) == {}
    assert [row.domain for row in uncovered] == ["music"]  # asked about coverage as the released domain's work


def test_a_released_mention_of_a_covered_work_goes_to_enrichment(q, store, monkeypatch):
    """A work SeminaVerbi already covers is never proposed again: once its domain exists, its waiting press
    mentions feed the enrichment list, as route() decides for the others."""
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("drop", {"have": ["en"]}))
    store.add_mention(mention("Q7", "waiting:music"))
    route(store, q, None, {}, FakeWikidata({"Q7": ent("Canto")}))
    assert release_waiting(store, q, "music", None, {}, FakeWikidata({"Q7": ent("Canto")})) == {"enrichment": 1}
    assert q.row("Q7") is None
    assert [m.qid for m in store.mentions(route="enrichment")] == ["Q7"]


def test_a_waiting_mention_is_not_asked_about_coverage(q, store, uncovered):
    store.add_mention(mention("Q7", "waiting:music"))
    route(store, q, None, {}, FakeWikidata({"Q7": ent()}))
    assert uncovered == []


def frow(sources, press, count=3, domain="film"):
    return Row("Q1", domain, sources, {}, {}, False,
               data={0: {"count": count, "instance_of": ["Q7725634" if domain == "literature" else "Q11424"],
                         **({"press": press} if press is not None else {})}})


def test_press_exempt():
    assert common.press_exempt(frow(["press:sir"], {"sir": 0.6}))
    assert common.press_exempt(frow(["press:sir"], {"sir": 0.5, "avvenire": 0.7}))
    assert not common.press_exempt(frow(["press:sir"], {"sir": 0.55}))
    assert not common.press_exempt(frow(["press:sir"], {}))
    assert not common.press_exempt(frow(["facet"], None))
    assert not common.press_exempt(Row("Q1", "film", [], {}, {}, False))


@pytest.mark.parametrize("domain,mod", [("film", film), ("literature", literature)])
def test_triage_exempts_a_kept_press_mention(domain, mod):
    assert mod.triage(frow(["press:sir"], {"sir": 0.6}, 3, domain), {"sitelink_floor": 15})[0] == "pass"
    assert mod.triage(frow(["facet", "press:sir"], {"sir": 0.8}, 3, domain), {"sitelink_floor": 15})[0] == "pass"


@pytest.mark.parametrize("domain,mod", [("film", film), ("literature", literature)])
def test_triage_floor_applies_below_the_exemption(domain, mod):
    assert mod.triage(frow(["press:sir"], {"sir": 0.55}, 3, domain), {"sitelink_floor": 15})[0] == "drop"
    assert mod.triage(frow(["facet", "press:sir"], {"sir": 0.55}, 3, domain), {"sitelink_floor": 15})[0] == "drop"


class SeriesSparql:
    """Closure queries answer the class itself; the volumes query answers V1 as a part of S1."""

    def __init__(self):
        self.queries = []

    def select(self, query):
        self.queries.append(query)
        if "SELECT ?sub WHERE" in query:
            import re
            return [{"sub": re.search(r"wd:(Q\d+)", query).group(1)}]
        if "P527" in query:
            return [{"series": "S1", "item": "V1", "s_en": "https://en.wikipedia.org/wiki/Volume_One", "n": "4",
                     "types": "Q7725634"}]
        return []


def series_ent():
    return {"labels": {"en": {"value": "Saga"}},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q277759"}}}}]},
            "sitelinks": {"frwiki": {"title": "Saga"}}}


@pytest.fixture
def closure_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CLOSURE_CACHE", tmp_path / "closure.json")


def test_a_series_covered_through_a_volume_goes_to_enrichment(q, store, monkeypatch, closure_cache):
    """literature.covered (stage 1) calls a series covered when one of its volumes is: routing must agree, so a
    series SeminaVerbi covers through a volume is never proposed."""
    seen = []

    def covered(row, repo, wikis):
        seen.append(row.qid)
        return ("drop", {"have": ["en", "it", "es", "fr", "de", "pt"]}) if row.qid == "V1" else ("pass", {})
    monkeypatch.setattr(stages, "covered", covered)
    store.add_mention(mention("S1", "literature"))
    assert route(store, q, None, {}, FakeWikidata({"S1": series_ent()}), SeriesSparql()) == {"enrichment": 1}
    assert q.row("S1") is None and seen == ["S1", "V1"]
    assert [m.qid for m in store.mentions(route="enrichment")] == ["S1"]


def test_a_new_series_candidate_carries_its_volumes(q, store, uncovered, closure_cache):
    store.add_mention(mention("S1", "literature"))
    assert route(store, q, None, {}, FakeWikidata({"S1": series_ent()}), SeriesSparql()) == {"candidate": 1}
    stage0 = q.row("S1").data[0]
    assert stage0["series"] is True and stage0["volumes"] == {"V1": {"sitelinks": {"enwiki": "Volume One"},
                                                                     "count": 4}}
    assert stage0["press"] == {"sir": 0.8}


def test_a_domain_without_its_own_covered_uses_stages_covered(q, store, uncovered, monkeypatch):
    import types
    from pipeline import domains
    bare = types.SimpleNamespace(__name__="bare")
    monkeypatch.setattr(domains, "DOMAINS", {**domains.DOMAINS, "bare": bare})
    store.add_mention(mention("Q5", "bare"))
    assert route(store, q, None, {}, FakeWikidata({"Q5": ent()}), SeriesSparql()) == {"candidate": 1}
    assert [r.qid for r in uncovered] == ["Q5"]


def test_release_uses_the_domains_own_covered(q, store, uncovered, monkeypatch):
    import types
    from pipeline import domains
    asked = []
    music = types.SimpleNamespace(covered=lambda row, repo, wikis: (asked.append(row.domain) or ("translation", {})))
    monkeypatch.setattr(domains, "DOMAINS", {**domains.DOMAINS, "music": music})
    store.add_mention(mention("Q7", "waiting:music"))
    store.set_route(URL, "Q7", "waiting")
    assert release_waiting(store, q, "music", None, {}, FakeWikidata({"Q7": ent()})) == {"enrichment": 1}
    assert asked == ["music"] and uncovered == [] and q.row("Q7") is None


class PartsSparql:
    """Closure queries answer the class itself; the parts query answers P1 as a part of W1."""

    def __init__(self):
        self.queries = []

    def select(self, query):
        self.queries.append(query)
        if "SELECT ?sub WHERE" in query:
            import re
            return [{"sub": re.search(r"wd:(Q\d+)", query).group(1)}]
        if "P361" in query:
            return [{"item": "P1", "s_en": "https://en.wikipedia.org/wiki/Part_One", "n": "4", "types": "Q1"}]
        return []


def whole_ent(*classes):
    return {"labels": {"en": {"value": "Whole"}},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": c}}}} for c in classes]},
            "sitelinks": {"frwiki": {"title": "Whole"}}}


def _whole_class(domain):
    from pipeline.domains import art, music
    return music.FOLD_WHOLES[0] if domain == "music" else art.WORK_CLASSES[0]


@pytest.mark.parametrize("domain", ["music", "art"])
def test_a_whole_covered_through_a_part_goes_to_enrichment(q, store, monkeypatch, closure_cache, domain):
    """music and art stage 1 (covered_with_parts) call a whole covered when one of its parts is: routing must
    agree, so a whole SeminaVerbi covers through a part is never proposed."""
    seen = []

    def covered(row, repo, wikis):
        seen.append(row.qid)
        return ("drop", {"have": ["en", "it", "es", "fr", "de", "pt"]}) if row.qid == "P1" else ("pass", {})
    monkeypatch.setattr(stages, "covered", covered)
    store.add_mention(mention("W1", domain))
    wd = FakeWikidata({"W1": whole_ent(_whole_class(domain))})
    assert route(store, q, None, {}, wd, PartsSparql()) == {"enrichment": 1}
    assert q.row("W1") is None and seen == ["W1", "P1"]


@pytest.mark.parametrize("domain", ["music", "art"])
def test_a_new_whole_candidate_carries_its_parts(q, store, uncovered, closure_cache, domain):
    store.add_mention(mention("W1", domain))
    wd = FakeWikidata({"W1": whole_ent(_whole_class(domain))})
    assert route(store, q, None, {}, wd, PartsSparql()) == {"candidate": 1}
    stage0 = q.row("W1").data[0]
    assert stage0["parts"] == {"P1": {"sitelinks": {"enwiki": "Part One"}, "count": 4}}
    assert stage0["count"] == 4 and stage0["press"] == {"sir": 0.8}


@pytest.mark.parametrize("domain,classes", [("music", ["Q7366"]), ("music", ["Q105543609", "Q482994"]),
                                            ("art", ["Q41176"])])
def test_an_item_that_is_no_whole_gets_no_parts(closure_cache, domain, classes):
    """A song is not a composition; an album (music) or a building (art) never takes parts."""
    from pipeline import domains
    sparql = PartsSparql()
    assert domains.DOMAINS[domain].press_stage0("W1", whole_ent(*classes), sparql) == {}
    assert not any("P361" in qy for qy in sparql.queries)
