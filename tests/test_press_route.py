import pytest
import requests

from pipeline import load_config, stages
from pipeline.domains import art, common, film, literature, music
from pipeline.press.route import release_waiting, route
from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue, Row
from sync.wiki import SyncError

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


def test_a_qid_of_another_domains_row_is_routed_under_that_domain(q, store, uncovered):
    """The queue is keyed by QID alone: a press 'film' mention of a literature row is that row's evidence, decided
    and recorded under literature, never a film candidate without a film row."""
    q.add("Q9", "literature", "category", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 20})
    store.add_mention(mention())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"candidate": 1}
    r = q.row("Q9")
    assert r.domain == "literature" and r.sources == ["category", "press:sir"]
    assert r.data[0]["press"] == {"sir": 0.8} and r.data[0]["count"] == 20
    assert [row.domain for row in uncovered] == ["literature"]
    m = store.mentions()[0]
    assert (m.domain, m.route) == ("literature", "candidate")


def test_a_qid_of_an_unloaded_domains_row_is_left_unrouted(q, store, uncovered, capsys):
    q.add("Q9", "nowhere", "category", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 20})
    store.add_mention(mention())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {}
    assert q.row("Q9").sources == ["category"] and store.mentions()[0].route is None
    assert "nowhere" in capsys.readouterr().err


def test_a_released_mention_of_another_domains_row_goes_to_that_row(q, store, uncovered):
    q.add("Q7", "art", "category", {"en": "Canto"}, {"enwiki": "Canto"}, data={"count": 20})
    store.add_mention(mention("Q7", "waiting:music"))
    store.set_route(URL, "Q7", "waiting")
    assert release_waiting(store, q, "music", None, {}, FakeWikidata({"Q7": ent()})) == {"candidate": 1}
    assert q.row("Q7").domain == "art" and q.row("Q7").sources == ["category", "press:sir"]
    assert store.mentions()[0].domain == "art"


def test_press_evidence_reopens_a_floor_dropped_row_for_triage(q, store, uncovered):
    q.add("Q9", "film", "facet", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 3, "instance_of": ["Q11424"]})
    q.set_outcome("Q9", 1, "pass")
    q.set_outcome("Q9", 2, "drop", {"reason": "3 sitelinks, under the floor of 15"})
    store.add_mention(mention(p=0.8))
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    r = q.row("Q9")
    assert 2 not in r.outcomes and r.outcomes[1] == "pass"
    assert [row.qid for row in q.pending(2)] == ["Q9"]


@pytest.mark.parametrize("p,status", [(0.5, None), (0.8, "declined"), (0.8, "open")])
def test_a_drop_stays_without_exemption_or_with_a_status(q, store, uncovered, p, status):
    q.add("Q9", "film", "facet", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 3, "instance_of": ["Q11424"]})
    q.set_outcome("Q9", 1, "pass")
    q.set_outcome("Q9", 2, "drop")
    if status:
        q.set_status("Q9", status)
    store.add_mention(mention(p=p))
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    assert q.row("Q9").outcomes[2] == "drop"


def test_the_dry_run_decides_under_the_rows_domain(q, store, uncovered):
    from pipeline.press.stages import would_route
    q.add("Q9", "literature", "category", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 20})
    store.add_mention(mention())
    assert [k for _, k in would_route(store, None, {}, FakeWikidata({"Q9": ent()}), queue=q)] == ["candidate"]
    assert [row.domain for row in uncovered] == ["literature"]
    assert store.mentions()[0].domain == "film" and q.row("Q9").sources == ["category"]  # writes nothing


def _passed(q, status=None):
    q.add("Q9", "film", "facet", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 20, "press": {"sir": 0.7}})
    for stage in (1, 2, 3, 4):
        q.set_outcome("Q9", stage, "pass")
    if status:
        q.set_status("Q9", status)


@pytest.mark.parametrize("p,url", [(0.9, URL), (0.8, URL + "2")])  # a higher p, a new outlet
def test_new_press_evidence_rebuilds_a_passed_dossier(q, store, uncovered, p, url):
    _passed(q)
    store.add_mention(mention(p=p, url=url))
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    r = q.row("Q9")
    assert 4 not in r.outcomes and r.outcomes[3] == "pass"


