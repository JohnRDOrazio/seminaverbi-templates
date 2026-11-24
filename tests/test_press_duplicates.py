"""One author's article on his blog and in the newspaper it first appeared in is one piece of evidence."""
import json
import random
import sqlite3

import pytest

from pipeline import stages as pipeline_stages
from pipeline.press.duplicates import fingerprint, overlap, same_article
from pipeline.press.enrich import enrichment
from pipeline.press.notes import press_notes
from pipeline.domains import common, film
from pipeline.press.route import reconcile, route
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
CFG = {"max_articles_per_day": 10, "culture_drop": 0.5, "angle_drop": 0.4, "outlets": OUTLETS}
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


def art(outlet, title="La scelta di Delia", published="2023-10-26", lang="it", counts_as=None, credit="Gaetano Vallini, L'Osservatore Romano"):
    return Article("u" + outlet, outlet, lang, title, published, attribution=counts_as and credit, counts_as=counts_as)


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
    (art("vallini", counts_as="osservatore", credit=None), art("osservatore"), edited(REVIEW, 12), False),  # a copy, no credit
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


@pytest.mark.parametrize("outcome", ["critical", "neutral"])
def test_a_copy_that_does_not_support_gives_no_note(store, outcome):
    pair(store, (BLOG, PAPER), Counting())
    store.add_mention(Mention(BLOG, "Q9", "Ordet", "work", "film", 0.8, ["x"], "dropped",
                              stance_outcome=outcome))
    assert press_notes(store, "Q9", NAMES) == []


BLOG2 = "https://x.example/2025/01/di-nuovo.html"


def second_post(store, judge):
    store.add_article(BLOG2, "vallini", "it", published="2025-01-02")
    routes = {ROBOTS: Resp(""), FEED.replace("{path}", "/2025/01/di-nuovo.html"): Resp(feed(
        entry(f"<div>{' '.join(REVIEW)}</div>", url=BLOG2)))}
    return run_filter(store, Pages(client(routes), OUTLETS), judge, CFG)


@pytest.mark.parametrize("order", [(BLOG, PAPER), (PAPER, BLOG)])
def test_a_third_copy_is_not_judged_whichever_copy_was(store, order):
    judge = Counting()
    pair(store, order, judge)
    assert second_post(store, judge) == {"skip (duplicate)": 1}
    assert judge.asked == [order[0]] and store.article(BLOG2).data["of"] == order[0]
    assert store.article(BLOG2).same_as == PAPER


def test_a_copy_of_unjudged_copies_is_judged(store):
    pair(store, (PAPER,), Counting())
    store.set_article(PAPER, "filtered", "skip", {"reason": "HTTP 404"})
    judge = Counting()
    assert second_post(store, judge) == {"keep": 1} and judge.asked == [BLOG2]


# ---- the queue's press map is what its supporting mentions say, under their present evidence outlet ----

OWN, OTHER = "https://x.example/own", "https://x.example/other"


def routed(store, q, url, p, outlet=None):
    if outlet:
        store.add_article(url, outlet, "it")
        store.set_article(url, "linked", "keep", {})
    mention(store, url, p, kind=None)
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))


def press(q):
    row = q.row("Q9")
    assert {s for s in row.sources if s.startswith("press:")} == {f"press:{o}" for o in row.data[0]["press"]}
    return row.data[0]["press"]


def test_a_copy_found_moves_the_evidence_with_no_further_candidate(store, q, uncovered):
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    routed(store, q, OTHER, 0.7, "osservatore")
    assert press(q) == {"vallini": 0.9, "osservatore": 0.7}
    pair(store, (PAPER,), Counting())
    assert route(store, q, None, {}, FakeWikidata({})) == {}
    assert press(q) == {"osservatore": 0.9}


def test_reattributed_and_own_evidence_keep_their_own_probabilities(store, q, uncovered):
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    pair(store, (PAPER,), Counting())
    routed(store, q, OWN, 0.6, "vallini")
    assert press(q) == {"vallini": 0.6, "osservatore": 0.9}
    routed(store, q, OTHER, 0.7, "osservatore")
    assert press(q) == {"vallini": 0.6, "osservatore": 0.9}


def test_a_changed_map_rebuilds_a_pending_dossier_and_an_unchanged_one_does_not(store, q, uncovered):
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    for stage in (1, 2, 3, 4, 5):
        q.set_outcome("Q9", stage, "pass")
    reconcile(store, q)
    assert sorted(q.row("Q9").outcomes) == [0, 1, 2, 3, 4, 5]
    pair(store, (PAPER,), Counting())
    reconcile(store, q)
    assert sorted(q.row("Q9").outcomes) == [0, 1, 2, 3]  # still exempt: not triaged again


