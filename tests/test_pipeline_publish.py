import pytest

import re

from pipeline.publish import (_set_status_param, approved_report, count_votes, page_title, proposal_call, publish as publish_all,
                              ranking, replace_call, sync_votes)
from pipeline.queue import Queue
from sync.wiki import SyncError

PREFIX = "Semina Verbi:Article proposals/"
RANK = "Semina Verbi:Article proposals"


class FakeEn:
    def __init__(self, pages=None):
        self.pages = {t: (1, text) for t, text in (pages or {}).items()}
        self.edits = []
        self.fail = set()
        self.votes = {}
        self.parses = []

    def parse(self, title, text):
        self.parses.append((title, text))
        out = []
        for m in re.finditer(r"SVV(\d+):\{\{NUMBEROFVOTESPAGE:(.*?)\}\}:SVV", text):
            if m.group(2) in self.votes:
                out.append(f"<p>SVV{m.group(1)}:{self.votes[m.group(2)]}:SVV</p>")
        return {"text": "\n".join(out)}

    def revision(self, title):
        return self.pages.get(title)

    def edit(self, title, text, summary, tags=None, createonly=False, baserevid=None):
        if title in self.fail:
            raise SyncError("conflict")
        self.edits.append(dict(title=title, text=text, summary=summary, createonly=createonly,
                               baserevid=baserevid))
        rev = self.pages.get(title, (0, ""))[0] + 1
        self.pages[title] = (rev, text)
        return {"result": "Success"}


def result(**kw):
    base = {"connection": {"choice": "direct", "probabilities": {"direct": 0.834}, "confidence": 0.9},
            "field": {"choice": "cinema", "probabilities": {"cinema": 0.7}},
            "themes": [{"key": "mercy", "p": 0.6, "status": "applied",
                        "evidence": {"url": "http://x/a", "excerpt": "He forgave | them }} all"}}],
            "outline": ["Lead", "Story"], "first_wiki": "en"}
    base.update(kw)
    return base


@pytest.fixture
def q():
    return Queue(":memory:")


def add(q, qid="Q1", title="Film One", calibration=False, outcome="pass", domain="film"):
    q.add(qid, domain, "s", {"en": title, "it": "Film Uno"}, {"enwiki": title}, calibration=calibration)
    q.set_outcome(qid, 4, "pass", {"notes": [{"lang": "en", "url": "http://x/a", "heading": "", "text": "t"},
                                             {"lang": "it", "url": "http://x/a", "heading": "", "text": "t"},
                                             {"lang": "it", "url": "http://x/b", "heading": "", "text": "t"}],
                                   "languages": {"en": 1}})
    q.set_outcome(qid, 5, outcome, result())
    return next(r for r in q._rows("qid=?", (qid,)))


def test_call_has_one_parameter_per_line_and_escapes():
    q = Queue(":memory:"); r = add(q)
    call = proposal_call(r, r.data[5])
    lines = call.splitlines()
    assert lines[0] == "{{SV proposal" and lines[-1] == "}}"
    assert "|qid=Q1" in lines and "|connection_p=0.83" in lines and "|field=cinema" in lines
    assert "|sources=http://x/a http://x/b" in lines and "|outline=Lead ; Story" in lines
    assert "|theme1_evidence=He forgave {{!}} them &#125;&#125; all" in lines
    assert "|label_it=Film Uno" in lines and "|status=open" in lines


def test_text_below_the_box_survives_republish(q):
    r = add(q)
    old = "{{SV proposal\n|qid=Q1\n|status=open\n}}\n\n== Notes ==\nhuman text {{x}}\n\n\n"
    new = replace_call(old, proposal_call(r, r.data[5]))
    assert new.endswith("\n\n== Notes ==\nhuman text {{x}}\n\n\n")
    assert new.count("{{SV proposal") == 1 and "|qid=Q1" in new
    text_above = "intro <!-- {{SV proposal|qid=NO}} -->\n"
    assert replace_call(text_above + old, "{{SV proposal\n|qid=Q1\n}}").startswith(text_above + "{{SV proposal\n|qid=Q1\n}}")


def test_new_page_is_the_call_and_newline(q):
    r = add(q)
    call = proposal_call(r, r.data[5])
    assert replace_call(None, call) == call + "\n"