@pytest.mark.parametrize("status", ["open", "declined"])
def test_a_dossier_stays_with_a_status(q, store, uncovered, status):
    _passed(q, status)
    store.add_mention(mention(p=0.9))
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    assert q.row("Q9").outcomes[4] == "pass"


def test_an_interrupted_release_leaves_the_mention_waiting(q, store, uncovered, monkeypatch):
    from pipeline.press import route as route_mod
    q.add("Q7", "art", "category", {"en": "Canto"}, {"enwiki": "Canto"}, data={"count": 20})
    store.add_mention(mention("Q7", "waiting:music"))
    store.set_route(URL, "Q7", "waiting")

    real = route_mod._add_candidate
    monkeypatch.setattr(route_mod, "_add_candidate", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cut")))
    with pytest.raises(RuntimeError):
        release_waiting(store, q, "music", None, {}, FakeWikidata({"Q7": ent()}))
    m = store.mentions()[0]
    assert (m.domain, m.route) == ("waiting:music", "waiting")
    monkeypatch.setattr(route_mod, "_add_candidate", real)
    assert release_waiting(store, q, "music", None, {}, FakeWikidata({"Q7": ent()})) == {"candidate": 1}
    m = store.mentions()[0]
    assert (m.domain, m.route) == ("art", "candidate")


def test_a_second_article_of_the_same_outlet_rebuilds_the_dossier(q, store, uncovered):
    store.add_article(URL + "3", "sir", "it", "Terzo", "2026-11-22")
    _passed(q)  # press {"sir": 0.7}
    store.add_mention(mention(p=0.6, url=URL + "3"))  # same outlet, lower p: the press map is unchanged
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    assert 4 not in q.row("Q9").outcomes


class FailingWikidata(FakeWikidata):
    """A Wikidata whose lookups of `failing` still fail after the client's retries."""

    def __init__(self, entities, failing, error):
        super().__init__(entities)
        self.failing, self.error = failing, error

    def entity(self, qid):
        if qid in self.failing:
            self.asked.append(qid)
            raise self.error
        return super().entity(qid)


@pytest.mark.parametrize("error", [
    requests.exceptions.ConnectionError("Remote end closed connection without response"),
    SyncError("sparql: HTTP 503 after 3 retries"),
])
def test_a_failed_lookup_leaves_that_mention_unrouted_and_routes_the_next(q, store, uncovered, capsys, error):
    store.add_mention(mention("Q8"))
    store.add_mention(mention("Q9"))
    counts = route(store, q, None, {}, FailingWikidata({"Q9": ent()}, {"Q8"}, error))
    assert counts == {"candidate": 1, "errors": 1}
    assert [m.qid for m in store.unrouted()] == ["Q8"]
    assert q.row("Q8") is None and q.row("Q9") is not None
    assert "Q8" in capsys.readouterr().err


def test_a_failed_stage0_query_leaves_that_mention_unrouted(q, store, uncovered, capsys, monkeypatch):
    """A domain's press_stage0 runs WDQS (literature: a series' volumes); its failure is that mention's alone."""
    from pipeline import domains
    calls = []

    def stage0(qid, entity, sparql):
        calls.append(qid)
        if qid == "Q8":
            raise requests.exceptions.ConnectionError("dropped")
        return {}
    monkeypatch.setattr(domains.DOMAINS["film"], "press_stage0", stage0, raising=False)
    store.add_mention(mention("Q8"))
    store.add_mention(mention("Q9"))
    assert route(store, q, None, {}, FakeWikidata({"Q8": ent(), "Q9": ent()}), sparql=object()) == \
        {"candidate": 1, "errors": 1}
    assert calls == ["Q8", "Q9"]
    assert [m.qid for m in store.unrouted()] == ["Q8"]


def test_the_dry_run_preview_goes_on_past_a_failed_lookup(q, store, uncovered, capsys):
    from pipeline.press.stages import preview
    store.add_mention(mention("Q8"))
    store.add_mention(mention("Q9"))
    lines = preview(store, None, {}, FailingWikidata({"Q9": ent()}, {"Q8"},
                                                     requests.exceptions.ConnectionError("dropped")), queue=q)
    assert any("candidate" in l and "Q9" in l for l in lines)
    assert not any("Q8" in l for l in lines)
    assert "Q8" in capsys.readouterr().err


@pytest.fixture(params=["neutral", "critical"])
def unsupporting(request):
    def make(**kw):
        m = mention(**kw)
        m.stance, m.stance_outcome = 0.1, request.param
        return m
    return make


def test_a_neutral_or_critical_mention_never_becomes_a_candidate(q, store, unsupporting, uncovered):
    store.add_mention(unsupporting())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"dropped": 1}
    assert q.row("Q9") is None and store.mentions()[0].route == "dropped"


