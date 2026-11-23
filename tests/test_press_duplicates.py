"""One author's article on his blog and in the newspaper it first appeared in is one piece of evidence."""
import json
import random
import sqlite3

import pytest

from pipeline import stages as pipeline_stages
from pipeline.press.duplicates import fingerprint, overlap, same_article
from pipeline.press.enrich import enrichment
from pipeline.press.notes import press_notes
from pipeline.press.route import route
from pipeline.press.stages import Pages, run_filter
from pipeline.press.store import Article, Mention, PressStore
from pipeline.publish import _params
from pipeline.queue import Queue, Row
from tests.test_press_entry import entry, feed
from tests.test_press_fetch import Resp
from tests.test_press_route import FakeWikidata, ent
from tests.test_press_stages import ROBOTS, Judge, client

BLOG, PAPER = "https://x.example/2024/04/un-film.html", "https://x.example/news/un-film"
FEED = "https://x.example/feeds?path={path}"
OUTLETS = [{"id": "osservatore", "name": "L'Osservatore Romano"},
           {"id": "vallini", "name": "Gaetano Vallini, Camera con vista", "author": "Gaetano Vallini",
            "republishes": "osservatore", "article_feed": FEED}]
NAMES = {o["id"]: o["name"] for o in OUTLETS}
CFG = {"max_articles_per_run": 10, "culture_drop": 0.5, "angle_drop": 0.4, "outlets": OUTLETS}
WORDS = [f"parola{i}" for i in range(900)]


def text(seed, n=600):
    return random.Random(seed).choices(WORDS, k=n)


def edited(words, every):
    """The text with one word in `every` changed."""
    return [f"nuova{i}" if i % every == 0 else w for i, w in enumerate(words)]


REVIEW = text(1)


def fp(words):
    return fingerprint([" ".join(words)])


def test_a_fingerprint_is_short_and_not_the_text():
    f = fp(REVIEW)
    assert len(f) <= 64 * 13 and not set(f.split()) & set(REVIEW)
    assert fingerprint(["Perché l’opera: " + " ".join(REVIEW)]) == fingerprint(["PERCHE' L'OPERA ", " ".join(REVIEW)])
    assert fingerprint(["troppo breve per dire qualcosa"]) is None


def test_overlap_tells_the_same_text_from_another():
    assert overlap(fp(REVIEW), fp(REVIEW)) == 1
    assert overlap(fp(REVIEW), fp(edited(REVIEW, 40))) > 0.6
    assert overlap(fp(REVIEW), fp(text(2))) < 0.05


def art(outlet, title="La scelta di Delia", published="2023-10-26", lang="it", counts_as=None):
    return Article("u" + outlet, outlet, lang, title, published, counts_as=counts_as)


CREDITED = art("vallini", counts_as="osservatore")


@pytest.mark.parametrize("blog,paper,words,same", [
    (art("vallini"), art("osservatore", "Altro titolo", "2023-01-01"), REVIEW, True),  # the text alone
    (art("vallini"), art("osservatore"), edited(REVIEW, 40), True),
    (CREDITED, art("osservatore", "La scelta di Delia - L'Osservatore Romano", "2023-10-25T16:00:00+02:00"),
     edited(REVIEW, 12), True),  # a heavier edit: the title and the date agree too
    (CREDITED, art("osservatore", "LA SCELTA DI DELIA!"), edited(REVIEW, 12), True),
    (CREDITED, art("osservatore", "Un altro titolo"), edited(REVIEW, 12), False),
    (CREDITED, art("osservatore", published="2023-11-20"), edited(REVIEW, 12), False),
    (CREDITED, art("osservatore", published=None), edited(REVIEW, 12), False),
    (art("vallini"), art("osservatore"), edited(REVIEW, 12), False),  # the post names no issue
    (CREDITED, art("osservatore"), text(2), False),  # same title, same day, another text
    (CREDITED, art("osservatore", lang="en"), REVIEW, False),
])
def test_when_two_articles_are_one(blog, paper, words, same):
    assert same_article(blog, fp(REVIEW), paper, fp(words)) is same


