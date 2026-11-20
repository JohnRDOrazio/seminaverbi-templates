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


# --- stage 5, adjudication ---

from pathlib import Path

from pipeline.stages import adjudicate
from sync import categories

DATA = categories.load(Path(__file__).resolve().parent.parent)
T1, T2, T3 = [n.key for n in categories.leaf_themes(DATA)[:3]]
EN = {n.key: n.names["en"] for n in categories.leaf_themes(DATA)}


class FakeJudge:
    def __init__(self, choice="direct", themes=None, pick=0):
        self.choice, self._themes, self.pick = choice, themes or {}, pick
        self.evidence_calls = []

    def connection(self, row, text):
        self.text = text
        return {"choice": self.choice, "probabilities": {}, "confidence": 0.9}

    def field(self, row, text):
        return {"choice": "some-field", "outcome": "confident"}

    def themes(self, row, text):
        return self._themes

    def evidence(self, row, key, paragraphs):
        self.evidence_calls.append((key, paragraphs))
        return self.pick


def note(lang="en", text="Some text.", heading="H", url="http://x/1"):
    return {"lang": lang, "url": url, "heading": heading, "text": text}


def make_row(q, **kw):
    q.add("Q1", "film", "s", {"en": "Film"}, {}, calibration=kw.pop("calibration", False))
    q.set_outcome("Q1", 4, "pass", kw)
    return q.pending(5)[0]


def test_none_connection_drops(q):
    r = make_row(q, notes=[note()], languages={"en": 1})
    outcome, result = adjudicate(r, FakeJudge("none", {T1: {"p": 0.9, "status": "applied"}}), DATA)
    assert outcome == "drop" and result["connection"]["choice"] == "none"


def test_none_connection_on_calibration_row_passes_with_result(q):
    r = make_row(q, notes=[], languages={}, calibration=True)
    outcome, result = adjudicate(r, FakeJudge("none"), DATA)
    assert outcome == "pass" and result["first_wiki"] == "en"


def test_themes_below_040_are_never_kept(q):
    r = make_row(q, notes=[note()], languages={"en": 1})
    judge = FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}, T2: {"p": 0.39, "status": "author"},
                              T3: {"p": 0.9, "status": "no"}})
    outcome, result = adjudicate(r, judge, DATA)
    assert outcome == "pass"
    assert [t["key"] for t in result["themes"]] == [T1]
    assert [k for k, _ in judge.evidence_calls] == [T1]


def test_excerpt_is_at_most_25_words(q):
    long = " ".join(f"w{i}" for i in range(40))
    r = make_row(q, notes=[note(text="short one\n\n" + long, url="http://x/2")], languages={"en": 1})
    _, result = adjudicate(r, FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}}, pick=1), DATA)
    ev = result["themes"][0]["evidence"]
    assert ev["url"] == "http://x/2"
    assert ev["excerpt"] == " ".join(f"w{i}" for i in range(25)) + "…"
    # an uncut paragraph gets no ellipsis
    _, result = adjudicate(r, FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}}, pick=0), DATA)
    assert result["themes"][0]["evidence"]["excerpt"] == "short one"


def test_outline_has_one_entry_per_kept_theme(q):
    r = make_row(q, notes=[note()], languages={"en": 1})
    judge = FakeJudge(themes={T2: {"p": 0.5, "status": "author"}, T1: {"p": 0.8, "status": "applied"}})
    _, result = adjudicate(r, judge, DATA)
    assert result["outline"] == ["Lead", "Story and the Gospel", f"Theme: {EN[T1]}", f"Theme: {EN[T2]}",
                                "Discernment", "See also", "References"]


def test_first_wiki_is_the_richest_language(q):
    r = make_row(q, notes=[note()], languages={"fr": 3, "en": 1})
    assert adjudicate(r, FakeJudge(), DATA)[1]["first_wiki"] == "fr"


def test_first_wiki_ties_break_en_it_es_fr_de_pt(q):
    r = make_row(q, notes=[note()], languages={"pt": 2, "de": 2, "it": 2})
    assert adjudicate(r, FakeJudge(), DATA)[1]["first_wiki"] == "it"


def test_dossier_text_joins_notes(q):
    r = make_row(q, notes=[note("en", "A", "Head"), note("it", "B", "Testa")], languages={"en": 1, "it": 1})
    j = FakeJudge()
    adjudicate(r, j, DATA)
    assert j.text == "[Head] (en)\nA\n\n[Testa] (it)\nB"


def test_wrapped_paragraph_reaches_evidence_whole(q):
    r = make_row(q, notes=[note(text="first line\nsecond line\n\nother para")], languages={"en": 1})
    j = FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}})
    adjudicate(r, j, DATA)
    assert j.evidence_calls[0][1] == ["first line\nsecond line", "other para"]
