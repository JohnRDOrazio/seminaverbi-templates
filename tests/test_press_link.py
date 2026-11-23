import re
import sqlite3
from types import SimpleNamespace

import pytest

from pipeline import domains
from pipeline.domains import common, film, literature
from pipeline.press.extract import Page, Span
from pipeline.press.link import class_route, link_article
from pipeline.press.store import PressStore

URL = "https://example.org/cultura/a"  # the stored article URL (press_articles key)
CANONICAL = "https://example.org/en/cultura/a?ref=rss"  # what the page's own canonical link says

# class -> its subclasses (P279*), as the closure query would answer
CLOSURE = {
    "Q11424": ["Q11424", "Q24862"],          # film, short film
    "Q5398426": ["Q5398426"],                # television series
    "Q7725634": ["Q7725634", "Q8261"],       # literary work, novel
    "Q47461344": ["Q47461344"],
    "Q1667921": ["Q1667921"],
    "Q277759": ["Q277759"],
    "Q7366": ["Q7366"],                      # song
    "Q3305213": ["Q3305213"],                # painting
    "Q5": ["Q5"],                            # human
}
CFG = {"pick_p": 0.8, "pick_confidence": 0.6, "mention_keep": 0.6,
       "waiting_classes": {"music": ["Q105543609", "Q7366", "Q482994", "Q484692"],
                           "art": ["Q3305213", "Q860861"], "artists": ["Q5"]}}


class FakeSparql:
    def __init__(self):
        self.queries = []

    def select(self, query):
        self.queries.append(query)
        cls = re.search(r"wd:(Q\d+)", query).group(1)
        return [{"sub": s} for s in CLOSURE.get(cls, [cls])]


def entity(p31, labels=None, descriptions=None):
    return {"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": q}}}} for q in p31]},
            "labels": {l: {"value": v} for l, v in (labels or {}).items()},
            "descriptions": {l: {"value": v} for l, v in (descriptions or {}).items()}}


class FakeWikidata:
    def __init__(self, entities, search=None, sitelinks=None):
        self.entities, self._search, self._sitelinks = entities, search or {}, sitelinks or {}
        self.searches, self.sitelink_calls = [], []

    def search(self, text, lang):
        self.searches.append((text, lang))
        return list(self._search.get((text, lang), []))

    def by_sitelink(self, site, titles):
        self.sitelink_calls.append((site, list(titles)))
        return {t: self._sitelinks[site, t] for t in titles if (site, t) in self._sitelinks}

    def entity(self, qid):
        return self.entities.get(qid, {})


class FakeJudge:
    def __init__(self, pick=None, substantive=0.9):
        self._pick, self._substantive = pick, substantive
        self.picks, self.substantives = [], []

    def pick(self, url, passage, label, options):
        self.picks.append({"url": url, "passage": passage, "label": label, "options": options})
        return self._pick

    def substantive(self, url, label, passage):
        self.substantives.append({"url": url, "label": label, "passage": passage})
        return self._substantive


@pytest.fixture
def store():
    return PressStore(sqlite3.connect(":memory:"))


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "closure.json"


def page(paragraphs, spans, lang="it"):
    return Page(title="T", published="2026-11-20", lang=lang, canonical=CANONICAL, paragraphs=paragraphs, spans=spans)


def run(pg, store, judge, wikidata, cache, cfg=CFG):
    return link_article(pg, "sir", store, judge, wikidata, FakeSparql(), cfg, cache=cache, url=URL)


def test_wikilink_resolves_without_search(store, cache):
    pg = page(["Abbiamo rivisto Silence di Scorsese, un film sul dubbio."],
              [Span("Silence", 0, "wikilink", ("it", "Silence (film 2016)"))])
    wd = FakeWikidata({"Q18393766": entity(["Q11424"], {"it": "Silence"})},
                      sitelinks={("itwiki", "Silence (film 2016)"): "Q18393766"})
    judge = FakeJudge()
    out = run(pg, store, judge, wd, cache)
    assert wd.searches == []
    assert wd.sitelink_calls == [("itwiki", ["Silence (film 2016)"])]
    assert [(m.qid, m.domain, m.kind, m.route) for m in out] == [("Q18393766", "film", "work", None)]
    assert judge.picks == []
    assert store.mentions() == out


def test_one_typed_hit_is_taken_without_jev_pick(store, cache):
    pg = page(["Il romanzo Il Signore degli Anelli parla di misericordia e di sacrificio."],
              [Span("Il Signore degli Anelli", 0, "italic")])
    # three hits, only one of a class that routes: the others are discarded before any pick
    wd = FakeWikidata({"Q15228": entity(["Q8261"]), "Q1": entity(["Q4167410"]), "Q2": entity([])},
                      search={("Il Signore degli Anelli", "it"): ["Q1", "Q15228", "Q2"]})
    judge = FakeJudge(substantive=0.75)
    out = run(pg, store, judge, wd, cache)
    assert judge.picks == []
    assert [(m.qid, m.domain, m.p) for m in out] == [("Q15228", "literature", 0.75)]
    assert wd.searches == [("Il Signore degli Anelli", "it")]  # a routed hit in the page's lang: no en search


