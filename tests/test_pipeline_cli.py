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


CONFIG = {"max_new_per_run": 2, "vote_threshold": 3, "quick_drop": 0.4, "sitelink_floor": 15,
          "dossier_languages": 3,
          "film": {"seeds": {"vatican_page": "Vatican's list of films", "awards": []}, "subjects": ["Q302"],
                   "book_class": "Q29154430", "biblical_classes": ["Q12405827"], "genres": ["Q9259727"]}}


@pytest.fixture
def base(monkeypatch, tmp_path):
    """The command line with the config fixed; the services are left to each test."""
    monkeypatch.setattr(cli, "load_config", lambda: CONFIG)
    return tmp_path / "data" / "pipeline.sqlite"


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
    monkeypatch.setattr(cli, "load_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "_sparql", lambda: None)
    monkeypatch.setattr(cli, "_wikidata", lambda: None)
    monkeypatch.setattr(cli, "_wikipedia", lambda lang, project="wikipedia": FakeWikipedia(lang, project))
    monkeypatch.setattr(cli, "_wikis", lambda: {lang: object() for lang in cli.LANGS})
    monkeypatch.setattr(cli, "_tree", lambda: {})
    monkeypatch.setattr(cli, "_judge", lambda queue: FakeJudge())
    monkeypatch.setattr(cli, "_editor", lambda: FakeEditor())

    def publish(queue, en, cfg, domain=None):
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


# ---- final review fixes ----

def item_row(qid, label, n=20):
    return {"item": qid, "l_en": label, "s_en": f"https://en.wikipedia.org/wiki/{label.replace(' ', '_')}",
            "n": str(n), "types": "Q11424"}


class ListSparql:
    """Rows for every query naming an item in VALUES; nothing for the bounded award/facet queries."""
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def select(self, query):
        self.queries.append(query)
        return [r for r in self.rows if f"wd:{r['item']} " in query or f"wd:{r['item']} }}" in query]


class LinksWikipedia:
    def __init__(self, lang, project="wikipedia"):
        self.lang, self.project = lang, project

    def links(self, title):
        assert self.lang == "en"
        return ["Ben-Hur"]

    def lead(self, title):
        return f"{title} lead"

    def sections(self, title):
        return [("Plot", f"{title} plot.")]

    def url(self, title):
        return f"https://{self.lang}.{self.project}.org/wiki/{title}"


class SitelinkWikidata:
    def __init__(self, links):
        self.links = links

    def by_sitelink(self, site, titles):
        return {t: self.links[(site, t)] for t in titles if (site, t) in self.links}

    def entity(self, qid):
        return {}


def test_candidates_runs_the_real_film_source(base, monkeypatch):
    monkeypatch.setattr(cli, "_sparql", lambda: ListSparql([item_row("Q1", "Ben-Hur")]))
    monkeypatch.setattr(cli, "_wikipedia", LinksWikipedia)
    monkeypatch.setattr(cli, "_wikidata", lambda: SitelinkWikidata({("enwiki", "Ben-Hur"): "Q1"}))
    assert cli.main(["candidates", "--db", str(base)]) == 0
    assert [r.qid for r in Queue(base).pending(1)] == ["Q1"]


class ClientWiki:
    """A client wiki (it, es, ...): pages and categories, but no Wikibase API."""
    def __init__(self, members=None):
        self._members = members or {}

    def members(self, category):
        return self._members.get(category, [])

    def page_info(self, titles):
        return {t: None for t in titles}

    def _no_wikibase(self, *a, **kw):
        raise AssertionError("client wikis have no wbgetentities: ask the repository")

    item_for = items_for = item_sitelinks = item_values = _no_wikibase


class RepoWiki(ClientWiki):
    """The en wiki, which is also the farm's Wikibase repository."""
    def __init__(self, members, items, links, values):
        super().__init__(members)
        self.items, self.links, self.values = items, links, values

    def item_for(self, site, title):
        return self.items.get((site, title))

    def items_for(self, site, titles):
        return {t: self.items[(site, t)] for t in titles if (site, t) in self.items}

    def item_sitelinks(self, item):
        return 1, dict(self.links.get(item, {}))

    def item_values(self, item, prop):
        return self.values.get((item, prop), [])


class CalibrationJudge:
    def plausible(self, row, synopsis):
        return 0.9

    def connection(self, row, text):
        return {"choice": "direct", "probabilities": {"direct": 0.8}, "confidence": 0.9}

    def field(self, row, text):
        return {"choice": "feature-films", "outcome": "applied"}

    def themes(self, row, text):
        return {"faith": {"p": 0.8, "status": "applied"}}

    def evidence(self, row, key, paragraphs):
        return 0


def test_calibrate_end_to_end_asks_only_the_repository(base, monkeypatch, capsys):
    from pipeline import judge as judge_module
    from sync import categories
    tree = categories.load(judge_module.ROOT)
    repo = RepoWiki({"Category:Feature films": [("Ordet", 0)]},
                    {("en", "Ordet"): "Q10", ("it", "Ordet"): "Q10", ("enwiki", "Ordet (film)"): "Q10"},
                    {"Q10": {"en": "Ordet", "it": "Ordet", "enwiki": "Ordet (film)"}},
                    {("Q10", SV_FIELD): ["Q48192"]})
    wikis = {"en": repo, **{lang: ClientWiki() for lang in cli.LANGS if lang != "en"}}
    wikis["it"] = ClientWiki({"Category:Lungometraggi": [("Ordet", 0)]})
    monkeypatch.setattr(cli, "_wikis", lambda: wikis)
    monkeypatch.setattr(cli, "_tree", lambda: tree)
    monkeypatch.setattr(cli, "_sparql", lambda: ListSparql([item_row("Q500", "Ordet (film)")]))
    monkeypatch.setattr(cli, "_wikipedia", LinksWikipedia)
    monkeypatch.setattr(cli, "_wikidata", lambda: SitelinkWikidata({("enwiki", "Ordet (film)"): "Q500"}))
    monkeypatch.setattr(cli, "_judge", lambda queue: CalibrationJudge())
    assert cli.main(["calibrate", "--db", str(base)]) == 0
    out = capsys.readouterr().out
    assert "Ordet (film) (Q500)" in out and "feature-films" in out
    row = Queue(base).calibration_rows()[0]
    assert sorted(row.outcomes) == [0, 1, 2, 3, 4, 5]
    assert row.data[1]["have"] == ["en", "it"] and "pt" in row.data[1]["missing"]


def test_quick_command_caps_new_rows(wired):
    path, _ = wired
    for command in ("candidates", "covered", "triage", "quick"):
        assert cli.main([command, "--db", str(path)]) == 0
    assert count(path, 2) == 5 and count(path, 3) == 2


def test_skip_records_a_drop_at_the_next_unset_stage(wired, capsys):
    path, _ = wired
    cli.main(["candidates", "--db", str(path)])
    cli.main(["covered", "--db", str(path)])
    assert cli.main(["skip", "Q1", "--reason", "crashes Jev", "--db", str(path)]) == 0
    q = Queue(path)
    row = q._rows("qid=?", ("Q1",))[0]
    assert row.outcomes[2] == "drop" and row.data[2] == {"reason": "crashes Jev", "skipped": True}
    assert "Q1" not in [r.qid for r in q.pending(2)]
    assert cli.main(["skip", "Q404", "--reason", "x", "--db", str(path)]) == 1
    assert "Q404" in capsys.readouterr().err


def test_domain_option_selects_rows_and_module(wired, monkeypatch):
    from types import SimpleNamespace
    from pipeline.domains import DOMAINS
    path, calls = wired
    seen = []
    music = SimpleNamespace(
        candidates=lambda queue, sparql, wikipedia, cfg, wikidata=None: 0,
        triage=lambda row, cfg: (seen.append(row.qid), ("pass", ""))[1],
    )
    monkeypatch.setitem(DOMAINS, "music", music)
    q = Queue(path)
    q.add("Q1", "film", "s", {"en": "A film"}, {"enwiki": "A film"}, data={"instance_of": [], "count": 30})
    q.add("Q2", "music", "s", {"en": "A song"}, {"enwiki": "A song"}, data={"instance_of": [], "count": 30})
    assert cli.main(["covered", "--domain", "music", "--db", str(path)]) == 0
    assert [r.qid for r in Queue(path).pending(1)] == ["Q1"]
    assert cli.main(["triage", "--domain", "music", "--db", str(path)]) == 0
    assert seen == ["Q2"]


def test_typesafe_error_is_reported_not_raised(wired, monkeypatch, capsys):
    from typesafe_sdk import TypeSafeError
    path, _ = wired

    class Down:
        def plausible(self, row, synopsis):
            raise TypeSafeError("service down")

    monkeypatch.setattr(cli, "_judge", lambda queue: Down())
    for command in ("candidates", "covered", "triage"):
        cli.main([command, "--db", str(path)])
    assert cli.main(["quick", "--db", str(path)]) == 1
    assert "ERROR: service down" in capsys.readouterr().err


def test_run_publish_checks_credentials_before_the_stages(wired, monkeypatch, capsys):
    path, calls = wired

    def no_editor():
        raise cli.CliError("SV_EDITOR_USER is not set")

    monkeypatch.setattr(cli, "_editor", no_editor)
    assert cli.main(["run", "--publish", "--db", str(path)]) == 1
    assert count(path, 0) == 0 and calls["publish"] == 0
    assert "SV_EDITOR_USER" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--db", "X.sqlite", "redo", "3"], ["redo", "3", "--db", "X.sqlite"],
                                  ["--db", "X.sqlite", "--domain", "film", "votes"]])
