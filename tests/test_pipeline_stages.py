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
    """`existing`: titles, or {title: (item, redirect)}."""
    def __init__(self, existing=()):
        self.existing = existing if isinstance(existing, dict) else {t: (None, False) for t in existing}
        self.asked = []

    def page_info(self, titles):
        self.asked.append(list(titles))
        return {t: (PageInfo(True, self.existing[t][1], [], self.existing[t][0]) if t in self.existing else None)
                for t in titles}


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


def test_exact_title_page_of_the_same_item_counts_as_covered(q):
    r = row(q, {"enwiki": "Film (1999 film)"}, labels={"en": "Film", "it": "Il film"})
    repo = FakeRepo({("enwiki", "Film (1999 film)"): "Q9"}, {"Q9": {}})
    ws = wikis(it={"Il film": ("Q9", False)}, en={"Film (1999 film)": ("Q9", False)})
    outcome, data = covered(r, repo, ws)
    assert outcome == "translation"
    assert data["have"] == ["en", "it"] and data["missing"] == ["es", "fr", "de", "pt"]


def test_title_match_on_another_subject_is_a_possible_duplicate(q):
    r = row(q, {"enwiki": "Jesus (1979 film)"}, labels={"en": "Jesus", "it": "Gesù"})
    repo = FakeRepo({("enwiki", "Jesus (1979 film)"): "Q9"}, {"Q9": {}})
    ws = wikis(en={"Jesus": ("Q1", False)}, it={"Gesù": (None, False)})
    outcome, data = covered(r, repo, ws)
    assert outcome == "pass"
    assert data == {"possible_duplicate": ["en:Jesus", "it:Gesù"]}


def test_redirect_title_is_ignored(q):
    r = row(q, {"enwiki": "Film"}, labels={"en": "Film"})
    repo = FakeRepo({("enwiki", "Film"): "Q9"}, {"Q9": {}})
    assert covered(r, repo, wikis(en={"Film": ("Q9", True)})) == ("pass", {})


def test_nothing_anywhere_passes(q):
    assert covered(row(q, {"enwiki": "Film"}), FakeRepo(), wikis()) == ("pass", {})


def test_calibration_rows_pass_with_their_coverage(q):
    r = row(q, {"enwiki": "Film"}, calibration=True)
    repo = FakeRepo({("enwiki", "Film"): "Q9"}, {"Q9": {"en": "Film", "it": "Film"}})
    outcome, data = covered(r, repo, wikis())
    assert outcome == "pass"
    assert data == {"have": ["en", "it"], "missing": ["es", "fr", "de", "pt"]}
    full = FakeRepo({("enwiki", "Film"): "Q9"}, {"Q9": {l: "Film" for l in LANGS}})
    assert covered(r, full, wikis()) == ("pass", {"have": LANGS, "missing": []})


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
        self.theme_texts = getattr(self, "theme_texts", []) + [text]
        return self._themes(text) if callable(self._themes) else self._themes

    def evidence(self, row, key, paragraphs):
        self.evidence_calls.append((key, paragraphs))
        return self.pick


def note(lang="en", text="Some text.", heading="H", url="http://x/1"):
    return {"lang": lang, "url": url, "heading": heading, "text": text}


def make_row(q, **kw):
    q.add("Q1", "film", "s", {"en": "Film"}, {}, calibration=kw.pop("calibration", False))
    q.set_outcome("Q1", 4, "pass", kw)
    return q.pending(5)[0]


def test_a_none_connection_with_a_theme_passes(q):
    r = make_row(q, notes=[note()], languages={"en": 1})
    outcome, result = adjudicate(r, FakeJudge("none", {T1: {"p": 0.9, "status": "applied"}}), DATA)
    assert outcome == "pass" and result["connection"]["choice"] == "none"


def test_no_theme_at_050_drops_whatever_the_connection(q):
    r = make_row(q, notes=[note()], languages={"en": 1})
    outcome, result = adjudicate(r, FakeJudge("direct", {T1: {"p": 0.49, "status": "author"}}), DATA)
    assert outcome == "drop" and result["themes"] == []
    assert result["theme_scores"] == {T1: 0.49}