def test_search_falls_back_to_english(store, cache):
    pg = page(["Abbiamo visto The Mission, il film di Joffé."], [Span("The Mission", 0, "italic")])
    wd = FakeWikidata({"Q7": entity(["Q11424"])}, search={("The Mission", "en"): ["Q7"]})
    out = run(pg, store, FakeJudge(), wd, cache)
    assert wd.searches == [("The Mission", "it"), ("The Mission", "en")]
    assert [m.qid for m in out] == ["Q7"]


def test_ambiguous_title_is_dropped_below_the_cut(store, cache):
    pg = page(["Ben-Hur resta un classico."], [Span("Ben-Hur", 0, "italic")])
    wd = FakeWikidata({"Q9": entity(["Q11424"], {"en": "Ben-Hur"}, {"en": "1959 film"}),
                       "Q3": entity(["Q11424"], {"it": "Ben-Hur"}, {"it": "film del 1925"})},
                      search={("Ben-Hur", "it"): ["Q9", "Q3"]})
    judge = FakeJudge(pick=("Q9", 0.7, 0.9))
    assert run(pg, store, judge, wd, cache) == []
    assert store.mentions() == []
    assert judge.substantives == []
    # options sorted by QID (stable cache key), labelled in the page's language, else en
    assert list(judge.picks[0]["options"]) == ["Q3", "Q9"]
    assert judge.picks[0]["options"] == {"Q3": "Ben-Hur — film del 1925", "Q9": "Ben-Hur — 1959 film"}
    assert judge.picks[0]["label"] == "Ben-Hur"


def test_ambiguous_title_is_taken_above_the_cut(store, cache):
    pg = page(["Ben-Hur resta un classico."], [Span("Ben-Hur", 0, "italic")])
    wd = FakeWikidata({"Q9": entity(["Q11424"]), "Q3": entity(["Q11424"])}, search={("Ben-Hur", "it"): ["Q9", "Q3"]})
    assert [m.qid for m in run(pg, store, FakeJudge(pick=("Q9", 0.85, 0.65)), wd, cache)] == ["Q9"]
    # low confidence, or "none of these": dropped
    store2 = PressStore(sqlite3.connect(":memory:"))
    assert run(pg, store2, FakeJudge(pick=("Q9", 0.95, 0.5)), wd, cache) == []
    assert run(pg, store2, FakeJudge(pick=(None, 0.95, 0.9)), wd, cache) == []


def test_passing_mention_is_dropped(store, cache):
    pg = page(["Nell'omelia ha citato Silence."], [Span("Silence", 0, "italic")])
    wd = FakeWikidata({"Q1": entity(["Q11424"])}, search={("Silence", "it"): ["Q1"]})
    judge = FakeJudge(substantive=0.5)
    assert run(pg, store, judge, wd, cache) == []
    assert store.mentions() == []
    assert judge.substantives[0]["label"] == "Silence"
    assert judge.substantives[0]["url"] == URL


def test_person_kind_and_waiting_route(store, cache):
    pg = page(["Martin Scorsese racconta la sua fede."], [Span("Martin Scorsese", 0, "wikilink", ("it", "Martin Scorsese"))])
    wd = FakeWikidata({"Q41148": entity(["Q5"])}, sitelinks={("itwiki", "Martin Scorsese"): "Q41148"})
    out = run(pg, store, FakeJudge(), wd, cache)
    assert [(m.kind, m.domain) for m in out] == [("person", "waiting:artists")]


def test_routes_music_art_and_people_to_waiting(cache):
    assert [class_route(p31, FakeSparql(), CFG, cache) for p31 in (["Q7366"], ["Q3305213"], ["Q5"])] == \
        ["waiting:music", "waiting:art", "waiting:artists"]
    assert class_route(["Q4167410"], FakeSparql(), CFG, cache) is None
    assert class_route([], FakeSparql(), CFG, cache) is None


def test_a_present_module_takes_its_items(monkeypatch, cache):
    music = SimpleNamespace(owns=lambda instance_of, sparql, cache=None: "Q7366" in instance_of)
    monkeypatch.setattr(domains, "DOMAINS", {"film": film, "literature": literature, "music": music})
    assert class_route(["Q7366"], FakeSparql(), CFG, cache) == "music"
    # the waiting class of a present domain is never used, even when its own owns() says no
    music.owns = lambda instance_of, sparql, cache=None: False
    assert class_route(["Q7366"], FakeSparql(), CFG, cache) is None