def test_common_options_before_or_after_the_command(argv):
    """CodeRabbit 4640187363: `--db X redo 3` must use X, not the default database."""
    args = cli.parse_args(argv)
    assert args.db == "X.sqlite" and args.domain == "film"


def test_omitted_common_options_take_the_defaults():
    args = cli.parse_args(["redo", "3"])
    assert args.db == cli.DEFAULT_DB and args.domain == "film"


def test_redo_with_db_before_the_command_clears_that_database(base, tmp_path):
    other = tmp_path / "other.sqlite"
    q = Queue(other)
    q.add("Q1", "film", "wikidata", {"en": "A"}, {"enwiki": "A"})
    q.set_outcome("Q1", 1, "pass", {})
    assert cli.main(["--db", str(other), "redo", "1"]) == 0
    assert 1 not in Queue(other).row("Q1").outcomes


def test_readme_documents_the_timer_checkout_path_and_its_override():
    """CodeRabbit 4640187439: the unit hard-codes the checkout; the README must say so and how to override it."""
    import re
    from conftest import ROOT
    unit = (ROOT / "deploy" / "systemd" / "sv-pipeline-votes.service").read_text(encoding="utf-8")
    workdir = re.search(r"^WorkingDirectory=(.+)$", unit, re.M).group(1)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    timer = readme[readme.index("**Vote timer.**"):readme.index("**Sources and licences.**")]
    assert workdir.replace("%h", "~") in timer
    assert "systemctl --user edit sv-pipeline-votes.service" in timer
    assert "WorkingDirectory=" in timer and "\nExecStart=\n" in timer  # the list is reset before the new command
    assert timer.index("systemctl --user edit") < timer.index("systemctl --user enable --now")


