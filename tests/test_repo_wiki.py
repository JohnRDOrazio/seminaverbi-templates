"""The repository wiki (config/wikis.json, sync.wiki.REPO_WIKI): entities are read and written there, pages on
their own wiki. The tools run against a fake farm that records every API request with its host."""
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import sync.wiki
from conftest import ROOT
from sync import categories as tree
from sync.pages import LANGS, desired_pages
from sync.wiki import WikiClient

DOMAIN = ".seminaverbi.bibleget.io"
ENTITY_ACTIONS = ("wbgetentities", "wbsearchentities", "wbeditentity", "wbcreateclaim", "wbsetsitelink")
P = json.loads((ROOT / "config" / "wikibase.json").read_text(encoding="utf-8"))
FILM = "Q13367"  # an item type with the film layout
FIELD = next(n for n in tree.nodes(tree.load(ROOT)) if n.key == "film").names
BOX = "{{Infobox film|released={{Film date|1999|11|17}}}}\nIntro.\n[[Category:Film]]"


class Farm:
    """Six wikis and their repository: the same entities whatever wiki is asked, so only the hosts can differ."""

    def __init__(self):
        self.log = []  # (wiki, params)
        self.new = {}  # titles created items link: (site, title) -> item
        self.claims = {}  # (item, property) -> datavalues written
        self.pages = {"en": {"Film": BOX, "New": "Text."}, "it": {"Film": "Testo."}}
        self.items = {"Q100": {"en": "Film", "it": "Film", "enwiki": "Film (film)"},
                      "Q300": {"en": "Category:Old", "enwiki": "Category:Film"}}
        self.missing = {"Category:Old"}

    def client_request(self, client, method, params, budget=None):
        host = urlsplit(client.api).hostname
        assert host == client.lang + DOMAIN
        self.log.append((client.lang, {k: v for k, v in params.items() if k not in ("format", "formatversion")}))
        return getattr(self, "do_" + params["action"])(client.lang, params)

    # ---- pages ----

    def do_login(self, lang, p):
        return {"login": {"result": "Success"}}

    def do_query(self, lang, p):
        if p.get("meta") == "tokens":
            return {"query": {"tokens": {f"{p['type']}token": "t"}}}
        if p.get("meta") == "siteinfo":
            return {"query": {"general": {"mainpage": "Main"}, "namespaces": {"14": {"name": "Category"}}}}
        if p.get("list") == "allpages":
            return {"query": {"allpages": [{"title": t} for t in self.pages.get(lang, {})]}}
        if p.get("list") == "categorymembers":
            titles = ["Film"] if lang == "en" and p["cmtitle"] == "Category:Film" else []
            return {"query": {"categorymembers": [{"title": t, "ns": 0} for t in titles]}}
        if p.get("list") == "usercontribs":
            return {"query": {"usercontribs": [{"title": "Item:Q300", "revid": 7, "parentid": 0, "new": True}]}}
        if p.get("generator") == "allpages":
            return {"query": {"pages": []}}
        if "revids" in p:  # an item's revision
            content = json.dumps({"sitelinks": {"enwiki": {"title": "Category:Film"}}})
            return {"query": {"pages": [{"revisions": [{"slots": {"main": {"content": content}}}]}]}}
        pages = []
        for title in p["titles"].split("|"):
            if title in self.missing:
                pages.append({"title": title, "missing": True})
                continue
            text = self.pages.get(lang, {}).get(title, "text")
            cats = [{"title": f"Category:{FIELD[lang]}"}] if title == "Film" else []
            pages.append({"title": title, "categories": cats, "revisions": [
                {"revid": 5, "comment": "", "user": "U", "slots": {"main": {"content": text}}}]})
        return {"query": {"pages": pages}}

    def do_parse(self, lang, p):
        film = p.get("title") == "Film" or "oldid" in p
        return {"parse": {"text": "<p>x</p>", "categories": [{"category": FIELD[lang]}] if film else [],
                          "links": [], "templates": []}}

    def do_edit(self, lang, p):
        return {"edit": {"result": "Success"}}

    # ---- entities ----

    def _entity(self, item):
        claims = {P["instance_of"]: [{"id": FILM}]} if item == "Q100" else {}
        for (q, prop), values in self.claims.items():
            if q == item:
                claims.setdefault(prop, []).extend(values)
        return {"id": item, "lastrevid": 9, "labels": {"en": {"value": item}},
                "sitelinks": {s: {"title": t} for s, t in self.items.get(item, {}).items()},
                "claims": {prop: [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                    "value": v, "type": "x"}}} for v in values] for prop, values in claims.items()}}

    def do_wbgetentities(self, lang, p):
        if "ids" in p:
            return {"entities": {q: self._entity(q) for q in p["ids"].split("|")}}
        out = {}
        for title in p["titles"].split("|"):
            found = [q for q, links in self.items.items() if links.get(p["sites"]) == title]
            out.update({q: self._entity(q) for q in found} or {f"-{title}": {"missing": ""}})
        return {"entities": out}

    def do_wbsearchentities(self, lang, p):
        return {"search": []}

    def do_wbeditentity(self, lang, p):
        if p.get("new") == "item":
            item = f"Q{900 + len(self.items)}"
            links = json.loads(p["data"])["sitelinks"]
            self.items[item] = {s: v["title"] for s, v in links.items()}
            return {"entity": {"id": item}}
        return {"entity": {"id": p.get("id", "P900")}}

    def do_wbcreateclaim(self, lang, p):
        self.claims.setdefault((p["entity"], p["property"]), []).append(json.loads(p["value"]))
        return {"success": 1}

    def do_wbsetsitelink(self, lang, p):
        self.items.setdefault(p["id"], {})[p["linksite"]] = p["linktitle"]
        return {"success": 1}

    # ---- what the tests look at ----

    @staticmethod
    def is_entity(p):
        return p["action"] in ENTITY_ACTIONS or p.get("list") == "usercontribs" or (
            p["action"] == "query" and "revids" in p)

    def entity_wikis(self):
        return {wiki for wiki, p in self.log if self.is_entity(p)}

    def page_wikis(self):
        return {wiki for wiki, p in self.log
                if not self.is_entity(p) and p["action"] != "login" and p.get("meta") != "tokens"}

    def logins(self):
        return [wiki for wiki, p in self.log if p["action"] == "login"]

    def did(self, action, wiki=None):
        return [p for w, p in self.log if p["action"] == action and wiki in (None, w)]


