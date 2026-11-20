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
