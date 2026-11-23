"""The `press` command line: stages wired together, with every network-facing service replaced by a fake."""
import json
from pathlib import Path

import pytest

from pipeline import __main__ as cli
from pipeline import stages
from pipeline.press import link
from pipeline.press.fetch import Refused
from pipeline.press.store import PressStore
from pipeline.queue import Queue
from tests.test_pipeline_cli import CONFIG

ROOT = Path(__file__).resolve().parents[1]
FEED = "https://news.example/feed"
A1 = "https://news.example/cultura/ordet"
A2 = "https://news.example/cultura/silence"
A3 = "https://news.example/cultura/sport"

RSS = f"""<?xml version="1.0"?><rss><channel>
<item><link>{A1}</link><pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate></item>
<item><link>{A2}</link><pubDate>Tue, 03 Nov 2026 08:00:00 GMT</pubDate></item>
<item><link>{A3}</link><pubDate>Wed, 04 Nov 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def html(title, body):
    return (f'<html lang="it"><head><title>{title}</title></head><body><article><h1>{title}</h1>'
            f"<p>{body}</p></article></body></html>")


PAGES = {
    A1: html("Ordet, la fede", "Abbiamo rivisto <em>Ordet</em> di Dreyer, un film sulla preghiera e sul miracolo."),
    A2: html("Silence, il dubbio", "Il film <em>Silence</em> di Scorsese racconta il silenzio di Dio."),
    A3: html("La partita", "Una partita di calcio senza nulla di culturale."),
}

PRESS = {"user_agent": "SeminaVerbi-press/1.0 (+test)", "delay_seconds": 0, "max_articles_per_run": 200,
         "culture_drop": 0.4, "pick_p": 0.8, "pick_confidence": 0.6, "mention_keep": 0.6,
         "angle_drop": 0.4, "stance_drop": 0.4, "press_exempt": 0.6,
         "waiting_classes": {"music": ["Q7366"]},
         "outlets": [{"id": "sir", "name": "SIR", "base": "https://news.example", "langs": ["it"],
                      "entries": [FEED], "culture_paths": ["/cultura/"], "since": None},
                     {"id": "civilta", "name": "La Civiltà Cattolica", "base": "https://civ.example",
                      "langs": ["it"], "entries": ["https://civ.example/feed"], "culture_paths": [], "since": None}]}


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code, self.headers = text, status, {}


class FakeClient:
    """A PoliteClient double: civilta answers 403 (recorded in `refused`), news.example serves the fixtures."""

    def __init__(self):
        self.refused, self.calls = {}, []

    def get(self, outlet, url, conditional=False):
        self.calls.append(url)
        if outlet == "civilta":
            self.refused[outlet] = "403"
            raise Refused(outlet, "403")
        return Resp(RSS) if url == FEED else Resp(PAGES[url])

    def remember(self, url, response):
        pass


class FakeJudge:
    def __init__(self):
        self.asked = []

    def culture(self, url, text):
        self.asked.append(("culture", url))
        return 0.1 if url == A3 else 0.9

    def angle(self, url, text):
        self.asked.append(("angle", url))
        return 0.7

    def stance(self, url, label, passage, item, title):
        self.asked.append(("stance", url))
        return 0.8

    def pick(self, url, passage, label, options):
        assert len(options) == 1, "one hit per title"
        return next(iter(options)), 0.9, 0.9

    def substantive(self, url, label, passage, item):
        self.asked.append(("substantive", url))
        return 0.8


def entity(label):
    return {"labels": {"en": {"value": label}}, "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q11424"}}}}]},
            "sitelinks": {"enwiki": {"title": label}, "itwiki": {"title": label}}}


class FakeWikidata:
    ENTITIES = {"Q1": entity("Ordet"), "Q2": entity("Silence")}
    SEARCH = {"Ordet": ["Q1"], "Silence": ["Q2"]}

    def search(self, text, lang):
        return list(self.SEARCH.get(text, []))

    def by_sitelink(self, site, titles):
        return {}

    def entity(self, qid):
        return self.ENTITIES[qid]


class FakeEditor:
    def __init__(self):
        self.edits = []

    def revision(self, title):
        return None

    def edit(self, title, text, summary, **kw):
        self.edits.append((title, text))


class FakeRepo:
    """The en wiki, read-only: Ordet has a SeminaVerbi article (local item Q900), Silence does not."""

    def items_for(self, site, titles):
        return {"Ordet": "Q900"} if "Ordet" in titles and site == "enwiki" else {}

    def item_sitelinks(self, item):
        return 1, {"en": "Ordet (film)", "it": "Ordet", "enwiki": "Ordet"}

    def revision(self, title):
        return None


@pytest.fixture
def press(monkeypatch, tmp_path):
    """The press commands with the outlets, Jev, Wikidata and the wikis faked; returns the db path and the fakes."""
    fakes = {"client": FakeClient(), "judge": FakeJudge(), "editor": FakeEditor(), "logins": 0, "judges": 0,
             "routes": 0}

    def login():
        fakes["logins"] += 1
        return fakes["editor"]

    def judge(queue):
        fakes["judges"] += 1
        return fakes["judge"]

    monkeypatch.setattr(cli, "load_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "load_press_config", lambda: PRESS)
    monkeypatch.setattr(cli, "_press_client", lambda pcfg, store: fakes["client"])
    monkeypatch.setattr(cli, "_press_judge", judge)
    monkeypatch.setattr(cli, "_login_editor", login)
    monkeypatch.setattr(cli, "_wikidata", lambda: FakeWikidata())
    monkeypatch.setattr(cli, "_sparql", lambda: None)
    monkeypatch.setattr(cli, "_wikis", lambda: {lang: FakeRepo() for lang in cli.LANGS})
    monkeypatch.setattr(link, "class_route", lambda instance_of, sparql, cfg, cache=None: "film")
    # covered: Ordet is covered on en (translation), Silence nowhere
    monkeypatch.setattr(stages, "covered",
                        lambda row, repo, wikis: ("translation", {"have": ["en"]}) if row.qid == "Q1" else ("pass", {}))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    return tmp_path / "pipeline.sqlite", fakes


def candidates(db):
    return Queue(db).db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]


def test_dry_run_writes_nothing_to_queue_or_wiki(press, monkeypatch, capsys):
    db, fakes = press
    monkeypatch.setattr(cli, "route", lambda *a, **k: (_ for _ in ()).throw(AssertionError("route called")))
    assert cli.main(["press", "run", "--dry-run", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert candidates(db) == 0
    assert fakes["logins"] == 0 and fakes["editor"].edits == []
    store = PressStore.of(Queue(db))
    assert store.unrouted() and all(m.route is None for m in store.mentions())
    assert "enrichment" in out and "candidate" in out and "Q1" in out and "Q2" in out
    assert "p=0.80" in out and "culture=0.90" in out and "angle=0.70" in out and "stance=0.80" in out
    assert ("angle", A3) not in fakes["judge"].asked  # below culture_drop: not asked
    assert "1/2" in out  # share of mentions hitting existing SeminaVerbi articles
    assert store.article(A3).outcome == "drop"


def test_run_reports_refused_outlets(press, capsys):
    db, fakes = press
    assert cli.main(["press", "run", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "civilta (403)" in out
    assert "sir 3" in out          # new articles
    assert "drop 1" in out         # culture drops
    assert "candidate 1" in out and "enrichment 1" in out
    assert fakes["logins"] == 1
    assert Queue(db).row("Q2").sources == ["press:sir"]
    assert Queue(db).row("Q1") is None
    [(title, text)] = fakes["editor"].edits
    assert title == "Semina Verbi:Press for existing articles"
    assert "[[:en:Ordet (film)]]" in text and "[[:it:Ordet]]" in text and A1 in text


def test_run_fetches_each_article_once(press):
    db, fakes = press
    assert cli.main(["press", "run", "--db", str(db)]) == 0
    assert sorted(fakes["client"].calls.count(u) for u in (A1, A2, A3)) == [1, 1, 1]


def test_press_needs_the_jev_key_only_for_filter_and_link(press, monkeypatch, capsys):
    db, fakes = press
    monkeypatch.delenv("TYPESAFE_API_KEY")
    for action in ("discover", "route", "enrich"):
        assert cli.main(["press", action, "--db", str(db)]) == 0, action
    assert fakes["judges"] == 0
    for action in ("filter", "link", "run"):
        assert cli.main(["press", action, "--db", str(db)]) == 1, action
        assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    assert fakes["judges"] == 0
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert cli.main(["press", "filter", "--db", str(db)]) == 0
    assert fakes["judges"] == 1


def test_enrich_and_run_log_in_before_anything_else(press, monkeypatch, capsys):
    db, fakes = press

    def no_login():
        raise cli.CliError("SV_EDITOR_USER is not set")

    monkeypatch.setattr(cli, "_login_editor", no_login)
    assert cli.main(["press", "run", "--db", str(db)]) == 1
    assert fakes["client"].calls == [] and fakes["judges"] == 0
    assert cli.main(["press", "enrich", "--db", str(db)]) == 1
    assert "SV_EDITOR_USER" in capsys.readouterr().err
    assert cli.main(["press", "enrich", "--dry-run", "--db", str(db)]) == 0  # prints the page, no login


def test_discover_dry_run_lists_urls_and_stores_nothing(press, capsys):
    db, fakes = press
    assert cli.main(["press", "discover", "--dry-run", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert A1 in out and A2 in out and "civilta (403)" in out
    assert PressStore.of(Queue(db)).articles("found") == []


def test_unreachable_article_is_skipped_and_the_run_goes_on(press, capsys):
    db, fakes = press
    client = fakes["client"]
    real = client.get

    def get(outlet, url, conditional=False):
        if url == A1:
            raise TimeoutError("timed out")
        if url == A2:
            return Resp("gone", 404)
        return real(outlet, url, conditional)

    client.get = get
    assert cli.main(["press", "discover", "--db", str(db)]) == 0
    assert cli.main(["press", "filter", "--db", str(db)]) == 0
    store = PressStore.of(Queue(db))
    assert store.article(A1).stage == "found"                                # transient: tried again next run
    assert (store.article(A2).stage, store.article(A2).outcome) == ("filtered", "skip")  # gone for good
    assert store.article(A3).outcome == "drop"


def test_filter_records_title_and_date_from_the_page(press):
    db, fakes = press
    assert cli.main(["press", "discover", "--db", str(db)]) == 0
    assert cli.main(["press", "filter", "--db", str(db)]) == 0
    art = PressStore.of(Queue(db)).article(A1)
    assert art.title == "Ordet, la fede" and art.published == "2026-11-02"
    assert art.data["culture"] == 0.9


def test_link_keys_mentions_by_the_stored_url(press):
    db, fakes = press
    for action in ("discover", "filter", "link"):
        assert cli.main(["press", action, "--db", str(db)]) == 0
    store = PressStore.of(Queue(db))
    assert sorted((m.url, m.qid) for m in store.mentions()) == [(A1, "Q1"), (A2, "Q2")]
    assert store.article(A1).stage == "linked"


def test_timer_and_service():
    timer = (ROOT / "deploy/systemd/sv-pipeline-press.timer").read_text()
    service = (ROOT / "deploy/systemd/sv-pipeline-press.service").read_text()
    assert "OnCalendar=*-*-* 04:30:00" in timer and "Persistent=true" in timer
    assert "EnvironmentFile=%h/.config/seminaverbi/pipeline.env" in service
    assert "runeditor.py pipeline press run" in service


def test_outlets_config():
    cfg = json.loads((ROOT / "config/press.json").read_text())
    ids = [o["id"] for o in cfg["outlets"]]
    assert ids == ["osservatore", "vaticannews", "avvenire", "romasette", "ewtn", "civilta", "famigliacristiana",
                   "aleteia", "ncregister", "kto", "sir"]
    for o in cfg["outlets"]:
        assert o["entries"] or o.get("note"), o["id"]
    assert cfg["user_agent"].startswith("SeminaVerbi-press/1.0 ")


def test_press_bot_page_text():
    text = (ROOT / "docs/press-bot-page.wiki").read_text()
    assert "Catholic Digital Commons Foundation" in text and "SeminaVerbi-press" in text
    assert "25 words" in text and "200" in text and "@" not in text


def test_a_jev_error_on_one_article_still_filters_the_others(press, capsys):
    from typesafe_sdk import TypeSafeError
    db, fakes = press
    judge = fakes["judge"]
    real = judge.culture

    def culture(url, text):
        if url == A1:
            raise TypeSafeError("service down")
        return real(url, text)

    judge.culture = culture
    assert cli.main(["press", "discover", "--db", str(db)]) == 0
    assert cli.main(["press", "filter", "--db", str(db)]) == 0
    captured = capsys.readouterr()
    store = PressStore.of(Queue(db))
    assert store.article(A1).stage == "found"          # retried next run
    assert store.article(A2).outcome == "keep" and store.article(A3).outcome == "drop"
    assert f"press: sir {A1}: service down" in captured.err
    assert "errors 1" in captured.out


def test_a_wikidata_error_on_one_article_still_links_the_other_and_routes(press, monkeypatch, capsys):
    import requests
    db, fakes = press
    search = FakeWikidata.search

    def flaky(self, text, lang):
        if text == "Ordet":
            raise requests.Timeout("wikidata timed out")
        return search(self, text, lang)

    monkeypatch.setattr(FakeWikidata, "search", flaky)
    assert cli.main(["press", "run", "--db", str(db)]) == 0
    captured = capsys.readouterr()
    store = PressStore.of(Queue(db))
    assert store.article(A1).stage == "filtered"       # left for the next run
    assert store.article(A2).stage == "linked"
    assert [(m.qid, m.route) for m in store.mentions()] == [("Q2", "candidate")]
    assert f"press: sir {A1}: wikidata timed out" in captured.err
    assert "errors 1" in captured.out
    assert "press route: candidate 1" in captured.out and "press enrich:" in captured.out
