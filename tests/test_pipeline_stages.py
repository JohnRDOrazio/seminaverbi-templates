import pytest

from pipeline.queue import Queue
from pipeline.stages import covered, run_stage
from sync.wiki import PageInfo

LANGS = ["en", "it", "es", "fr", "de", "pt"]


class FakeRepo:
    def __init__(self, items=None, links=None):
        self.items = items or {}   # (site, title) -> qid
        self.links = links or {}   # qid -> {sv site: title}
        self.calls = []

    def items_for(self, site, titles):
        self.calls.append(site)
        return {t: self.items[(site, t)] for t in titles if (site, t) in self.items}

    def item_sitelinks(self, item):
        return 1, dict(self.links.get(item, {}))


class FakeWiki:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.asked = []

    def page_info(self, titles):
        self.asked.append(list(titles))
        return {t: (PageInfo(True, False, [], None) if t in self.existing else None) for t in titles}


def wikis(**existing):
    return {lang: FakeWiki(existing.get(lang, ())) for lang in LANGS}


@pytest.fixture
def q():
    return Queue(":memory:")


def row(q, sitelinks, labels=None, calibration=False):
    q.add("Q1", "film", "s", labels or {"en": "Film"}, sitelinks, calibration=calibration)
    return q.pending(1)[0]


def test_covered_on_all_six_drops(q):
    r = row(q, {"enwiki": "Film"})
    repo = FakeRepo({("enwiki", "Film"): "Q9"}, {"Q9": {l: "Film" for l in LANGS}})
    outcome, data = covered(r, repo, wikis())
    assert outcome == "drop" and data == {"have": LANGS}


def test_covered_on_some_is_a_translation(q):
    r = row(q, {"enwiki": "Film"})
    repo = FakeRepo({("enwiki", "Film"): "Q9"}, {"Q9": {"en": "Film", "fr": "Film"}})
    outcome, data = covered(r, repo, wikis())
    assert outcome == "translation"
    assert data == {"have": ["en", "fr"], "missing": ["it", "es", "de", "pt"]}


def test_found_through_itwiki_when_no_enwiki(q):
    r = row(q, {"itwiki": "Il film"}, labels={"it": "Il film"})
    repo = FakeRepo({("itwiki", "Il film"): "Q9"}, {"Q9": {"it": "Il film"}})
    outcome, data = covered(r, repo, wikis())
    assert outcome == "translation" and data["have"] == ["it"]
    assert repo.calls == ["itwiki"]  # only the sitelinks the row has are tried


def test_exact_title_page_without_item_counts_as_covered(q):
    r = row(q, {"enwiki": "Film (1999 film)"}, labels={"en": "Film", "it": "Il film"})
    ws = wikis(it={"Il film"}, en={"Film (1999 film)"})
    outcome, data = covered(r, FakeRepo(), ws)
    assert outcome == "translation"
    assert data["have"] == ["en", "it"] and data["missing"] == ["es", "fr", "de", "pt"]


def test_nothing_anywhere_passes(q):
    assert covered(row(q, {"enwiki": "Film"}), FakeRepo(), wikis()) == ("pass", {})


def test_calibration_rows_pass_without_lookups(q):
    r = row(q, {"enwiki": "Film"}, calibration=True)
    repo = FakeRepo()
    assert covered(r, repo, wikis()) == ("pass", {}) and repo.calls == []


def test_run_stage_counts_outcomes(q):
    q.add("Q1", "film", "s", {}, {}); q.add("Q2", "film", "s", {}, {})
    counts = run_stage(q, 1, lambda r: ("pass" if r.qid == "Q1" else "drop", {"x": 1}))
    assert counts == {"pass": 1, "drop": 1} and q.pending(1) == []


def test_run_stage_stops_on_exception_and_keeps_row_pending(q):
    for n in (1, 2, 3):
        q.add(f"Q{n}", "film", "s", {}, {})

    def fn(r):
        if r.qid == "Q2":
            raise RuntimeError("boom")
        return "pass", {}

    with pytest.raises(RuntimeError):
        run_stage(q, 1, fn)
    assert [r.qid for r in q.pending(1)] == ["Q2", "Q3"]
