import pytest

from pipeline import __main__ as cli
from pipeline import load_config, stages
from pipeline.domains import DOMAINS, benchmark, hold_benchmarks
from pipeline.judge import ROOT
from pipeline.press.store import Mention, PressStore
from pipeline.publish import publish
from pipeline.queue import Queue
from sync import categories
from test_pipeline_cli import DomainJudge, DomainWikidata, DomainWikipedia, PageEditor
from test_pipeline_film import FakeSparql, item


@pytest.fixture
def q(tmp_path):
    return Queue(tmp_path / "q.db")


def test_hold_marks_only_rows_of_its_domain(q):
    q.add("QA", "art", "depicts", {"en": "A"}, {"enwiki": "A"}, data={"count": 30})
    q.add("QB", "architecture", "depicts", {"en": "B"}, {"enwiki": "B"}, data={"count": 30})
    benchmark.hold(q, "architecture", [{"qid": "QA", "kind": "strong"}, {"qid": "QB", "kind": "weak"}])
    assert q.row("QB").calibration is True
    assert q.row("QB").data[0]["benchmark_kind"] == "weak"
    assert q.row("QA").calibration is False


def test_add_reports_under_its_domain(q, capsys):
    added = benchmark.add(q, FakeSparql({}), "architecture", ["Q9"], [], ["Q41176"])
    assert added == 0 and q.row("Q9") is None
    assert "architecture calibration: Q9 skipped (a person, or not an architecture work)" in capsys.readouterr().out


def test_add_queues_its_rows_under_its_domain(q, capsys):
    q.add("Q8", "art", "depicts", {"en": "Old"}, {"enwiki": "Old"}, data={"count": 30})
    sparql = FakeSparql({"VALUES ?item { wd:Q9 }\n  ?item wdt:P31/wdt:P279* wd:Q16970": [item("Q9", "A", 20, "Q16970")],
                         "VALUES ?item { wd:Q2 }": [item("Q2", "B", 3, "Q1440476")]})
    kinds = [{"qid": "Q2", "kind": "weak"}, {"qid": "Q8", "kind": "control"}]
    assert benchmark.add(q, sparql, "architecture", ["Q9"], kinds, ["Q16970"]) == 2
    assert [(q.row(x).domain, q.row(x).calibration) for x in ("Q9", "Q2", "Q8")] == [
        ("architecture", True), ("architecture", True), ("art", False)]
    assert q.row("Q2").data[0]["benchmark_kind"] == "weak" and "benchmark" not in q.row("Q9").data[0]
    assert "architecture candidates: Q8 is already a art candidate, skipped" in capsys.readouterr().out


# --- held whatever brought the work in: the press, or a run before the hold ---

WORKS = {"architecture": ("Q180274", "Q2977"), "art": ("Q570188", "Q3305213")}  # in the config's benchmarks
URL = "https://x/a"


def wikidata_entity(cls):
    return {"labels": {"en": {"value": "Work"}}, "sitelinks": {"enwiki": {"title": "Work"}},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": cls}}}}]}}


class Wired:
    """The command line on fakes, with the real config: no candidates query finds anything."""

    def __init__(self, domain, monkeypatch, path):
        self.domain, self.path, self.editor = domain, path, PageEditor()
        self.qid, self.cls = WORKS[domain]
        tree = categories.load(ROOT)
        wd = type("W", (DomainWikidata,), {"entity": lambda s, qid: wikidata_entity(self.cls) if qid == self.qid else {}})
        monkeypatch.setattr(DOMAINS[domain], "candidates", lambda queue, sparql, wikipedia, cfg, wikidata=None: 0)
        monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("pass", {}))
        monkeypatch.setattr(cli, "_sparql", lambda: None)
        monkeypatch.setattr(cli, "_wikidata", wd)
        monkeypatch.setattr(cli, "_wikipedia", DomainWikipedia)
        monkeypatch.setattr(cli, "_wikis", lambda: {lang: object() for lang in cli.LANGS})
        monkeypatch.setattr(cli, "_repo", lambda wikis: None)
        monkeypatch.setattr(cli, "_tree", lambda: tree)
        monkeypatch.setattr(cli, "_judge", lambda queue: DomainJudge(categories.leaf_themes(tree)[0].key))
        monkeypatch.setattr(cli, "_editor", lambda: self.editor)

    def queue(self):
        return Queue(self.path)

    def row(self):
        return self.queue().row(self.qid)

    def command(self, *args):
        assert cli.main(["--domain", self.domain, *args, "--db", str(self.path)]) == 0

    def mention(self, waiting=False):
        press = PressStore.of(self.queue())
        press.add_article(URL, "sir", "it")
        domain = f"waiting:{self.domain}" if waiting else self.domain
        press.add_mention(Mention(URL, self.qid, "Work", "work", domain, 0.8, [], "waiting" if waiting else None))

    def live(self, stage=0, calibration=False, **data):
        """The work as a live press row that passed the stages up to `stage`."""
        queue = self.queue()
        queue.add(self.qid, self.domain, "press:sir", {"en": "Work"}, {"enwiki": "Work"}, calibration,
                  {"count": 90, "instance_of": [self.cls], **data})
        for n in range(1, stage + 1):
            queue.set_outcome(self.qid, n, "pass", {"notes": [], "connection": {"choice": "direct"},
                                                    "field": {"choice": "architecture"}, "themes": []})

    def proposals(self):
        return [t for t in self.editor.pages if t.startswith("Semina Verbi:Article proposals/")]

    def held(self):
        row = self.row()
        return row.calibration and row.data[0].get("benchmark") and row.data[0].get("benchmark_kind") == "strong"