TREE = {"fields": [{"key": "film", "item": "Q10", "names": {"en": "Film"},
                    "children": [{"key": "feature-films", "item": "Q12", "names": {"en": "Feature films"}}]},
                   {"key": "television", "item": "Q11", "names": {"en": "TV"}}],
        "themes": [{"key": "mercy", "item": "Q20", "names": {"en": "Mercy"}},
                   {"key": "grace", "item": "Q21", "names": {"en": "Grace"}}]}


def calibration_row(q, labels, sitelinks, field="feature-films", themes=(("mercy", 0.7),), scores=None, quick=0.6):
    q.add("Q1", "film", "calibration", labels, sitelinks, calibration=True)
    q.set_outcome("Q1", 3, "pass", {"p": quick})
    q.set_outcome("Q1", 5, "pass", {
        "connection": {"choice": "seeds", "probabilities": {"seeds": 0.6}},
        "field": {"choice": field},
        "themes": [{"key": k, "p": p, "status": "author", "evidence": {}} for k, p in themes],
        "theme_scores": scores or {k: p for k, p in themes}})


def test_a_deeper_field_under_the_items_field_agrees():
    q = Queue(":memory:")
    calibration_row(q, {"en": "The Film"}, {"enwiki": "The Film"})
    out = calibration.report(q, {"en": FakeWiki({SV_FIELD: ["Q10"], SV_THEME: ["Q20"]})}, tree=TREE)
    assert "feature-films | film (agree, deeper) |" in out and "DISAGREE" not in out