def test_short_titles_prove_nothing():
    a, b = art("vallini", "Delia", counts_as="osservatore"), art("osservatore", "Delia")
    assert same_article(a, fp(REVIEW), b, fp(edited(REVIEW, 12))) is False


def test_without_a_fingerprint_nothing_is_merged():
    """A subscribers-only page holds no text: its title and date alone are not enough."""
    assert same_article(CREDITED, fp(REVIEW), art("osservatore"), None) is False


# ---- the stages ----

class Counting(Judge):
    def __init__(self, culture=0.9):
        self.asked, self.value = [], culture

    def culture(self, url, text):
        self.asked.append(url)
        return self.value


def paper_page(words=REVIEW):
    return Resp(f"<html><head><title>Un film - L'Osservatore Romano</title></head><body><p>{' '.join(words)}</p></body></html>")


def blog_feed(words=REVIEW, credit=""):
    return Resp(feed(entry(f"<p>di Gaetano Vallini</p><div>{' '.join(words)}</div><b>{credit}</b>", url=BLOG)))


def pair(store, order, judge, blog_words=REVIEW, credit=""):
    """Finds and filters the two articles in two runs, in the order given."""
    routes = {ROBOTS: Resp(""), PAPER: paper_page(),
              FEED.replace("{path}", "/2024/04/un-film.html"): blog_feed(blog_words, credit)}
    counts = []
    for url in order:
        store.add_article(url, "vallini" if url == BLOG else "osservatore", "it", published="2024-04-24")
        counts.append(run_filter(store, Pages(client(routes), OUTLETS), judge, CFG))
    return counts


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


@pytest.fixture
def store(q):
    return PressStore.of(q)


@pytest.mark.parametrize("order", [(BLOG, PAPER), (PAPER, BLOG)])
@pytest.mark.parametrize("culture,outcome", [(0.9, "keep"), (0.1, "drop")])
def test_the_second_copy_is_not_judged_again(store, order, culture, outcome):
    judge = Counting(culture)
    counts = pair(store, order, judge)
    assert judge.asked == [order[0]]
    assert counts[1] == {"skip (duplicate)": 1}
    first, second = store.article(order[0]), store.article(order[1])
    assert first.outcome == outcome
    assert (second.stage, second.outcome, second.data) == ("filtered", "skip",
                                                           {"reason": "duplicate", "of": order[0]})
    assert store.article(BLOG).same_as == PAPER and store.article(BLOG).evidence_outlet == "osservatore"
    assert store.article(PAPER).same_as is None


@pytest.mark.parametrize("order", [(BLOG, PAPER), (PAPER, BLOG)])
def test_different_articles_are_both_judged(store, order):
    judge = Counting()
    pair(store, order, judge, blog_words=text(2))
    assert len(judge.asked) == 2 and store.article(BLOG).same_as is None


def test_no_article_text_is_stored_and_other_outlets_have_no_fingerprint(store, q):
    pair(store, (BLOG, PAPER), Counting())
    store.add_article("https://x.example/n", "sir", "it")
    run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/n": paper_page()}), OUTLETS), Counting(), CFG)
    dump = "\n".join(q.db.iterdump())
    assert not any(" ".join(REVIEW[i:i + 3]) in dump for i in range(0, 500, 7))
    assert {u for u, _, _ in store.fingerprints("sir")} == set()
    assert [a.url for a in store.copies(PAPER)] == [BLOG]


def test_a_first_copy_jev_could_not_judge_does_not_hide_the_second(store):
    from typesafe_sdk import TypeSafeError

    class Down(Judge):
        def culture(self, url, text):
            raise TypeSafeError("down")
    pair(store, (PAPER,), Down())
    judge = Counting()
    pair(store, (BLOG,), judge)
    assert judge.asked == [BLOG]
    assert {store.article(BLOG).outcome, store.article(PAPER).outcome} == {"keep", "skip"}


def mention(store, url, p=0.8, kind="candidate", passages=("un passo",), qid="Q9"):
    store.add_mention(Mention(url, qid, "Ordet", "work", "film", p, list(passages), kind))