@pytest.fixture(params=sorted(WORKS))
def wired(request, monkeypatch, tmp_path):
    return Wired(request.param, monkeypatch, tmp_path / "q.db")


STAGES = ("covered", "triage", "quick", "dossier", "adjudicate")


def test_a_benchmark_work_the_press_routes_is_held_through_every_stage_and_never_published(wired):
    wired.mention()
    wired.command("press", "route")
    assert wired.held() and wired.row().sources == ["press:sir"] and wired.queue().open_rows(wired.domain) == []
    for stage in STAGES:
        wired.command(stage)
        assert wired.held()
    assert wired.row().outcomes[5] == "pass"  # it ran as calibration
    wired.command("publish")
    assert wired.proposals() == [] and wired.row().status is None


def test_a_waiting_mention_of_a_benchmark_work_is_held_in_a_run_that_publishes(wired):
    wired.mention(waiting=True)
    wired.command("run", "--publish")
    assert wired.held() and wired.row().sources == ["press:sir"]
    assert wired.proposals() == [] and wired.row().status is None


def test_candidates_holds_the_waiting_mention_it_releases(wired):
    wired.mention(waiting=True)
    wired.command("candidates")
    assert wired.held()


@pytest.mark.parametrize("command", [*STAGES, "run", "publish"])
def test_a_live_benchmark_row_is_held_before_any_command_acts_on_it(wired, command):
    """A row that reached stage 5 before the hold starts again: its outcomes from stage 1 on are cleared."""
    wired.live(stage=5)
    wired.command(command)
    assert wired.held()
    reached = {"covered": {1}, "run": {1, 2, 3, 4, 5}}.get(command, set())  # the others wait for covered to pass it
    assert set(wired.row().outcomes) == {0} | reached
    if command == "publish":
        assert wired.proposals() == []


def test_the_hold_is_for_every_domain_whatever_the_command_line_says(wired):
    wired.live()
    other = next(d for d in WORKS if d != wired.domain)
    assert cli.main(["--domain", other, "covered", "--db", str(wired.path)]) == 0
    assert wired.held()


def test_a_dry_press_route_holds_nothing(wired):
    wired.live()
    wired.mention()
    wired.command("press", "route", "--dry-run")
    row = wired.row()
    assert not row.calibration and "benchmark" not in row.data[0]


def test_a_released_benchmark_work_stays_released(wired):
    wired.live(calibration=True, benchmark=True, benchmark_kind="strong")
    assert wired.queue().release_calibration(wired.qid)
    wired.mention()
    wired.command("press", "route")
    for command in (*STAGES, "candidates", "run"):
        wired.command(command)
        row = wired.row()
        assert not row.calibration and row.data[0]["released"] and "benchmark" not in row.data[0]
    wired.command("publish")
    assert len(wired.proposals()) == 1 and wired.row().status == "open"
    wired.command("run", "--publish")
    assert wired.row().status == "open" and not wired.row().calibration


def test_publish_never_writes_a_held_row(wired):
    wired.live(stage=5, calibration=True, benchmark=True, benchmark_kind="strong")
    publish(wired.queue(), wired.editor, load_config(), domain=wired.domain)
    assert wired.proposals() == [] and wired.row().status is None


def test_domains_without_a_benchmark_have_nothing_to_hold():
    assert [d for d, module in DOMAINS.items() if hasattr(module, "hold_benchmark")] == ["art", "architecture"]
    hold_benchmarks(None, {})  # no domain configured: the queue is not touched
