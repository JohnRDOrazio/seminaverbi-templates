import pytest

from pipeline.publish import page_title, proposal_call, publish as publish_all, ranking, replace_call
from pipeline.queue import Queue
from sync.wiki import SyncError

PREFIX = "Semina Verbi:Article proposals/"
RANK = "Semina Verbi:Article proposals"


class FakeEn:
    def __init__(self, pages=None):
        self.pages = {t: (1, text) for t, text in (pages or {}).items()}
        self.edits = []
        self.fail = set()

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