def test_a_neutral_or_critical_mention_adds_nothing_to_an_existing_row(q, store, unsupporting, uncovered):
    q.add("Q9", "film", "facet", {"en": "Ordet"}, {"enwiki": "Ordet"}, data={"count": 2, "instance_of": ["Q11424"]})
    q.set_outcome("Q9", 2, "drop", {"reason": "floor"})
    before = (q.row("Q9").sources, q.row("Q9").data, q.row("Q9").outcomes)
    store.add_mention(unsupporting(p=0.95))
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"dropped": 1}
    r = q.row("Q9")
    assert (r.sources, r.data, r.outcomes) == before and not common.press_exempt(r)


def test_a_neutral_or_critical_mention_of_a_covered_work_still_goes_to_enrichment(q, store, unsupporting, monkeypatch):
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("drop", {"have": ["en"]}))
    store.add_mention(unsupporting())
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"enrichment": 1}


def test_a_released_neutral_or_critical_mention_is_dropped_too(q, store, unsupporting, uncovered):
    store.add_mention(unsupporting(qid="Q7", domain="waiting:music"))
    wd = FakeWikidata({"Q7": ent("Canto")})
    assert route(store, q, None, {}, wd) == {"waiting": 1}
    assert release_waiting(store, q, "music", None, {}, wd) == {"dropped": 1}
    assert q.row("Q7") is None


def test_the_preview_shows_the_drop_and_the_scores(q, store, unsupporting, uncovered):
    from pipeline.press.stages import preview
    store.set_article(URL, "linked", "keep", {"culture": 0.9, "angle": 0.55})
    store.add_mention(unsupporting())
    store.add_mention(mention("Q8", url=URL + "2"))
    lines = preview(store, None, {}, FakeWikidata({"Q9": ent(), "Q8": ent()}), queue=q)
    assert lines[0].split()[0] == "dropped" and "culture=0.90 angle=0.55 stance=0.10 (" in lines[0]
    assert lines[1].split()[0] == "candidate" and "stance" not in lines[1]


@pytest.mark.parametrize("outcome", ["bearing", "mixed", None])
def test_a_bearing_or_mixed_mention_or_one_never_asked_becomes_a_candidate(q, store, uncovered, outcome):
    m = mention()
    m.stance_outcome = outcome
    store.add_mention(m)
    assert route(store, q, None, {}, FakeWikidata({"Q9": ent()})) == {"candidate": 1}


@pytest.mark.parametrize("domain,mod,cls", [("music", music, "Q7366"), ("art", art, "Q3305213")])
def test_music_and_art_triage_exempt_a_kept_press_mention(domain, mod, cls):
    """The exemption from the sitelink floor is every domain's; what triage drops for another reason stays dropped."""
    cfg = common.domain_config(load_config(), domain)

    def result(p, classes=(cls,), sources=("press:sir",), **more):
        data = {"count": 3, "instance_of": list(classes), "press": {"sir": p}, **more}
        return mod.triage(Row("Q1", domain, list(sources), {}, {}, False, data={0: data}), cfg)[0]
    assert result(0.6) == "pass" and result(0.8, sources=("genre", "press:sir")) == "pass"
    assert result(0.55) == "drop"
    assert result(0.9, classes=("Q5",)) == "drop" and result(0.9, folded_into="Q2") == "drop"
    if domain == "music":
        assert result(0.6, sources=("artist", "press:sir")) == "pass"
