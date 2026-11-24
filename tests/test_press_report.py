"""What each outlet has given: figures for the author's review, never a removal."""
import sqlite3

import pytest

from pipeline import __main__ as cli
from pipeline.press.report import figures, lines, marked
from pipeline.press.store import Mention, PressStore
from pipeline.queue import Queue
from tests.test_press_cli import press  # noqa: F401 (fixture)
from tests.test_press_refusals import OK, day, refuse_days, visit  # noqa: F401 (fixture)

OUTLETS = [{"id": "o", "entries": ["f"]}, {"id": "paper", "entries": ["f"]},
           {"id": "blog", "entries": ["f"], "republishes": "paper"}, {"id": "none", "entries": []}]


class Book:
    def __init__(self):
        self.queue = Queue(":memory:")
        self.store = PressStore.of(self.queue)
        self.n = 0

    def article(self, outlet, outcome="keep", data=None):
        self.n += 1
        url = f"https://{outlet}.example/{self.n}"
        self.store.add_article(url, outlet, "it", "T")
        self.store.set_article(url, "linked" if outcome == "keep" else "filtered", outcome, data or {})
        return url

    def mention(self, url, qid, route="candidate"):
        self.store.add_mention(Mention(url, qid, "L", "work", "film", 0.9, [], route))

    def proposal(self, qid, status=None, outlet="o"):
        self.queue.add(qid, "film", f"press:{outlet}", {}, {})
        if status:
            self.queue.set_status(qid, status, page="Semina Verbi:Proposals/" + qid)

    def of(self, outlet="o"):
        return figures(self.store, self.queue, OUTLETS)[outlet]

    def line(self, outlet="o"):
        return next(x for x in lines(self.store, self.queue, OUTLETS) if x.startswith(f"  {outlet}:"))


@pytest.fixture
def book(day):  # noqa: F811
    return Book()


def test_first_read_and_days_read_come_from_successful_reads(book, day):  # noqa: F811
    for n in (3, 3, 5, 9):
        day.set(n)
        visit(book.store, OK)
    refuse_days(book.store, day, [10])
    f = book.of()
    assert (f.first_read, f.days_read) == ("2026-12-03", 3)
    assert book.of("paper").first_read is None


def test_articles_read_and_kept(book):
    for outcome, data in (("keep", {}), ("drop", {"culture": 0.1}), ("skip", {"reason": "duplicate"}),
                          ("skip", {"reason": "not an article"}), ("skip", {"reason": "HTTP 404"}),
                          ("skip", {"reason": "robots.txt"})):
        book.article("o", outcome, data)
    book.store.add_article("https://o.example/found", "o", "it")
    gone = book.article("o")  # kept by filter, gone when link fetched it
    book.store.set_article(gone, "linked", "skip", {"reason": "HTTP 404", "culture": 0.9})
    assert (book.of().read, book.of().kept) == (4, 1)


def test_candidates_proposals_and_enrichment_entries(book):
    url = book.article("o")
    for qid, status in (("Q1", None), ("Q2", "open"), ("Q3", "approved"), ("Q4", "created"), ("Q5", "declined")):
        book.mention(url, qid)
        book.proposal(qid, status)
    for qid, route in (("Q6", "enrichment"), ("Q7", "dropped"), ("Q8", "waiting")):
        book.mention(url, qid, route)
    f = book.of()
    assert (f.candidates, f.proposals, f.approved, f.created, f.enrichment) == (5, 3, 1, 1, 1)
    assert "candidates 5, proposals published 3 (approved 1, created 1); enrichment entries 1" in book.line()


def test_two_articles_on_one_work_are_one_candidate(book):
    for _ in range(2):
        book.mention(book.article("o"), "Q1")
    book.proposal("Q1", "open")
    assert (book.of().candidates, book.of().proposals) == (1, 1)


def test_a_page_is_needed_and_calibration_rows_do_not_count(book):
    url = book.article("o")
    book.mention(url, "Q1"), book.mention(url, "Q2")
    book.queue.add("Q1", "film", "press:o", {}, {})
    book.queue.set_status("Q1", "open")
    book.queue.add("Q2", "film", "press:o", {}, {}, calibration=True)
    book.queue.set_status("Q2", "open", page="P")
    assert (book.of().candidates, book.of().proposals) == (1, 0)


def test_a_retired_part_is_no_proposal_while_open(book):
    url = book.article("o")
    book.mention(url, "Q1")
    book.proposal("Q1", "open")
    book.queue.set_outcome("Q1", 0, "pass", {"retired": True})
    assert book.of().proposals == 0


def test_a_post_that_counts_as_the_newspapers_is_credited_where_it_was_read(book):
    post = book.article("blog")
    book.store.set_credit(post, "V, Paper", "2026-01-01", "paper")
    book.mention(post, "Q1")
    book.proposal("Q1", "open", "paper")  # the queue's source is the newspaper
    assert book.of("blog").proposals == 1 and book.of("paper").proposals == 0
    assert "; 1 of the candidates counted as another outlet's evidence (paper)" in book.line("blog")