def test_a_row_without_an_english_label_is_named_by_another_label_or_its_sitelink():
    q = Queue(":memory:")
    calibration_row(q, {"it": "Il miglio verde"}, {"enwiki": "The Green Mile (film)"})
    assert "### Il miglio verde (Q1)" in calibration.report(q, {"en": FakeWiki({})}, tree=TREE)
    q = Queue(":memory:")
    calibration_row(q, {}, {"enwiki": "Pulp Fiction"})
    assert "### Pulp Fiction (Q1)" in calibration.report(q, {"en": FakeWiki({})}, tree=TREE)


def test_a_missed_theme_shows_jevs_score():
    q = Queue(":memory:")
    calibration_row(q, {"en": "F"}, {"enwiki": "F"}, scores={"mercy": 0.7, "grace": 0.31})
    out = calibration.report(q, {"en": FakeWiki({SV_FIELD: ["Q12"], SV_THEME: ["Q20", "Q21"]})}, tree=TREE)
    assert "not found by Jev: grace (0.31)" in out


@pytest.mark.parametrize("quick,themes,verdict", [
    (0.6, (("mercy", 0.7),), "would be proposed"),
    (0.2, (("mercy", 0.7),), "would be dropped: quick check 0.20 < 0.25"),
    (0.6, (), "would be dropped: no theme at 0.50"),
])
def test_the_report_says_whether_a_new_candidate_would_be_proposed(quick, themes, verdict, monkeypatch):
    monkeypatch.setattr(calibration, "load_config", lambda: {"quick_drop": 0.25})
    q = Queue(":memory:")
    calibration_row(q, {"en": "F"}, {}, themes=themes, quick=quick)
    assert f"| verdict | {verdict} | |" in calibration.report(q, {"en": FakeWiki({})}, tree=TREE)


def test_the_verdict_of_a_confident_direct_connection_without_a_theme(monkeypatch):
    monkeypatch.setattr(calibration, "load_config", lambda: {"quick_drop": 0.25})
    q = Queue(":memory:")
    calibration_row(q, {"en": "F"}, {}, themes=())
    q.set_outcome("Q1", 5, "pass", {**q.row("Q1").data[5],
                                    "connection": {"choice": "direct", "probabilities": {"direct": 0.99}}})
    assert "| verdict | would be proposed (direct connection) | |" in calibration.report(q, {"en": FakeWiki({})}, tree=TREE)


def test_a_saved_theme_under_the_current_cutoff_is_neither_shown_nor_counted(monkeypatch):
    """CodeRabbit (PR 37, calibration.py:61): a result saved under the 0.40 cutoff is read with today's."""
    monkeypatch.setattr(calibration, "load_config", lambda: {"quick_drop": 0.25})
    q = Queue(":memory:")
    calibration_row(q, {"en": "F"}, {"enwiki": "F"}, themes=(("mercy", 0.45),))
    out = calibration.report(q, {"en": FakeWiki({SV_THEME: ["Q20"]})}, tree=TREE)
    assert "| verdict | would be dropped: no theme at 0.50 | |" in out
    assert "| themes | none |" in out and "not found by Jev: mercy (0.45)" in out