class Wikidata:
    resolved = {}

    def by_sitelink(self, site, titles):
        return {}

    def prefetch(self, qids):
        pass

    def search(self, text, lang):
        return []

    def entity(self, qid):
        return {"sitelinks": {"enwiki": {"title": "Film (film)"}}, "labels": {}, "claims": {}}


@pytest.fixture
def farm(monkeypatch):
    farm = Farm()
    monkeypatch.setattr(WikiClient, "_request", lambda self, m, p, b=None: farm.client_request(self, m, p, b))
    monkeypatch.setenv("SV_EDITOR_USER", "SV editor bot@mcp")
    monkeypatch.setenv("SV_EDITOR_PASSWORD", "secret")
    return farm


@pytest.fixture(params=["en", "data"])
def repo(request, monkeypatch):
    monkeypatch.setattr(sync.wiki, "REPO_WIKI", request.param)
    return request.param


# ---- the runs, also used to compare the two repositories ----

def run_file_items(tmp_path):
    from tools import file_items
    assert file_items.main(["--wiki", "en,it", "--apply"], classify=lambda text: None) == 0


def run_file_items_it(tmp_path):
    from tools import file_items
    assert file_items.main(["--wiki", "it", "--apply"], classify=lambda text: None) == 0


def run_connect_items(tmp_path):
    from tools import connect_items
    assert connect_items.main(["--wiki", "en", "--apply", "--only", "en:New", "--create", "en:New=a page"],
                              wikidata=Wikidata(), classify=lambda text: None) == 0


def run_switch_infobox(tmp_path):
    from tools import switch_infobox
    assert switch_infobox.main(["--wiki", "en", "--apply", "--progress", str(tmp_path / "p.json")],
                               wikidata=Wikidata()) == 0


def run_fix_sitelinks(tmp_path):
    from tools import fix_sitelinks
    assert fix_sitelinks.main(["--apply"]) == 0


def run_categories(wiki):
    def run(tmp_path):
        from tools import categories
        node = tree.nodes(tree.load(ROOT))[1]
        data = {"root": {"names": dict.fromkeys(LANGS, "Root")},
                "themes_root": {"names": dict.fromkeys(LANGS, "Themes")},
                "fields": [{"key": node.key, "names": node.names}], "themes": []}
        categories.main(["check", "--wiki", wiki, "--create"], data=data)
    return run