def test_declined_status_is_carried_over(q):
    r = add(q)
    old = "{{SV proposal\n|qid=Q1\n|status=declined\n|reason=off [[topic|x]] {{!}} bad\n}}\nbody\n"
    new = replace_call(old, proposal_call(r, r.data[5]))
    assert "|status=declined" in new and "|reason=off [[topic|x]] {{!}} bad" in new
    assert "|status=open" not in new and new.endswith("}}\nbody\n")


def test_title_is_sanitised_and_stable(q):
    q.add("Q1", "film", "s", {"en": "x"}, {"enwiki": "Star Wars: Episode I – The Phantom Menace"})
    r = q.pending(1)[0]
    assert page_title(r, q) == PREFIX + "Star Wars: Episode I – The Phantom Menace"
    q.add("Q2", "film", "s", {"en": "x"}, {"enwiki": ":A|B#[c]{d}<e>"})
    r2 = q.pending(1)[1]
    assert page_title(r2, q) == PREFIX + "ABcde"
    r2.sitelinks["enwiki"] = "Changed"
    assert page_title(r2, q) == PREFIX + "ABcde"
    fresh = [x for x in q.pending(1) if x.qid == "Q2"][0]
    assert fresh.page == PREFIX + "ABcde"


def test_title_uses_first_wiki_label_without_enwiki(q):
    q.add("Q1", "film", "s", {"en": "English", "it": "Titolo"}, {})
    q.set_outcome("Q1", 5, "pass", result(first_wiki="it"))
    r = q._rows("qid=?", ("Q1",))[0]
    assert page_title(r, q) == PREFIX + "Titolo"


def test_publish_creates_and_sets_status_and_skips_others(q):
    add(q, "Q1", "One"); add(q, "Q2", "Cal", calibration=True); add(q, "Q3", "Dropped", outcome="drop")
    add(q, "Q4", "Declined"); q.set_status("Q4", "declined", reason="no")
    en = FakeEn()
    n = publish_all(q, en, {})
    assert [e["title"] for e in en.edits] == [PREFIX + "One", RANK] and n == 2
    assert en.edits[0]["createonly"] is True and en.edits[0]["summary"] == "Article proposal (pipeline)"
    assert q.by_status("open")[0].qid == "Q1" and q.by_status("open")[0].page == PREFIX + "One"


def test_existing_page_is_saved_against_its_revision(q):
    add(q, "Q1", "One")
    en = FakeEn({PREFIX + "One": "{{SV proposal\n|qid=Q1\n}}\nmine\n"})
    publish_all(q, en, {})
    e = en.edits[0]
    assert e["baserevid"] == 1 and e["createonly"] is False and e["text"].endswith("}}\nmine\n")


def test_unchanged_page_is_not_edited(q):
    add(q, "Q1", "One")
    en = FakeEn()
    publish_all(q, en, {})
    en.edits.clear()
    assert publish_all(q, en, {}) == 0 and en.edits == []


def test_declined_on_the_page_is_read_back_into_the_queue(q):
    add(q, "Q1", "One")
    en = FakeEn({PREFIX + "One": "{{SV proposal\n|qid=Q1\n|status=declined\n|reason=nope\n}}\n"})
    publish_all(q, en, {})
    assert q.by_status("declined")[0].qid == "Q1"


def test_a_failed_edit_does_not_stop_the_others(q, capsys):
    add(q, "Q1", "One"); add(q, "Q2", "Two")
    en = FakeEn(); en.fail.add(PREFIX + "One")
    assert publish_all(q, en, {}) == 2   # Two and the ranking
    assert "One" in capsys.readouterr().err


def test_ranking_orders_by_votes(q):
    for qid, t, v in (("Q1", "Alpha", 1), ("Q2", "Beta", 5), ("Q3", "Gamma", None), ("Q4", "Aardvark", None)):
        add(q, qid, t); q.set_status(qid, "open", page=PREFIX + t)
        if v is not None:
            q.set_votes(qid, v)
    text = ranking(q)
    order = [text.index(t) for t in ("Beta", "Alpha", "Aardvark", "Gamma")]
    assert order == sorted(order)