@pytest.fixture
def uncovered(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "covered", lambda row, repo, wikis: ("pass", {}))


@pytest.mark.parametrize("order", [(BLOG, PAPER), (PAPER, BLOG)])
def test_the_pair_is_one_outlet_in_the_queue(store, q, uncovered, order):
    pair(store, order, Counting())
    mention(store, order[0], kind=None)
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    row = q.row("Q9")
    assert row.sources == ["press:osservatore"] and row.data[0]["press"] == {"osservatore": 0.8}


def test_evidence_routed_before_the_copy_was_found_is_moved_to_the_newspaper(store, q, uncovered):
    """The post came first and counted for the blog; once it proves the newspaper's, a further article of the
    newspaper does not make two outlets agree."""
    pair(store, (BLOG,), Counting())
    mention(store, BLOG, 0.9, kind=None)
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    assert q.row("Q9").data[0]["press"] == {"vallini": 0.9}
    pair(store, (PAPER,), Counting())
    store.add_article("https://x.example/other", "osservatore", "it")
    mention(store, "https://x.example/other", 0.7, kind=None)
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    row = q.row("Q9")
    assert row.data[0]["press"] == {"osservatore": 0.9} and row.sources == ["press:osservatore"]


def test_the_blogs_own_evidence_stays_beside_the_newspapers(store, q, uncovered):
    pair(store, (BLOG, PAPER), Counting())
    store.add_article("https://x.example/own", "vallini", "it")
    for url in (BLOG, "https://x.example/own"):
        mention(store, url, kind=None)
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    assert q.row("Q9").data[0]["press"] == {"osservatore": 0.8, "vallini": 0.8}


@pytest.mark.parametrize("order", [(BLOG, PAPER), (PAPER, BLOG)])
@pytest.mark.parametrize("both", [False, True])
def test_the_pair_is_one_note_with_both_links(store, order, both):
    pair(store, order, Counting())
    for url in order if both else order[:1]:
        mention(store, url)
    (note,) = press_notes(store, "Q9", NAMES)
    main, other = (PAPER, BLOG) if both else order
    assert note["url"] == main and [a["url"] for a in note["also"]] == [other]
    label = {BLOG: "Gaetano Vallini, Camera con vista (2024-04-24): Un film",
             PAPER: "L'Osservatore Romano (2024-04-24): Un film - L'Osservatore Romano"}
    assert note["label"] == label[main] and note["also"][0]["label"] == label[other]
    assert note["text"] == "un passo"


def test_both_links_are_among_the_proposals_sources():
    row = Row("Q9", "film", [], {}, {}, False, data={4: {"notes": [
        {"url": "https://p", "label": "P", "text": "t", "lang": "it", "also": [{"url": "https://b", "label": "B"}]}]}})
    result = {"connection": {"choice": "x"}, "field": {"choice": "x"}, "themes": [], "outline": [],
              "first_wiki": "it"}
    p = dict(_params(row, result))
    assert p["sources"] == "https://p https://b" and (p["source1_label"], p["source2_label"]) == ("P", "B")


@pytest.mark.parametrize("order", [(BLOG, PAPER), (PAPER, BLOG)])
@pytest.mark.parametrize("both", [False, True])
def test_the_pair_is_one_enrichment_entry_with_both_links(store, order, both):
    pair(store, order, Counting())
    for url in order if both else order[:1]:
        mention(store, url, kind="enrichment")
    page = enrichment(store, NAMES, None)
    assert page.count("\n** ") == 1 and f"[{PAPER} " in page and f"[{BLOG} " in page
    for url in (BLOG, PAPER):  # closing either closes the entry
        done = enrichment(store, NAMES, page + "{{SV press done|%s}}" % url)
        assert "\n** " not in done.split("<!-- press:end -->")[0]


def test_an_only_critical_copy_gives_no_note(store):
    pair(store, (BLOG, PAPER), Counting())
    store.add_mention(Mention(BLOG, "Q9", "Ordet", "work", "film", 0.8, ["x"], "dropped", critical=True))
    assert press_notes(store, "Q9", NAMES) == []
