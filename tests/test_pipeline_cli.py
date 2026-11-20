import pytest

from pipeline import __main__ as cli
from pipeline import calibration, stages
from pipeline.domains import film
from pipeline.queue import Queue

SV_FIELD, SV_THEME = "P10768", "P10769"


class FakeJudge:
    def plausible(self, row, synopsis):
        return 0.9


class FakeWikipedia:
    def __init__(self, lang, project="wikipedia"):
        self.lang = lang

    def lead(self, title):
        return f"{title} lead"


class FakeEditor:
    pass


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """The command line with every network-facing service replaced by a fake."""
    calls = {"publish": 0}

    def candidates(queue, sparql, wikipedia, cfg, wikidata=None):
        for i in range(5):
            queue.add(f"Q{i}", "film", "vatican", {"en": f"Film {i}"}, {"enwiki": f"Film {i}"},
                      data={"instance_of": [], "count": 30})
        return 5

    monkeypatch.setattr(film, "candidates", candidates)
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("pass", {}))
    monkeypatch.setattr(film, "dossier_stage", lambda row, cfg, wiki_for, wikidata: ("pass", {"notes": [], "languages": {}}))
    monkeypatch.setattr(stages, "adjudicate", lambda row, judge, data: ("pass", {"connection": {"choice": "direct"}}))
    monkeypatch.setattr(cli, "load_config", lambda: {"max_new_per_run": 2, "vote_threshold": 3, "quick_drop": 0.4,
                                                     "sitelink_floor": 15})
    monkeypatch.setattr(cli, "_sparql", lambda: None)
    monkeypatch.setattr(cli, "_wikidata", lambda: None)
    monkeypatch.setattr(cli, "_wikipedia", lambda lang, project="wikipedia": FakeWikipedia(lang, project))
    monkeypatch.setattr(cli, "_wikis", lambda: {lang: object() for lang in cli.LANGS})
    monkeypatch.setattr(cli, "_tree", lambda: {})
    monkeypatch.setattr(cli, "_judge", lambda queue: FakeJudge())
    monkeypatch.setattr(cli, "_editor", lambda: FakeEditor())

    def publish(queue, en, cfg):
        calls["publish"] += 1
        return 0

    monkeypatch.setattr(cli, "publish", publish)
    return tmp_path / "data" / "pipeline.sqlite", calls


def count(path, stage):
    q = Queue(path)
    return q.db.execute("SELECT COUNT(*) FROM outcomes WHERE stage=?", (stage,)).fetchone()[0]


def test_run_caps_new_rows_entering_stage_3(wired):
    path, _ = wired
    assert cli.main(["run", "--db", str(path)]) == 0
    assert count(path, 2) == 5
    assert count(path, 3) == 2
    assert count(path, 5) == 2
    cli.main(["run", "--db", str(path)])  # the next run takes the next two
    assert count(path, 3) == 4


def test_run_does_not_publish_without_flag(wired):
    path, calls = wired
    cli.main(["run", "--db", str(path)])
    assert calls["publish"] == 0
    cli.main(["run", "--db", str(path), "--publish"])
    assert calls["publish"] == 1


def test_queue_creates_missing_parent_directory(tmp_path):
    path = tmp_path / "a" / "b" / "q.sqlite"
    Queue(path)
    assert path.exists()


def test_redo_zero_is_refused(wired, capsys):
    path, _ = wired
    cli.main(["candidates", "--db", str(path)])
    with pytest.raises(SystemExit):
        cli.main(["redo", "0", "--db", str(path)])
    assert count(path, 0) == 5
    assert cli.main(["redo", "3", "--qid", "Q1", "--db", str(path)]) == 0


def test_writing_commands_need_credentials(monkeypatch):
    monkeypatch.delenv("SV_EDITOR_USER", raising=False)
    monkeypatch.delenv("SV_EDITOR_PASSWORD", raising=False)
    with pytest.raises(cli.CliError, match="SV_EDITOR_USER"):
        cli._login_editor()


class FakeWiki:
    """Stands for the local Wikibase: item_for/items_for and the item's statements."""
    def __init__(self, claims):
        self.claims = claims

    def items_for(self, site, titles):
        return {t: "Q900" for t in titles}

    def item_sitelinks(self, item):
        return None, {}

    def item_values(self, item, prop):
        return self.claims.get(prop, [])


def test_calibrate_reports_field_disagreement():
    q = Queue(":memory:")
    q.add("Q1", "film", "calibration", {"en": "The Film"}, {"enwiki": "The Film"}, calibration=True)
    q.set_outcome("Q1", 5, "pass", {
        "connection": {"choice": "direct", "probabilities": {"direct": 0.83}},
        "field": {"choice": "television"},
        "themes": [{"key": "mercy", "p": 0.7, "status": "applied", "evidence": {}}],
    })
    tree = {"fields": [{"key": "film", "item": "Q10", "names": {"en": "Film"}},
                       {"key": "television", "item": "Q11", "names": {"en": "TV"}}],
            "themes": [{"key": "mercy", "item": "Q20", "names": {"en": "Mercy"}},
                       {"key": "grace", "item": "Q21", "names": {"en": "Grace"}}]}
    wiki = FakeWiki({SV_FIELD: ["Q10"], SV_THEME: ["Q20", "Q21"]})
    out = calibration.report(q, {"en": wiki}, tree=tree, unmapped=["it:Orphan"])
    assert "The Film" in out and "direct (0.83)" in out
    assert "DISAGREE" in out  # television vs film
    assert "grace" in out  # on the item, not found by Jev
    assert "it:Orphan" in out


def test_run_stage_only_leaves_other_rows_pending():
    q = Queue(":memory:")
    q.add("Q1", "film", "s", {}, {}, calibration=True)
    q.add("Q2", "film", "s", {}, {})
    assert stages.run_stage(q, 1, lambda r: ("pass", {}), only=lambda r: r.calibration) == {"pass": 1}
    assert [r.qid for r in q.pending(1)] == ["Q2"]