def test_ranking_sections(q):
    add(q, "Q1", "A"); q.set_status("Q1", "approved", page=PREFIX + "A")
    add(q, "Q2", "B"); q.set_status("Q2", "created", page=PREFIX + "B")
    add(q, "Q3", "C"); q.set_status("Q3", "declined", page=PREFIX + "C")
    q.add("Q9", "film", "s", {"en": "Trans"}, {})
    q.set_outcome("Q9", 1, "translation", {"have": ["it"], "missing": ["en", "fr"]})
    text = ranking(q)
    heads = [text.index(h) for h in ("== Open", "== Approved", "== Created", "== Declined", "== Translation")]
    assert heads == sorted(heads)
    assert "Trans" in text and "it" in text and "fr" in text


def test_colliding_titles_get_the_qid(q):
    q.add("Q1", "film", "s", {}, {"enwiki": "A|B"}); q.add("Q2", "film", "s", {}, {"enwiki": "AB"})
    a, b = q.pending(1)
    assert page_title(a, q) == PREFIX + "AB" and page_title(b, q) == PREFIX + "AB (Q2)"


def test_page_of_another_qid_is_left_alone(q, capsys):
    add(q, "Q1", "One")
    other = "{{SV proposal\n|qid=Q9\n|status=declined\n|reason=x\n}}\n"
    en = FakeEn({PREFIX + "One": other})
    assert publish_all(q, en, {}) == 1 and [e["title"] for e in en.edits] == [RANK]
    assert "another QID" in capsys.readouterr().err and q.by_status("declined") == []


def test_values_cannot_open_comments_or_sign():
    q = Queue(":memory:"); r = add(q)
    res = result(); res["themes"][0]["evidence"]["excerpt"] = "a <!-- b <ref> ~~~~"
    call = proposal_call(r, res)
    assert "<" not in call and "~" not in call


@pytest.mark.parametrize("text", ["#REDIRECT [[Elsewhere]]\n", "{{SV proposal|qid=Q1\n", "plain\n"])
def test_page_without_a_recognisable_call_is_skipped(q, capsys, text):
    add(q, "Q1", "One")
    en = FakeEn({PREFIX + "One": text})
    publish_all(q, en, {})
    assert [e["title"] for e in en.edits] == [RANK] and "no {{SV proposal}}" in capsys.readouterr().err


def test_template_prefix_and_spacing_are_recognised(q):
    r = add(q)
    old = "{{ Template:sv_proposal |qid=Q1\n|status=open}}\nkeep\n"
    new = replace_call(old, proposal_call(r, r.data[5]))
    assert new.count("SV proposal") == 1 and new.endswith("}}\nkeep\n")


def test_queue_further_along_advances_the_page(q):
    add(q, "Q1", "One"); q.set_status("Q1", "approved", page=PREFIX + "One")
    en = FakeEn({PREFIX + "One": "{{SV proposal\n|qid=Q1\n|status=open\n}}\n"})
    publish_all(q, en, {})
    assert "|status=approved" in en.edits[0]["text"]


def test_page_further_along_than_queue_is_kept(q):
    add(q, "Q1", "One"); q.set_status("Q1", "approved", page=PREFIX + "One")
    en = FakeEn({PREFIX + "One": "{{SV proposal\n|qid=Q1\n|status=created\n|article=Foo\n}}\n"})
    publish_all(q, en, {})
    assert "|status=created" in en.edits[0]["text"] and "|article=Foo" in en.edits[0]["text"]
    assert q.by_status("created")[0].qid == "Q1"


def test_trailing_whitespace_does_not_trigger_an_edit(q):
    en = FakeEn({RANK: ranking(q).rstrip() + "\n\n\n"})
    assert publish_all(q, en, {}) == 0


CFG = {"vote_threshold": 3}


def open_page(q, en, qid, title, status="open", extra=""):
    add(q, qid, title)
    en.pages[PREFIX + title] = (1, f"{{{{SV proposal\n|qid={qid}\n|status={status}\n{extra}}}}}\n\nhuman text\n")
    q.set_page(qid, PREFIX + title)
    q.set_status(qid, "open", page=PREFIX + title)