def test_film_wins_over_a_broader_class(cache):
    assert class_route(["Q3305213", "Q24862"], FakeSparql(), CFG, cache) == "film"


def test_domain_classes_and_owns(cache):
    assert film.WORK_CLASSES == ("Q11424", "Q5398426")
    assert literature.WORK_CLASSES == ("Q7725634", "Q1667921", "Q277759")
    assert film.owns(["Q24862"], FakeSparql(), cache) is True
    assert film.owns(["Q8261"], FakeSparql(), cache) is False
    assert literature.owns(["Q8261"], FakeSparql(), cache) is True
    assert literature.owns(["Q277759"], FakeSparql(), cache) is True


def test_at_most_three_passages_each_25_words(store, cache):
    filler = " ".join(f"parola{i}" for i in range(40))
    paragraphs = [f"{filler} Silence {filler}" for _ in range(5)] + ["Un paragrafo senza il titolo."]
    pg = page(paragraphs, [Span("Silence", 0, "italic"), Span("Silence", 3, "quoted")])
    wd = FakeWikidata({"Q1": entity(["Q11424"])}, search={("Silence", "it"): ["Q1"]})
    judge = FakeJudge()
    out = run(pg, store, judge, wd, cache)
    assert len(out) == 1  # one mention for the repeated title, judged once
    assert len(judge.substantives) == 1
    assert len(judge.substantives[0]["passage"].split()) <= 150
    assert len(out[0].passages) == 3
    assert all(len(p.split()) <= 25 and "Silence" in p for p in out[0].passages)
    assert store.mentions()[0].passages == out[0].passages


def test_two_spans_of_one_item_make_one_mention(store, cache):
    pg = page(["Il film Silence, cioè «Silenzio» nell'edizione italiana."],
              [Span("Silence", 0, "italic"), Span("Silenzio", 0, "quoted")])
    wd = FakeWikidata({"Q1": entity(["Q11424"])}, search={("Silence", "it"): ["Q1"], ("Silenzio", "it"): ["Q1"]})
    judge = FakeJudge()
    out = run(pg, store, judge, wd, cache)
    assert [(m.qid, m.label) for m in out] == [("Q1", "Silence")]
    assert len(judge.substantives) == 1


def test_no_sparql_of_its_own(store, cache):
    """Class membership comes only from owns() and its cached closure: the only queries are closure queries,
    each class asked at most once across an article."""
    pg = page(["Silence e Ben-Hur."], [Span("Silence", 0, "italic"), Span("Ben-Hur", 0, "italic")])
    wd = FakeWikidata({"Q1": entity(["Q11424"]), "Q2": entity(["Q11424"])},
                      search={("Silence", "it"): ["Q1"], ("Ben-Hur", "it"): ["Q2"]})
    sparql = FakeSparql()
    link_article(pg, "sir", store, FakeJudge(), wd, sparql, CFG, cache=cache, url=URL)
    assert all(q.startswith("SELECT ?sub WHERE") for q in sparql.queries)
    assert len(sparql.queries) == len(set(sparql.queries))


def test_mentions_and_jev_are_keyed_by_the_stored_url_not_the_canonical(store, cache):
    pg = page(["Ben-Hur resta un classico."], [Span("Ben-Hur", 0, "italic")])
    assert pg.canonical != URL
    wd = FakeWikidata({"Q9": entity(["Q11424"]), "Q3": entity(["Q11424"])}, search={("Ben-Hur", "it"): ["Q9", "Q3"]})
    judge = FakeJudge(pick=("Q9", 0.9, 0.9))
    out = run(pg, store, judge, wd, cache)
    assert [m.url for m in out] == [URL]
    assert [m.url for m in store.mentions()] == [URL]
    assert [c["url"] for c in judge.picks + judge.substantives] == [URL, URL]


def test_url_is_a_required_keyword(store, cache):
    pg = page(["Silence."], [Span("Silence", 0, "italic")])
    with pytest.raises(TypeError):
        link_article(pg, "sir", store, FakeJudge(), FakeWikidata({}), FakeSparql(), CFG, cache=cache)
    with pytest.raises(TypeError):
        link_article(pg, "sir", store, FakeJudge(), FakeWikidata({}), FakeSparql(), CFG, URL)


def test_a_written_work_only_item_does_not_route_to_literature(cache):
    """Written work (Q47461344) covers encyclicals, letters and scholarly articles: they must not reach
    literature, which itself queries literary works and series only."""
    assert literature.owns(["Q47461344"], FakeSparql(), cache) is False
    assert class_route(["Q47461344"], FakeSparql(), CFG, cache) is None