def test_none_connection_on_calibration_row_passes_with_result(q):
    r = make_row(q, notes=[], languages={}, calibration=True)
    outcome, result = adjudicate(r, FakeJudge("none"), DATA)
    assert outcome == "pass" and result["first_wiki"] == "en"


def test_themes_below_050_are_never_kept(q):
    r = make_row(q, notes=[note()], languages={"en": 1})
    judge = FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}, T2: {"p": 0.49, "status": "author"},
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


def test_dossier_text_sent_to_jev_is_capped(q):
    big = "\n\n".join("x" * 9000 for _ in range(4))
    r = make_row(q, notes=[note(text=big), note(text="tail paragraph")], languages={"en": 2})
    j = FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}})
    adjudicate(r, j, DATA)
    assert len(j.text) <= 20000 and j.text.startswith("[H] (en)\nxxx")
    paragraphs = j.evidence_calls[0][1]
    assert sum(len(p) for p in paragraphs) <= 20000 and paragraphs[0] == "x" * 9000


def test_at_most_eight_themes_are_kept_best_first(q):
    keys = list(EN)[:10]
    r = make_row(q, notes=[note()], languages={"en": 1})
    judge = FakeJudge(themes={k: {"p": 0.9 - i / 100, "status": "applied"} for i, k in enumerate(keys)})
    _, result = adjudicate(r, judge, DATA)
    assert [t["key"] for t in result["themes"]] == keys[:8]


def big_notes(n, size=15000):
    return [note(text=f"part{i} " + "y" * size, url=f"http://x/{i}") for i in range(n)]


def test_a_long_dossier_is_read_in_parts_and_each_theme_keeps_its_best(q):
    r = make_row(q, notes=big_notes(3), languages={"en": 3})

    def themes(text):
        if "part1" in text:
            return {T1: {"p": 0.3, "status": "no"}, T2: {"p": 0.9, "status": "applied"}}
        return {T1: {"p": 0.7, "status": "author"}, T2: {"p": 0.2, "status": "no"}}
    judge = FakeJudge(themes=themes)
    outcome, result = adjudicate(r, judge, DATA)
    assert len(judge.theme_texts) == 3 and all(len(t) <= 20000 for t in judge.theme_texts)
    assert outcome == "pass"
    assert [(t["key"], t["p"], t["status"]) for t in result["themes"]] == [(T2, 0.9, "applied"), (T1, 0.7, "author")]
    assert result["themes"][0]["evidence"]["url"] == "http://x/1"  # from the part that scored it
    assert "part0" in judge.text and "part1" not in judge.text  # connection and field read the first part


def test_at_most_five_parts_are_read(q):
    r = make_row(q, notes=big_notes(8), languages={"en": 8})
    judge = FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}})
    adjudicate(r, judge, DATA)
    assert len(judge.theme_texts) == 5


def test_a_note_longer_than_a_part_is_split_at_paragraphs(q):
    text = "\n\n".join(f"para{i} " + "z" * 9000 for i in range(5))
    r = make_row(q, notes=[note(text=text)], languages={"en": 1})
    judge = FakeJudge(themes={T1: {"p": 0.8, "status": "applied"}})
    adjudicate(r, judge, DATA)
    assert len(judge.theme_texts) == 3
    assert all(f"para{i}" in "".join(judge.theme_texts) for i in range(5))


@pytest.mark.parametrize("choice,p,outcome", [("direct", 0.8, "pass"), ("direct", 0.79, "drop"),
                                              ("seeds", 0.95, "drop")])
def test_a_confident_direct_connection_passes_without_a_theme(q, choice, p, outcome):
    r = make_row(q, notes=[note()], languages={"en": 1})
    judge = FakeJudge(choice, {T1: {"p": 0.3, "status": "no"}})
    judge.connection = lambda row, text: {"choice": choice, "probabilities": {choice: p}, "confidence": 0.9}
    assert adjudicate(r, judge, DATA)[0] == outcome