@pytest.mark.parametrize("status", ["open", "approved", "created", "declined"])
def test_a_row_with_a_status_has_its_evidence_corrected_and_its_outcomes_left(store, q, uncovered, status):
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    for stage in (1, 2, 3, 4, 5):
        q.set_outcome("Q9", stage, "pass", {"n": stage})
    q.set_status("Q9", status, page="P")
    pair(store, (PAPER,), Counting())
    reconcile(store, q)
    row = q.row("Q9")
    assert press(q) == {"osservatore": 0.9} and (row.status, row.page) == (status, "P")
    assert {s: row.data[s] for s in (1, 2, 3, 4, 5)} == {s: {"n": s} for s in (1, 2, 3, 4, 5)}
    unsupport(store, BLOG)
    reconcile(store, q)
    row = q.row("Q9")
    assert row.data[0]["press"] == {} and sorted(row.outcomes) == [0, 1, 2, 3, 4, 5]


def unsupport(store, url, outcome="neutral"):
    """The article's mention is judged again: it gives no support."""
    store.add_mention(Mention(url, "Q9", "Ordet", "work", "film", 0.8, ["x"], stance=0.1, stance_outcome=outcome))


@pytest.mark.parametrize("outcome", ["neutral", "critical"])
def test_a_pending_row_without_support_loses_the_exemption(store, q, uncovered, outcome):
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    for stage in (1, 2, 3, 4):
        q.set_outcome("Q9", stage, "pass")
    unsupport(store, BLOG, outcome)
    reconcile(store, q)
    row = q.row("Q9")
    assert row.data[0]["press"] == {} and not common.press_exempt(row)
    assert sorted(row.outcomes) == [0, 1]  # triaged again, now held to the floor
    assert row.sources == ["press:vallini"]  # what found it: a row with no source would pass the floor
    assert film.triage(row, {"sitelink_floor": 15})[0] == "drop"


@pytest.mark.parametrize("outcome", ["bearing", "mixed", None])
def test_support_is_every_stance_but_neutral_and_critical(store, q, uncovered, outcome):
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    unsupport(store, BLOG, outcome)
    reconcile(store, q)
    assert press(q) == {"vallini": 0.8}  # the probability as judged again


def test_a_row_of_other_sources_keeps_them_when_the_press_falls_away(store, q, uncovered):
    q.add("Q9", "film", "facet", {}, {}, data={"count": 20})
    pair(store, (BLOG,), Counting())
    routed(store, q, BLOG, 0.9)
    unsupport(store, BLOG)
    reconcile(store, q)
    assert q.row("Q9").sources == ["facet"] and q.row("Q9").data[0] == {"count": 20, "press": {}}


def test_evidence_that_raises_the_row_to_the_exemption_triages_it_again(store, q, uncovered):
    routed(store, q, OTHER, 0.5, "osservatore")
    q.set_outcome("Q9", 1, "pass")
    q.set_outcome("Q9", 2, "drop", {"reason": "3 sitelinks, under the floor of 15"})
    mention(store, OTHER, 0.7, kind=None)
    reconcile(store, q)
    assert sorted(q.row("Q9").outcomes) == [0, 1]


def test_a_benchmark_row_is_not_triaged_again(store, q, uncovered):
    q.add("Q9", "film", "category", {}, {}, calibration=True, data={"count": 1})
    routed(store, q, OTHER, 0.9, "osservatore")
    for stage in (1, 2):
        q.set_outcome("Q9", stage, "pass")
    unsupport(store, OTHER)
    reconcile(store, q)
    assert q.row("Q9").outcomes[2] == "pass"


def test_rows_the_press_never_touched_are_left_alone(store, q):
    q.add("Q1", "film", "facet", {}, {}, data={"count": 3})
    before = list(q.db.iterdump())
    reconcile(store, q)
    assert list(q.db.iterdump()) == before


PAPER2 = "https://x.example/news/un-film-bis"


def arrive(store, judge, url, outlet, words=REVIEW):
    store.add_article(url, outlet, "it", published="2024-04-24")
    path = url.replace("https://x.example", "")
    routes = {ROBOTS: Resp(""), PAPER: paper_page(words), PAPER2: paper_page(words), FEED.replace("{path}", path): Resp(
        feed(entry(f"<div>{' '.join(words)}</div>", url=url)))}
    return run_filter(store, Pages(client(routes), OUTLETS), judge, CFG)


def group(store):
    return {u: store.article(u).same_as for u in (PAPER, PAPER2, BLOG, BLOG2) if store.article(u)}


@pytest.mark.parametrize("culture", [0.9, 0.1])
@pytest.mark.parametrize("order", [(PAPER, BLOG), (BLOG, PAPER)])
def test_a_second_newspaper_url_of_a_judged_article_is_not_judged(store, order, culture):
    judge = Counting(culture)
    pair(store, order, judge)
    assert arrive(store, judge, PAPER2, "osservatore") == {"skip (duplicate)": 1}
    assert judge.asked == [order[0]] and store.article(PAPER2).data["of"] == order[0]
    assert group(store) == {PAPER: None, PAPER2: PAPER, BLOG: PAPER}
    assert {a.url for a in store.copies(BLOG)} == {PAPER, PAPER2}