def test_literature_domain_is_accepted_and_stage_2_reads_its_floor(wired, monkeypatch):
    from pipeline.domains import literature
    path, _ = wired
    config = {**CONFIG, "literature": {"sitelink_floor": 8}}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    seen = []
    monkeypatch.setattr(literature, "triage", lambda row, cfg: (seen.append(cfg["sitelink_floor"]), ("pass", ""))[1])
    Queue(path).add("Q1", "literature", "author", {"en": "A book"}, {"enwiki": "A book"},
                    data={"instance_of": [], "count": 10})
    assert cli.main(["covered", "--domain", "literature", "--db", str(path)]) == 0
    assert cli.main(["triage", "--domain", "literature", "--db", str(path)]) == 0
    assert seen == [8] and config["sitelink_floor"] == 15


# --- release-benchmark ---

@pytest.fixture
def bench(tmp_path):
    path = tmp_path / "q.db"
    queue = Queue(path)
    queue.add("Q1", "art", "calibration", {"en": "Plain"}, {}, calibration=True, data={"count": 30})
    queue.add("Q2", "art", "calibration", {"en": "Trinity"}, {"enwiki": "Trinity"}, calibration=True,
              data={"count": 30, "benchmark": True, "benchmark_kind": "strong"})
    return path, queue


def test_release_turns_one_benchmark_row_into_a_candidate(bench, capsys):
    path, queue = bench
    assert cli.main(["--domain", "art", "release-benchmark", "Q2", "--db", str(path)]) == 0
    assert "released Q2" in capsys.readouterr().out
    row = Queue(path).row("Q2")
    assert row.calibration is False and not row.data[0].get("benchmark")
    assert row.data[0]["count"] == 30 and "Q2" in [r.qid for r in Queue(path).open_rows("art")]
    assert Queue(path).row("Q1").calibration is True
    assert "Q2" in [r.qid for r in Queue(path).pending(1, "art")]  # it runs stages 1-5 as a live candidate


def test_release_refuses_a_non_benchmark_qid(bench, capsys):
    path, queue = bench
    assert cli.main(["--domain", "art", "release-benchmark", "Q1", "Q404", "--db", str(path)]) != 0
    out = capsys.readouterr().out
    assert "Q1 is not a benchmark row, nothing changed" in out and "Q404 is not a benchmark row" in out
    row = Queue(path).row("Q1")
    assert row.calibration is True and Queue(path).row("Q2").data[0]["benchmark"]


def test_release_releases_the_valid_qids_even_when_another_is_refused(bench, capsys):
    path, _ = bench
    assert cli.main(["release-benchmark", "Q2", "Q1", "--db", str(path)]) != 0
    assert Queue(path).row("Q2").calibration is False and Queue(path).row("Q1").calibration is True


# --- the music and art domains, end to end on fakes ---

class DomainWikipedia:
    def __init__(self, lang, project="wikipedia"):
        self.lang, self.project = lang, project

    def lead(self, title):
        return f"{title} is a work with a theological reading in its sources."

    def sections(self, title):
        return [("Reception", f"{title} was read as a parable of mercy.")]

    def url(self, title):
        return f"https://{self.lang}.{self.project}.org/wiki/{title.replace(' ', '_')}"


class DomainWikidata:
    def entity(self, qid):
        return {}

    def labels(self, qids, lang="en"):
        return {}


class DomainJudge:
    def __init__(self, key):
        self.key = key

    def plausible(self, row, synopsis):
        return 0.9

    def connection(self, row, text):
        return {"choice": "seeds", "probabilities": {"seeds": 0.8}, "confidence": 0.9}

    def field(self, row, text):
        return {"choice": "music", "probabilities": {"music": 0.7}}

    def themes(self, row, text):
        return {self.key: {"p": 0.7, "status": "applied"}}

    def evidence(self, row, key, paragraphs):
        return 0