def run_record_items(tmp_path):
    from tools import categories
    categories.main(["record-items"], path=tmp_path / "c.json")


RUNS = {"file_items": run_file_items, "file_items it": run_file_items_it, "connect_items": run_connect_items,
        "switch_infobox": run_switch_infobox, "fix_sitelinks": run_fix_sitelinks,
        "categories en": run_categories("en"), "categories fr": run_categories("fr"),
        "record_items": run_record_items}


@pytest.mark.parametrize("name", RUNS)
def test_tools_take_entities_from_the_repository_and_pages_from_their_wikis(name, farm, repo, tmp_path):
    RUNS[name](tmp_path)
    assert farm.entity_wikis() == {repo}
    assert "data" not in farm.page_wikis()


@pytest.mark.parametrize("name", RUNS)
def test_only_the_host_of_entity_requests_depends_on_the_repository(name, monkeypatch, tmp_path):
    """Same page requests on the same wikis, same entity requests (data, summaries, tags) on the repository."""
    monkeypatch.setenv("SV_EDITOR_USER", "u")
    monkeypatch.setenv("SV_EDITOR_PASSWORD", "p")
    logs = {}
    for where in ("en", "data"):
        farm = Farm()
        monkeypatch.setattr(WikiClient, "_request", lambda self, m, p, b=None, f=farm: f.client_request(self, m, p, b))
        monkeypatch.setattr(sync.wiki, "REPO_WIKI", where)
        (tmp_path / where).mkdir()
        RUNS[name](tmp_path / where)
        logs[where] = ([(w, p) for w, p in farm.log if not farm.is_entity(p) and p["action"] != "login"
                        and p.get("meta") != "tokens"], [p for _, p in farm.log if farm.is_entity(p)])
    assert logs["data"] == logs["en"] and logs["en"][1]