@pytest.mark.parametrize("cited", ["blog", "paper"])
def test_the_same_article_in_two_outlets_is_credited_to_both(book, cited):
    urls = {"blog": book.article("blog"), "paper": book.article("paper", "skip", {"reason": "duplicate"})}
    book.store.join(book.store.article(urls["blog"]), book.store.article(urls["paper"]))
    book.mention(urls[cited], "Q1")
    book.proposal("Q1", "open", "paper")
    book.mention(urls[cited], "Q2", "enrichment")
    for outlet in ("blog", "paper"):
        assert (book.of(outlet).proposals, book.of(outlet).enrichment) == (1, 1)


# ---- the marks ----

def read_on(book, day, n):  # noqa: F811
    day.set(n)
    visit(book.store, OK)


@pytest.mark.parametrize("today,mark", [
    ("2026-12-30", None), ("2026-12-31", "no useful proposals for 30 days: review"),
    ("2027-01-29", "no useful proposals for 59 days: review"),
    ("2027-01-30", "no useful proposals for 60 days: consider dropping")])
def test_marks_by_days_since_the_first_read(book, day, today, mark):  # noqa: F811
    read_on(book, day, 1)
    day.day = today
    assert book.of().mark == mark
    assert (mark in book.line()) if mark else ("no useful" not in book.line())


@pytest.mark.parametrize("status,is_marked", [("open", False), ("approved", False), ("created", False),
                                              ("declined", True), (None, True)])
def test_only_a_published_proposal_not_declined_is_useful(book, day, status, is_marked):  # noqa: F811
    read_on(book, day, 1)
    book.mention(book.article("o"), "Q1")
    book.proposal("Q1", status)
    day.day = "2027-03-01"
    assert bool(book.of().mark) is is_marked


def test_enrichment_entries_do_not_make_it_useful_but_are_shown_with_the_mark(book, day):  # noqa: F811
    read_on(book, day, 1)
    book.mention(book.article("o"), "Q1", "enrichment")
    day.day = "2027-03-01"
    assert "no useful proposals for 90 days: consider dropping (enrichment entries: 1)" in book.line()
    assert marked(book.store, book.queue, OUTLETS) == (
        "press review: o (no useful proposals for 90 days: consider dropping; enrichment entries: 1)")


def test_outlets_never_read_removed_or_no_longer_configured(book, day):  # noqa: F811
    read_on(book, day, 1)
    refuse_days(book.store, day, range(2, 9))
    day.day = "2027-03-01"
    assert "never read (no entries configured)" in book.line("none")
    assert "never read" in book.line("paper") and "no entries" not in book.line("paper")
    assert "removed on 2026-12-08" in book.line() and "no useful" not in book.line()
    assert marked(book.store, book.queue, OUTLETS) == "press review: none"
    book.article("old")
    assert "not in the configuration" in book.line("old")


def test_nothing_is_dropped(book, day):  # noqa: F811
    read_on(book, day, 1)
    day.day = "2027-06-01"
    lines(book.store, book.queue, OUTLETS), marked(book.store, book.queue, OUTLETS)
    assert book.store.removed() == {} and visit(book.store, OK)[-1].endswith("/a")


# ---- stores made before ----

def test_an_old_store_takes_its_dates_from_its_articles(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("CREATE TABLE press_articles (url TEXT PRIMARY KEY, outlet TEXT, lang TEXT, title TEXT, published TEXT, "
               "found_at REAL, stage TEXT DEFAULT 'found', outcome TEXT, data_json TEXT)")
    for i, at in enumerate((1790000000, 1790000100, 1790200000)):  # 2026-09-21 (twice) and 2026-09-23, UTC
        db.execute("INSERT INTO press_articles (url, outlet, found_at) VALUES (?, 'o', ?)", (f"u{i}", at))
    db.execute("CREATE TABLE press_outlets (outlet TEXT PRIMARY KEY, ok_day TEXT, reinstated TEXT, removed TEXT, "
               "since TEXT, reason TEXT)")
    db.execute("INSERT INTO press_outlets (outlet, ok_day) VALUES ('p', '2026-10-01')")
    db.commit()
    for _ in range(2):  # the migration is guarded
        store = PressStore(sqlite3.connect(tmp_path / "old.sqlite"))
        assert store.reads() == {"o": ("2026-09-21", 2), "p": ("2026-10-01", 1)}


def test_the_dates_are_not_taken_from_articles_again_later(day):  # noqa: F811
    db = sqlite3.connect(":memory:")
    store = PressStore(db)
    store.add_article("u", "o", "it")  # e.g. a store filled by hand: no read recorded
    assert PressStore(db).reads() == {}


# ---- the command line ----

def test_press_outlets_shows_the_figures(press, day, capsys):  # noqa: F811
    db, _ = press
    assert cli.main(["press", "run", "--db", str(db)]) == 0
    assert capsys.readouterr().out.rstrip().endswith("press review: none")
    assert cli.main(["press", "outlets", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "  sir: never read; articles read 3, kept 2; candidates 1, proposals published 0" in out
    assert "press removed: none" in out


def test_a_run_names_the_marked_outlets_last_and_a_dry_run_does_not(press, day, capsys):  # noqa: F811
    db, _ = press
    store = PressStore.of(Queue(db))
    store.succeeded("sir")
    day.day = "2027-02-01"
    assert cli.main(["press", "run", "--db", str(db)]) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == (
        "press review: sir (no useful proposals for 62 days: consider dropping; enrichment entries: 1)")
    assert cli.main(["press", "run", "--dry-run", "--db", str(db)]) == 0
    assert "press review" not in capsys.readouterr().out