class PageEditor:
    def __init__(self):
        self.pages = {}

    def revision(self, title):
        return self.pages.get(title)

    def edit(self, title, text, summary, tags=None, createonly=False, baserevid=None):
        self.pages[title] = (len(self.pages) + 1, text)
        return {"result": "Success"}


@pytest.mark.parametrize("domain", ["music", "art"])
def test_run_takes_a_candidate_to_a_proposal(domain, monkeypatch, tmp_path):
    from pipeline import load_config
    from pipeline.domains import DOMAINS
    from sync import categories
    from pipeline.judge import ROOT
    tree = categories.load(ROOT)
    key = categories.leaf_themes(tree)[0].key
    module, path, editor = DOMAINS[domain], tmp_path / "data" / "pipeline.sqlite", PageEditor()

    def candidates(queue, sparql, wikipedia, cfg, wikidata=None):
        queue.add("Q42", domain, "form" if domain == "music" else "list", {"en": "Sample Work"},
                  {"enwiki": "Sample Work"}, data={"instance_of": list(module.WORK_CLASSES[:1]), "count": 40})
        return 1

    monkeypatch.setattr(module, "candidates", candidates)
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("pass", {}))
    monkeypatch.setattr(module, "covered", lambda row, repo, wikis: ("pass", {}), raising=False)
    monkeypatch.setattr(cli, "load_config", load_config)
    monkeypatch.setattr(cli, "_sparql", lambda: None)
    monkeypatch.setattr(cli, "_wikidata", DomainWikidata)
    monkeypatch.setattr(cli, "_wikipedia", DomainWikipedia)
    monkeypatch.setattr(cli, "_wikis", lambda: {lang: object() for lang in cli.LANGS})
    monkeypatch.setattr(cli, "_tree", lambda: tree)
    monkeypatch.setattr(cli, "_judge", lambda queue: DomainJudge(key))
    monkeypatch.setattr(cli, "_editor", lambda: editor)
    assert cli.main(["run", "--publish", "--domain", domain, "--db", str(path)]) == 0
    row = Queue(path).row("Q42")
    assert row.outcomes == {0: "pass", 1: "pass", 2: "pass", 3: "pass", 4: "pass", 5: "pass"}
    proposals = [t for t in editor.pages if t.startswith("Semina Verbi:Article proposals/")]
    assert len(proposals) == 1 and "Sample Work" in proposals[0]
    assert f"=== {domain.capitalize()} ===" in editor.pages["Semina Verbi:Article proposals"][1]


@pytest.mark.parametrize("domain, source, passing, dropped, floor", [
    ("music", "artist", 25, 15, 20),  # an artist-only row: the artist floor (20), not the music floor (10)
    ("art", "depicts", 20, 10, 15),
])
def test_triage_reads_the_real_config_for_the_domain(domain, source, passing, dropped, floor, monkeypatch, tmp_path,
                                                     capsys):
    from pipeline import load_config
    from pipeline.domains import DOMAINS
    module, path = DOMAINS[domain], tmp_path / "data" / "pipeline.sqlite"
    monkeypatch.setattr(cli, "load_config", load_config)
    monkeypatch.setattr(module, "covered", lambda row, repo, wikis: ("pass", {}), raising=False)
    monkeypatch.setattr(stages, "covered", lambda row, repo, wikis: ("pass", {}))
    monkeypatch.setattr(cli, "_wikis", lambda: {lang: object() for lang in cli.LANGS})
    queue = Queue(path)
    for qid, n in (("Q1", passing), ("Q2", dropped)):
        queue.add(qid, domain, source, {"en": qid}, {"enwiki": qid},
                  data={"instance_of": list(module.WORK_CLASSES[:1]), "count": n})
    assert cli.main(["covered", "--domain", domain, "--db", str(path)]) == 0
    assert cli.main(["triage", "--domain", domain, "--db", str(path)]) == 0
    assert Queue(path).row("Q1").outcomes[2] == "pass"
    assert Queue(path).row("Q2").outcomes[2] == "drop"
    assert f"under the floor of {floor}" in str(Queue(path).row("Q2").data.get(2))