def test_file_items_writes_statements_on_the_repository_and_edits_the_page(farm, repo, tmp_path):
    run_file_items(tmp_path)
    claims = farm.did("wbcreateclaim", repo)
    assert claims and {(c["tags"], c["summary"]) for c in claims} == {
        ("seminaverbi-mcp", json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8"))["file_summary"])}
    edits = farm.did("edit", "en")
    assert [e["title"] for e in edits] == ["Film"] and farm.did("edit", "it") and edits[0]["tags"] == "seminaverbi-mcp"


def test_connect_items_creates_on_the_repository_and_files_the_en_page(farm, repo, tmp_path):
    run_connect_items(tmp_path)
    (new,) = farm.did("wbeditentity", repo)
    assert json.loads(new["data"])["sitelinks"] == {"en": {"site": "en", "title": "New"}}  # the site ID stays "en"
    assert [e["title"] for e in farm.did("edit", "en")] == ["New"]


def test_switch_infobox_writes_the_statement_on_the_repository_and_saves_the_en_page(farm, repo, tmp_path):
    run_switch_infobox(tmp_path)
    assert [c["property"] for c in farm.did("wbcreateclaim", repo)] == [P["publication_date"]]
    assert [e["title"] for e in farm.did("edit", "en")] == ["Film"]


def test_fix_sitelinks_checks_the_page_on_en(farm, repo, tmp_path):
    run_fix_sitelinks(tmp_path)
    assert "en" in farm.page_wikis() and len(farm.did("wbeditentity", repo)) == 1
    assert farm.logins() == [repo]


@pytest.mark.parametrize("name, wikis", [
    ("file_items", ["en", "it"]), ("file_items it", ["it"]), ("connect_items", LANGS), ("switch_infobox", ["en"]),
    ("categories en", ["en"]), ("categories fr", ["fr"])])
def test_every_wiki_that_is_written_to_gets_one_login(name, wikis, farm, repo, tmp_path):
    RUNS[name](tmp_path)
    assert farm.logins() == [*wikis, *([repo] if repo not in wikis else [])]


def test_properties_and_labels_come_from_the_repository(farm, repo, monkeypatch):
    from tools import jev, properties
    assert properties.main([]) == 0
    assert jev.item_labels(["Q100"]) == {"Q100": "Q100"}
    assert farm.entity_wikis() == {repo} and not farm.page_wikis() and not farm.logins()
    seen = []
    monkeypatch.setattr(properties, "ensure", lambda client, apply: seen.append(client.lang) or {
        "sv_field": "P1", "sv_theme": "P2"})
    assert properties.main(["--apply"]) == 0
    assert seen == farm.logins() == [repo]


def test_the_printed_server_commands_run_on_the_repository(repo):
    from tools.imports import enrich_command, import_command
    assert import_command(["Q5"], "/t") == f"php maintenance/run.php /t/EnrichItems.php --wiki={repo} --import Q5"
    assert enrich_command(["Q5"], "/t") == f"php maintenance/run.php /t/EnrichItems.php --wiki={repo} --item Q5"


# ---- the pipeline ----

ROW = SimpleNamespace(qid="Q1", labels={"en": "Film"}, sitelinks={"enwiki": "Film (film)"}, calibration=False,
                      status=None, page=None,
                      data={0: {}, 5: {"connection": {"choice": "direct"}, "field": {}, "themes": []}})


def test_pipeline_coverage(farm, repo):
    from pipeline import __main__ as cli, load_config
    covered = cli._stage_functions(cli.DOMAINS["film"], None, load_config())[1]
    assert covered(ROW)[1]["have"] == ["en", "it"]
    assert farm.entity_wikis() == {repo} and not farm.page_wikis()


def test_pipeline_coverage_reads_the_title_on_en(farm, repo):
    from pipeline import __main__ as cli, load_config
    del farm.items["Q100"]["en"]
    cli._stage_functions(cli.DOMAINS["film"], None, load_config())[1](ROW)
    assert farm.entity_wikis() == {repo} and "en" in farm.page_wikis() and "data" not in farm.page_wikis()


def test_pipeline_candidates_recheck_and_release(farm, repo, monkeypatch, tmp_path):
    from pipeline import __main__ as cli
    seen = {}
    monkeypatch.setattr(cli.DOMAINS["music"], "candidates", lambda queue, sparql, wikipedia, cfg, wikidata=None,
                        covered=None: covered(ROW) and 0)
    monkeypatch.setattr(cli, "_sparql", lambda: None)
    monkeypatch.setattr(cli, "_wikidata", lambda: None)
    monkeypatch.setattr(cli, "_wikipedia", lambda lang, project="wikipedia": None)
    monkeypatch.setattr(cli, "release_waiting", lambda press, queue, domain, client, wikis, wikidata, sparql:
                        seen.update(repo=client.lang, wikis=[c.lang for c in wikis.values()]))
    monkeypatch.setattr(cli.PressStore, "of", lambda queue: SimpleNamespace(
        mentions=lambda **kw: [SimpleNamespace(domain="waiting:music")]))
    assert cli.main(["candidates", "--domain", "music", "--db", str(tmp_path / "q.sqlite")]) == 0
    assert farm.entity_wikis() == {repo}
    assert seen == {"repo": repo, "wikis": list(LANGS)}


@pytest.mark.parametrize("action", ["run", "route", "enrich"])
@pytest.mark.parametrize("dry", [True, False])
def test_press_stages_get_the_repository_and_the_six_wikis(action, dry, farm, repo, monkeypatch, tmp_path):
    from pipeline import __main__ as cli
    repos, editors = [], []
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    for name in ("_sparql", "_wikidata"):
        monkeypatch.setattr(cli, name, lambda: None)
    monkeypatch.setattr(cli, "_press_client", lambda pcfg, store: None)
    monkeypatch.setattr(cli, "_press_judge", lambda queue: None)
    for name in ("run_discover", "run_filter", "run_link"):
        monkeypatch.setattr(cli.press_stages, name, lambda *a: {})
    monkeypatch.setattr(cli.press_stages, "preview", lambda store, client, wikis, *a: repos.append(client.lang) or [
        str(list(wikis))])
    monkeypatch.setattr(cli, "route", lambda store, queue, client, wikis, *a: repos.append(client.lang) or {})
    monkeypatch.setattr(cli.press_stages, "titles", lambda store, client, wikidata: repos.append(client.lang) or {})
    monkeypatch.setattr(cli, "enrichment", lambda *a: "page")
    monkeypatch.setattr(cli, "publish_enrichment", lambda store, editor, *a: editors.append(editor.lang))
    assert cli.main(["press", action, *(["--dry-run"] if dry else []), "--db", str(tmp_path / "q.sqlite")]) == 0
    assert set(repos) == {repo}
    assert farm.logins() == ([] if dry or action == "route" else ["en"])  # pages are saved on en
    assert editors == ([] if dry or action == "route" else ["en"])
    assert farm.page_wikis() <= {"en"} and not farm.entity_wikis()


def test_calibration_reads_items_on_the_repository_and_categories_on_the_six_wikis(farm, repo):
    from pipeline import calibration
    from pipeline.domains import common
    wikis = {lang: WikiClient(lang) for lang in LANGS}
    data = {"root": {"names": {}}, "fields": [{"key": "film", "names": dict.fromkeys(LANGS, "Film")}],
            "themes_root": {"names": {}}, "themes": []}
    wikidata = SimpleNamespace(by_sitelink=lambda site, titles: {t: "Q500" for t in titles})
    assert common.calibration_qids(wikis, wikidata, data, {"film"}) == (["Q500"], [])
    assert farm.page_wikis() == set(LANGS) and farm.entity_wikis() == {repo}
    queue = SimpleNamespace(calibration_rows=lambda: [ROW])
    farm.log.clear()
    assert "| item |" not in calibration.report(queue, wikis, tree=data)
    assert farm.entity_wikis() == {repo} and not farm.page_wikis()


# ---- the setting ----

def test_the_repository_is_the_configured_wiki():
    config = json.loads((ROOT / "config" / "wikis.json").read_text(encoding="utf-8"))
    assert sync.wiki.REPO_WIKI == config["repository"] == "data"
    assert WikiClient(sync.wiki.REPO_WIKI).api == "https://data.seminaverbi.bibleget.io/w/api.php"


@pytest.mark.parametrize("value", ["", "it", "www.example.org/", None])
def test_another_repository_value_is_refused(value, tmp_path):
    path = tmp_path / "wikis.json"
    path.write_text(json.dumps({"repository": value}), encoding="utf-8")
    with pytest.raises(ValueError, match="repository"):
        sync.wiki.load_repo_wiki(path)


def test_the_en_client_is_the_repository_client_only_while_en_is_the_repository(monkeypatch):
    clients = {lang: WikiClient(lang) for lang in LANGS}
    monkeypatch.setattr(sync.wiki, "REPO_WIKI", "en")
    assert sync.wiki.repo_client(clients) is clients["en"]
    monkeypatch.setattr(sync.wiki, "REPO_WIKI", "data")
    assert sync.wiki.repo_client(clients).lang == "data" and "data" not in clients


def test_data_is_never_a_page_language():
    import pipeline.__main__
    import pipeline.domains.common
    import pipeline.publish
    import pipeline.stages
    import tools.migrate_categories
    for langs in (LANGS, pipeline.__main__.LANGS, pipeline.stages.LANGS, pipeline.publish.LANGS,
                  pipeline.domains.common.LANGS, tools.migrate_categories.WIKIS):
        assert "data" not in langs
    for lang in LANGS:
        assert not any("wikis" in title for title in desired_pages(ROOT, lang, "x"))


@pytest.mark.parametrize("module, argv", [
    ("sync.__main__", ["--wiki", "data"]), ("tools.categories", ["check", "--wiki", "data"]),
    ("tools.file_items", ["--wiki", "data"]), ("tools.connect_items", ["--wiki", "data"]),
    ("tools.switch_infobox", ["--wiki", "data"]), ("tools.cleanup_articles", ["--wiki", "data"]),
    ("tools.migrate_categories", ["--wiki", "data"])])
def test_data_is_refused_as_a_page_wiki(module, argv, farm, monkeypatch):
    import importlib
    monkeypatch.setattr(sync.wiki, "REPO_WIKI", "data")
    monkeypatch.setenv("SV_SYNC_USER", "u")
    monkeypatch.setenv("SV_SYNC_PASSWORD", "p")
    with pytest.raises(SystemExit) as exc:
        importlib.import_module(module).main(argv)
    assert exc.value.code == 2 and farm.log == []


def test_mcp_config_has_the_data_wiki_like_the_others():
    config = json.loads((ROOT / "mcp" / "config.json").read_text(encoding="utf-8"))
    assert config["defaultWiki"] == "en"
    assert list(config["wikis"]) == [*LANGS, "data"]
    own = {"sitename", "server"}
    shared = {k: v for k, v in config["wikis"]["en"].items() if k not in own}
    for key, wiki in config["wikis"].items():
        assert wiki["server"] == f"https://{key}{DOMAIN}" and wiki["articlepath"] == "/wiki"
        assert {k: v for k, v in wiki.items() if k not in own} == shared