def test_threshold_promotes_to_approved(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    en.votes["Semina Verbi:Article proposals/One"] = 3
    out = sync_votes(q, en, CFG)
    assert out == {"approved": ["Q1"], "declined": []}
    assert q.by_status("approved")[0].votes == 3
    edit = [e for e in en.edits if e["title"] == PREFIX + "One"][0]
    assert edit["baserevid"] == 1
    assert edit["text"] == "{{SV proposal\n|qid=Q1\n|status=approved\n}}\n\nhuman text\n"
    assert any(e["title"] == RANK for e in en.edits)


def test_below_threshold_stays_open(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    en.votes[PREFIX + "One"] = 2
    assert sync_votes(q, en, CFG) == {"approved": [], "declined": []}
    row = q.by_status("open")[0]
    assert row.votes == 2
    assert not [e for e in en.edits if e["title"] == PREFIX + "One"]


def test_hand_declined_is_read_back_and_never_reproposed(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One", status="declined", extra="|reason=off topic\n")
    en.votes[PREFIX + "One"] = 9
    assert sync_votes(q, en, CFG) == {"approved": [], "declined": ["Q1"]}
    assert q.by_status("declined")[0].qid == "Q1"
    before = en.pages[PREFIX + "One"]
    publish_all(q, en, {})
    assert en.pages[PREFIX + "One"] == before
    assert "declined" in en.pages[PREFIX + "One"][1]


def test_votes_are_counted_in_one_parse_per_50_pages(q):
    en = FakeEn()
    titles = [f"{PREFIX}T{i}" for i in range(120)]
    en.votes = {t: i for i, t in enumerate(titles) if i != 7}
    counts = count_votes(en, titles)
    assert len(en.parses) == 3
    assert counts[titles[5]] == 5 and counts[titles[119]] == 119 and counts[titles[7]] == 0
    assert all(t == RANK for t, _ in en.parses)


def test_missing_count_warns(q, capsys):
    en = FakeEn()
    assert count_votes(en, [PREFIX + "X"]) == {PREFIX + "X": 0}
    assert "X" in capsys.readouterr().err


def test_page_of_another_qid_or_without_call_is_not_approved(q, capsys):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    open_page(q, en, "Q2", "Two")
    en.pages[PREFIX + "One"] = (1, "{{SV proposal\n|qid=Q9\n|status=open\n}}\n")
    en.pages[PREFIX + "Two"] = (1, "just text")
    en.votes = {PREFIX + "One": 5, PREFIX + "Two": 5}
    assert sync_votes(q, en, CFG)["approved"] == []
    assert not q.by_status("approved")
    assert capsys.readouterr().err.count("left alone") == 2


def test_only_open_rows_are_synced(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    q.set_status("Q1", "created")
    en.votes[PREFIX + "One"] = 5
    sync_votes(q, en, CFG)
    assert not [p for p in en.parses if "One" in p[1]]


def test_approved_report(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    q.set_status("Q1", "approved")
    report = approved_report(q)
    for needle in ("One (Q1)", "Q1", "en", "direct", "cinema", "mercy", "He forgave | them }} all",
                   "Lead", "http://x/a", "http://x/b"):
        assert needle in report
    q.set_status("Q1", "open")
    assert approved_report(q) == ""


def test_page_declined_between_read_and_write_is_not_overwritten(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    en.votes[PREFIX + "One"] = 5
    real = en.parse

    def parse(title, text):
        en.pages[PREFIX + "One"] = (2, "{{SV proposal\n|qid=Q1\n|status=declined\n}}\n")
        return real(title, text)
    en.parse = parse
    assert sync_votes(q, en, CFG)["approved"] == []
    assert "declined" in en.pages[PREFIX + "One"][1]
    assert not q.by_status("approved")


def test_call_vanishing_between_reads_does_not_crash(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One")
    en.votes[PREFIX + "One"] = 5
    real = en.parse

    def parse(title, text):
        en.pages[PREFIX + "One"] = (2, "gone")
        return real(title, text)
    en.parse = parse
    assert sync_votes(q, en, CFG)["approved"] == []
    assert en.pages[PREFIX + "One"] == (2, "gone")


def test_created_page_is_read_back_not_downgraded(q):
    en = FakeEn()
    open_page(q, en, "Q1", "One", status="created", extra="|article=Foo\n")
    en.votes[PREFIX + "One"] = 5
    before = en.pages[PREFIX + "One"]
    assert sync_votes(q, en, CFG)["approved"] == []
    assert en.pages[PREFIX + "One"] == before
    assert q.by_status("created")[0].qid == "Q1"


@pytest.mark.parametrize("call", [
    "{{SV proposal\n|qid=Q1\n}}",
    "{{SV proposal\n|qid=Q1\n|status=open\n|reason=a\n|article=B\n}}",
    "{{SV proposal\n|qid=Q1\n|t={{x|status=no}}\n|l=[[A|status=b]]\n|status = open \n|z=1\n}}",
])
def test_set_status_param_changes_only_status(call):
    new = _set_status_param(call, "approved")
    assert dict(__import__("pipeline.publish", fromlist=["_split"])._split(new))["status"] == "approved"
    stripped_old = re.sub(r"\|status = open \n|\|status=open\n", "", call)
    stripped_new = re.sub(r"\|status ?= ?approved ?\n", "", new)
    assert stripped_new == stripped_old


# ---- final review fixes ----

def test_calibration_rows_with_missing_wikis_are_translation_suggestions(q):
    q.add("Q7", "film", "calibration", {"en": "Ordet"}, {}, calibration=True)
    q.set_outcome("Q7", 1, "pass", {"have": ["en", "it"], "missing": ["es", "fr", "de", "pt"]})
    q.add("Q8", "film", "calibration", {"en": "Everywhere"}, {}, calibration=True)
    q.set_outcome("Q8", 1, "pass", {"have": list(("en", "it", "es", "fr", "de", "pt")), "missing": []})
    text = ranking(q)
    suggestions = text[text.index("== Translation suggestions =="):]
    assert "Ordet" in suggestions and "missing: es, fr, de, pt" in suggestions
    assert "Everywhere" not in suggestions


class FlakyEn(FakeEn):
    def __init__(self, broken, **kw):
        super().__init__(**kw)
        self.broken = broken

    def revision(self, title):
        if title == self.broken:
            raise SyncError("revision read failed")
        return super().revision(title)


def test_a_failed_page_read_skips_only_that_page(q, capsys):
    add(q, "Q1", "One"); add(q, "Q2", "Two")
    en = FlakyEn(PREFIX + "One")
    assert publish_all(q, en, {}) == 2
    assert [e["title"] for e in en.edits] == [PREFIX + "Two", RANK]
    assert "revision read failed" in capsys.readouterr().err


def test_publish_selects_the_domain(q):
    add(q, "Q1", "One"); add(q, "Q2", "Song", domain="music")
    en = FakeEn()
    publish_all(q, en, {}, domain="music")
    assert [e["title"] for e in en.edits] == [PREFIX + "Song", RANK]


def test_failed_create_leaves_the_row_unopened(q, capsys):
    add(q, "Q1", "One")
    en = FakeEn(); en.fail.add(PREFIX + "One")
    publish_all(q, en, {})
    assert q.by_status("open") == []
    assert q._rows("qid=?", ("Q1",))[0].status is None


def test_status_with_a_comment_or_capitals_is_recognised(q):
    r = add(q)
    old = "{{SV proposal\n|qid=Q1\n|status=declined <!-- by hand -->\n|reason=no\n}}\n"
    new = replace_call(old, proposal_call(r, r.data[5]))
    assert "|status=open" not in new and "declined" in new
    en = FakeEn({PREFIX + "Film One": "{{SV proposal\n|qid=Q1\n|status=Declined <!-- x -->\n|reason=no\n}}\n"})
    publish_all(q, en, {})
    assert q.by_status("declined")[0].qid == "Q1"


def test_approved_report_has_the_dossier_notes(q):
    q.add("Q1", "film", "s", {"en": "One"}, {"enwiki": "One"})
    long_text = "a" * 400 + "TAIL"
    q.set_outcome("Q1", 4, "pass", {"notes": [{"lang": "it", "url": "http://x/it", "heading": "Trama",
                                               "text": long_text}], "languages": {"it": 1}})
    q.set_outcome("Q1", 5, "pass", result())
    q.set_status("Q1", "approved", page=PREFIX + "One")
    report = approved_report(q)
    assert "Trama" in report and "(it)" in report and "http://x/it" in report
    assert "a" * 400 in report and "TAIL" not in report