def test_a_second_newspaper_url_of_unjudged_copies_is_judged(store):
    pair(store, (PAPER, BLOG), Counting())
    store.set_article(PAPER, "linked", "skip", {"reason": "HTTP 404"})
    judge = Counting()
    assert arrive(store, judge, PAPER2, "osservatore") == {"keep": 1} and judge.asked == [PAPER2]
    assert group(store) == {PAPER: None, PAPER2: PAPER, BLOG: PAPER}


def two_groups(store, first, second):
    """Two newspaper URLs of one review judged apart (`first` and `second`: the culture scores), then a post of it."""
    arrive(store, Counting(first), PAPER, "osservatore")
    arrive(store, Counting(second), PAPER2, "osservatore")
    assert store.article(PAPER2).same_as is None
    judge = Counting()
    counts = arrive(store, judge, BLOG, "vallini")
    assert judge.asked == [] and counts == {"skip (duplicate)": 1}


@pytest.mark.parametrize("first,second,stands", [(0.9, 0.1, PAPER), (0.1, 0.9, PAPER2), (0.1, 0.1, PAPER)])
def test_a_copy_that_joins_two_judged_articles_puts_them_in_one_group_and_keep_wins(store, first, second, stands):
    two_groups(store, first, second)
    assert group(store) == {PAPER: None, PAPER2: PAPER, BLOG: PAPER}
    assert store.article(BLOG).data["of"] == stands
    assert [store.article(u).outcome for u in (PAPER, PAPER2)] == [
        "keep" if c > 0.5 else "drop" for c in (first, second)]  # neither verdict is rewritten


def test_two_groups_with_posts_are_joined_whole(store):
    """Each newspaper URL has its own post; a later copy of both ties all together."""
    arrive(store, Counting(), PAPER, "osservatore")
    arrive(store, Counting(), BLOG, "vallini")
    arrive(store, Counting(), PAPER2, "osservatore", words=text(3))
    arrive(store, Counting(), BLOG2, "vallini", words=text(3))
    assert group(store) == {PAPER: None, PAPER2: None, BLOG: PAPER, BLOG2: PAPER2}
    store.join(store.article(BLOG2), store.article(PAPER))
    assert group(store) == {PAPER: None, PAPER2: PAPER, BLOG: PAPER, BLOG2: PAPER}
    assert {store.article(u).evidence_outlet for u in group(store)} == {"osservatore"}


def test_joined_kept_articles_are_one_note_one_entry_and_one_outlet(store, q, uncovered):
    two_groups(store, 0.9, 0.9)
    mention(store, PAPER, 0.7, kind=None)
    mention(store, PAPER2, 0.9, kind=None)
    route(store, q, None, {}, FakeWikidata({"Q9": ent()}))
    assert press(q) == {"osservatore": 0.9}
    (note,) = press_notes(store, "Q9", NAMES)
    assert note["url"] == PAPER and {a["url"] for a in note["also"]} == {PAPER2, BLOG}
    for url in (PAPER, PAPER2):
        store.set_route(url, "Q9", "enrichment")
    page = enrichment(store, NAMES, None)
    assert page.count("\n** ") == 1 and all(f"[{u} " in page for u in (PAPER, PAPER2, BLOG))
    for url in (PAPER, PAPER2, BLOG):  # a done entry of any copy stays done for all
        done = enrichment(store, NAMES, page + "{{SV press done|%s}}" % url)
        assert "\n** " not in done.split("<!-- press:end -->")[0]


def test_the_notes_of_a_joined_group_follow_the_mention_that_supports(store):
    two_groups(store, 0.9, 0.9)
    store.add_mention(Mention(PAPER, "Q9", "Ordet", "work", "film", 0.8, ["no"], "dropped", 0.1, "neutral"))
    mention(store, PAPER2, passages=("sì",))
    (note,) = press_notes(store, "Q9", NAMES)
    assert note["text"] == "sì" and note["url"] == PAPER2


@pytest.mark.parametrize("order", [(PAPER, BLOG, BLOG2, PAPER2), (BLOG, BLOG2, PAPER, PAPER2),
                                   (PAPER2, PAPER, BLOG, BLOG2), (BLOG2, PAPER2, BLOG, PAPER)])
def test_whatever_the_order_the_copies_are_one_group(store, order):
    for url in order:
        arrive(store, Counting(), url, "osservatore" if "/news/" in url else "vallini")
    first = next(u for u in order if "/news/" in u)
    assert group(store) == {u: None if u == first else first for u in group(store)} and len(group(store)) == 4
