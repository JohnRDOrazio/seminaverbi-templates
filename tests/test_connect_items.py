import pytest

from tools import connect_items as ci
from tools.connect_items import Article, Connect


def ent(qid, p31=(), p279=(), sitelinks=None, labels=None, descriptions=None):
    def claims(values):
        return [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": q}, "type": "wikibase-entityid"}}}
                for q in values]
    return {"id": qid, "claims": {"P31": claims(p31), "P279": claims(p279)},
            "sitelinks": {s: {"site": s, "title": t} for s, t in (sitelinks or {}).items()},
            "labels": {lang: {"language": lang, "value": v} for lang, v in (labels or {}).items()},
            "descriptions": {lang: {"language": lang, "value": v} for lang, v in (descriptions or {}).items()}}


class Wikidata:
    def __init__(self, links=None, entities=None, hits=None):
        self.links, self.ents, self.hits = links or {}, entities or {}, hits or {}
        self.resolved = {}

    def by_sitelink(self, site, titles):
        return {t: self.links[site, t] for t in titles if (site, t) in self.links}

    def prefetch(self, qids):
        pass

    def entity(self, qid):
        return self.ents[qid]

    def search(self, text, lang):
        return list(self.hits.get((text, lang), []))


class Repo:
    def __init__(self, links=None):
        self.links = {item: dict(s) for item, s in (links or {}).items()}  # item -> {site: title}
        self.calls, self.next_id, self.summaries = [], 48000, []
        self.revs = {item: 1 for item in self.links}  # item -> its latest revision

    def item_sitelinks(self, item):
        return self.revs.get(item), dict(self.links.get(item, {}))

    def items_for(self, site, titles):
        out = {}
        for item, sites in self.links.items():
            if sites.get(site) in titles:
                out[sites[site]] = item
        return out

    def sitelinks(self, items):
        return {i: dict(self.links[i]) for i in items if self.links.get(i)}

    def set_sitelink(self, item, site, title, tags=None, summary=None, baserevid=None):
        if baserevid is not None and baserevid != self.revs.get(item):
            raise ci.SyncError(f"en: editconflict: {item} changed")
        self.summaries.append(summary)
        self.calls.append(("sitelink", item, site, title))
        self.links.setdefault(item, {})[site] = title
        self.revs[item] = self.revs.get(item, 0) + 1

    def new_item(self, labels, descriptions, sitelinks, tags=None, summary=None):
        self.summaries.append(summary)
        self.next_id += 1
        item = f"Q{self.next_id}"
        self.calls.append(("new", item, labels, descriptions, sitelinks))
        self.links[item] = dict(sitelinks)
        self.revs[item] = 1
        return item


class Client:
    def __init__(self, lang, repo, pages, main="Main Page"):
        self.lang, self.repo, self.pages, self.main = lang, repo, pages, main

    def articles(self):
        return list(self.pages)

    def revision(self, title):
        return 1, self.pages[title]

    def main_page(self):
        return self.main

    def first_comment(self, title):
        return ""


def plan(articles, repo, wikidata, existing=None, **kw):
    return ci.plan(articles, repo, wikidata, existing or {}, **kw)


def by_title(rows):
    return {r.title: r for r in rows}


SONG = "{{Infobox song\n| name = X\n}}\nText."
ALBUM = "{{Infobox album\n| name = X\n}}\nText."


def test_excluded_pages_are_listed_not_connected(capsys):
    repo = Repo()
    rows = plan([Article("it", "JavaScript", "x"), Article("en", "Draft:Sandbox", "x"),
                 Article("it", "Progetto:Fumetti/Citare fumetti/Esempi", "x")], repo, Wikidata())
    assert [r.action for r in rows] == ["skip-excluded"] * 3
    assert ci.run(rows, repo, apply=True) == 0
    assert repo.calls == []
    out = capsys.readouterr().out
    assert "Pages to delete" in out and "it:JavaScript" in out and "en:Draft:Sandbox" in out


def test_main_pages_join_q4(capsys):
    repo = Repo({"Q4": {"en": "Main Page"}})
    rows = plan([Article("es", "Página principal", "x", main=True)], repo, Wikidata())
    assert rows == [Connect("es", "Página principal", "main-page", None, "Q4", "")]
    ci.run(rows, repo, apply=True)
    assert repo.calls == [("sitelink", "Q4", "es", "Página principal")]


def test_leftover_main_page_is_not_linked_over_the_real_one():
    # es/fr/de/pt carry MediaWiki's default "Main Page" next to their real main page, already on Q4
    repo = Repo({"Q4": {"en": "Main Page", "es": "Página principal"}})
    (row,) = plan([Article("es", "Main Page", "x")], repo, Wikidata())
    assert row.action == "skip-excluded" and "Página principal" in row.reason
    ci.run([row], repo, apply=True)
    assert repo.calls == []


def test_translation_joins_the_existing_item():
    wd = Wikidata(links={("itwiki", "David Bowie"): "Q5383", ("enwiki", "David Bowie"): "Q5383"},
                  entities={"Q5383": ent("Q5383", p31=["Q5"], sitelinks={"enwiki": "David Bowie"})})
    repo = Repo({"Q46886": {"en": "David Bowie", "enwiki": "David Bowie", "itwiki": "David Bowie"}})
    existing = ci.known(repo, wd, ["Q46886"])
    assert existing == {"Q5383": ("Q46886", {"en": "David Bowie"})}
    (row,) = plan([Article("it", "David Bowie", "{{Bio\n|Nome = David\n}}")], repo, wd, existing)
    assert (row.action, row.wd, row.local) == ("join", "Q5383", "Q46886")
    ci.run([row], repo, apply=True)
    assert repo.calls == [("sitelink", "Q46886", "it", "David Bowie")]


def test_wikipedia_title_gives_the_item():
    wd = Wikidata(links={("enwiki", "Baba Yetu"): "Q790484"},
                  entities={"Q790484": ent("Q790484", p31=["Q7366"], sitelinks={"enwiki": "Baba Yetu"})})
    (row,) = plan([Article("en", "Baba Yetu", SONG)], Repo(), wd)
    assert (row.action, row.wd, row.local) == ("import", "Q790484", None)


def test_kind_filter_drops_the_album_for_a_song_page():
    wd = Wikidata(links={("enwiki", "Hear Me Lord"): "Q100"},
                  entities={"Q100": ent("Q100", p31=["Q482994"], labels={"en": "Hear Me Lord"}),
                            "Q200": ent("Q200", p31=["Q7366"], labels={"en": "Hear Me Lord"},
                                        descriptions={"en": "song by George Harrison"})},
                  hits={("Hear Me Lord", "en"): ["Q100", "Q200"]})
    (row,) = plan([Article("en", "Hear Me Lord", SONG)], Repo(), wd)
    assert row.action == "author" and row.wd is None
    assert "Q200" in row.reason and "song by George Harrison" in row.reason
    assert "Q100" not in row.reason.split("candidates:")[1]
    # the same item is right for an album page
    (row,) = plan([Article("en", "Hear Me Lord", ALBUM)], Repo(), wd)
    assert (row.action, row.wd) == ("import", "Q100")


def test_class_item_passes_the_kind_filter():
    # Easter egg is a class: no P31, only P279; it is itself listed in kinds.work
    wd = Wikidata(links={("enwiki", "Easter egg"): "Q163886", ("enwiki", "Easter bread"): "Q267726"},
                  entities={"Q163886": ent("Q163886", p279=["Q12345"]),
                            "Q267726": ent("Q267726", p279=["Q7802"])})
    food = "{{Infobox food\n| name = X\n}}"
    rows = by_title(plan([Article("en", "Easter egg", food), Article("en", "Easter bread", food)], Repo(), wd))
    assert rows["Easter egg"].action == "import" and rows["Easter bread"].action == "import"


def test_two_candidates_go_to_the_author():
    wd = Wikidata(entities={"Q1": ent("Q1", p31=["Q7366"]), "Q2": ent("Q2", p31=["Q7366"])},
                  hits={("Holy Mother", "en"): ["Q1", "Q2"]})
    (row,) = plan([Article("en", "Holy Mother (August album)", SONG)], Repo(), wd)
    assert row.action == "author" and "Q1" in row.reason and "Q2" in row.reason


def test_author_decision_forces_the_item():
    wd = Wikidata(entities={"Q2": ent("Q2", p31=["Q7366"])}, hits={("Holy Mother", "en"): ["Q1", "Q2"]})
    (row,) = plan([Article("en", "Holy Mother (August album)", SONG)], Repo(), wd,
                  forced={("en", "Holy Mother (August album)"): "Q2"})
    assert (row.action, row.wd) == ("import", "Q2")


def test_mapped_wikidata_item_is_used_not_imported():
    wd = Wikidata(links={("enwiki", "Christmas tree"): "Q47128"},
                  entities={"Q47128": ent("Q47128", sitelinks={"enwiki": "Christmas tree"})})
    repo = Repo({"Q47700": {"enwiki": "Christmas tree"}})
    (row,) = plan([Article("en", "Christmas tree", "Text.")], repo, wd)
    assert (row.action, row.wd, row.local) == ("local", "Q47128", "Q47700")
    filed = []
    ci.run([row], repo, apply=True, file_item=filed.append)
    assert repo.calls == [("sitelink", "Q47700", "en", "Christmas tree")] and filed == ["Q47700"]


def test_item_already_linked_on_the_same_wiki_goes_to_the_author():
    wd = Wikidata(links={("enwiki", "Star Wars"): "Q462"},
                  entities={"Q462": ent("Q462", sitelinks={"enwiki": "Star Wars"})})
    repo = Repo({"Q4102": {"it": "Guerre Stellari", "enwiki": "Star Wars"}})
    existing = {"Q462": ("Q4102", {"it": "Guerre Stellari"})}
    (row,) = plan([Article("it", "Star Wars", "Text.")], repo,
                  Wikidata(links={("itwiki", "Star Wars"): "Q462"}, entities=wd.ents), existing)
    assert row.action == "author" and "it:Guerre Stellari" in row.reason


def test_same_item_twice_on_one_wiki_goes_to_the_author():
    wd = Wikidata(links={("itwiki", "A"): "Q9", ("itwiki", "B"): "Q9"}, entities={"Q9": ent("Q9")})
    rows = plan([Article("it", "A", "x"), Article("it", "B", "x")], Repo(), wd)
    assert [r.action for r in rows] == ["author", "author"]


def test_no_wikidata_item_means_create():
    title = "Polittico dell'Agnello Mistico"
    repo = Repo()
    (row,) = plan([Article("it", title, "Testo.")], repo, Wikidata())
    assert (row.action, row.wd, row.local) == ("create", None, None)
    ci.run([row], repo, apply=True)  # proposed only: the author confirms with --create
    assert repo.calls == []
    filed = []
    assert ci.run([row], repo, apply=True, create={("it", title): "polittico di Jan van Eyck"},
                  file_item=filed.append) == 0
    assert repo.calls == [("new", "Q48001", {"it": title}, {"it": "polittico di Jan van Eyck"}, {"it": title})]
    assert filed == ["Q48001"]


def test_dry_run_writes_nothing():
    wd = Wikidata(links={("enwiki", "Christmas tree"): "Q47128"},
                  entities={"Q47128": ent("Q47128", sitelinks={"enwiki": "Christmas tree"})})
    repo = Repo({"Q47700": {"enwiki": "Christmas tree"}})
    rows = plan([Article("en", "Christmas tree", "Text.")], repo, wd)
    assert ci.run(rows, repo, apply=False, create={("en", "Christmas tree"): "tree"}) == 0
    assert repo.calls == []


def test_import_needed_without_log_prints_the_command_and_stops(capsys):
    rows = [Connect("en", "Baba Yetu", "import", "Q790484", None, ""),
            Connect("it", "Hear Me Lord", "import", "Q7339", None, ""),
            Connect("en", "Hear Me Lord", "import", "Q7339", None, ""),
            Connect("en", "Christmas tree", "local", "Q47128", "Q47700", "")]
    repo = Repo()
    assert ci.run(rows, repo, apply=True, tools_dir="/srv/tools") == 3
    assert repo.calls == []
    out = capsys.readouterr().out
    assert "php maintenance/run.php /srv/tools/EnrichItems.php --wiki=en --import Q790484 --import Q7339\n" in out


def test_import_output_parsed(capsys):
    log = ("IMPORT Q7339 = Q48001 (created)\nIMPORT Q790484 = Q47900 (mapped)\n"
           "IMPORT Q9 FAILED not found on Wikidata\nsomething else\n")
    assert ci.parse_import_log(log) == ({"Q7339": "Q48001", "Q790484": "Q47900"}, {"Q9": "not found on Wikidata"})
    rows = [Connect("it", "Hear Me Lord", "import", "Q7339", None, ""),
            Connect("en", "Bad", "import", "Q9", None, ""),
            Connect("en", "Missing", "import", "Q77", None, ""),
            Connect("en", "Baba Yetu", "import", "Q790484", None, "")]
    repo, filed = Repo(), []
    assert ci.run(rows, repo, apply=True, import_log=log, file_item=filed.append) == 1
    assert repo.calls == [("sitelink", "Q48001", "it", "Hear Me Lord"), ("sitelink", "Q47900", "en", "Baba Yetu")]
    assert filed == ["Q48001", "Q47900"]
    out = capsys.readouterr().out
    assert "en:Bad: FAILED import Q9: not found on Wikidata" in out
    assert "en:Missing: FAILED import Q77: not in the import log" in out


def test_second_run_plans_nothing():
    repo = Repo({"Q4": {"en": "Main Page"}, "Q46886": {"en": "David Bowie", "enwiki": "David Bowie"}})
    wd = Wikidata(links={("itwiki", "David Bowie"): "Q5383", ("enwiki", "David Bowie"): "Q5383",
                         ("enwiki", "Christmas tree"): "Q47128"},
                  entities={"Q5383": ent("Q5383", p31=["Q5"]), "Q47128": ent("Q47128")})
    clients = {"en": Client("en", repo, {"Main Page": "", "David Bowie": "", "Christmas tree": "Text.",
                                          "Draft:Sandbox": ""}),
               "it": Client("it", repo, {"Pagina principale": "", "David Bowie": "{{Bio}}"},
                            main="Pagina principale")}

    def once(log=None):
        articles, connected = ci.gather(clients, repo)
        rows = ci.plan(articles, repo, wd, ci.known(repo, wd, connected))
        code = ci.run(rows, repo, apply=True, import_log=log)
        return rows, code

    rows, code = once()
    assert code == 3  # Christmas tree needs an import
    rows, code = once("IMPORT Q47128 = Q48005 (created)\n")
    assert code == 0 and {r.action for r in rows} == {"skip-excluded", "join", "import", "main-page"}
    rows, code = once()
    assert code == 0 and [(r.title, r.action) for r in rows] == [("Draft:Sandbox", "skip-excluded")]


def test_page_kind():
    conf = ci.load_config()
    assert ci.page_kind("en", "<!-- {{Infobox film}} -->\n{{Short description|x}}{{infobox_song|a=b}}", conf) == "song"
    assert ci.page_kind("it", "{{Bio\n|Nome=X}}", conf) == "person"
    assert ci.page_kind("it", "{{Template:Brano musicale|x}}", conf) == "song"
    assert ci.page_kind("en", "Text.", conf) is None


def test_italian_album_box_of_a_single_is_a_song():
    conf = ci.load_config()
    assert ci.page_kind("it", "{{Album\n|titolo = My Sweet Lord\n|tipo = Singolo\n}}", conf) == "song"
    assert ci.page_kind("it", "{{Album\n|titolo = August\n|tipo = Studio\n}}", conf) == "album"
    assert ci.page_kind("it", "{{Album\n|titolo = August\n}}", conf) == "album"


def test_two_part_episode_is_a_tv_kind():
    kinds = ci.load_config()["kinds"]
    assert "Q21664088" in kinds["tv"]


def test_cli_decisions_resolve_titles():
    articles = [Article("en", "Hear Me Lord", ""), Article("it", "Hear Me Lord", ""), Article("it", "Nek", "")]
    assert ci.decisions(["Nek=Q45036", "en:Hear Me Lord=Q7339"], articles, "qid") == {
        ("it", "Nek"): "Q45036", ("en", "Hear Me Lord"): "Q7339"}
    with pytest.raises(ValueError, match="both"):
        ci.decisions(["Hear Me Lord=Q7339"], articles, "qid")
    with pytest.raises(ValueError, match="not an unconnected"):
        ci.decisions(["Nobody=Q1"], articles, "qid")
    assert ci.decisions(["it:Nek=cantautore italiano"], articles, "description") == {
        ("it", "Nek"): "cantautore italiano"}
    with pytest.raises(ValueError, match="description"):
        ci.decisions(["it:Nek"], articles, "description")


def test_main_wires_the_cli(tmp_path, capsys):
    repo = Repo({"Q4": {"en": "Main Page"}})
    wd = Wikidata(links={("enwiki", "Christmas tree"): "Q47128"}, entities={"Q47128": ent("Q47128")})
    clients = {"en": Client("en", repo, {"Main Page": "", "Christmas tree": "Text.", "Nek": "x"})}
    assert ci.main(["--apply", "--tools-dir", "/t", "--create", "Nek=singer"], repo=repo, clients=clients,
                   wikidata=wd) == 3
    assert "/t/EnrichItems.php --wiki=en --import Q47128" in capsys.readouterr().out
    log = tmp_path / "import.log"
    log.write_text("IMPORT Q47128 = Q48100 (created)\n", encoding="utf-8")
    filed = []
    code = ci.main(["--apply", "--import-log", str(log), "--create", "Nek=singer"], repo=repo, clients=clients,
                   wikidata=wd, file_item=filed.append)
    assert code == 0 and repo.links["Q48100"] == {"en": "Christmas tree"} and filed == ["Q48100", "Q48001"]
    with pytest.raises(SystemExit):
        ci.main(["--wd", "Nobody=Q1"], repo=repo, clients=clients, wikidata=wd)
    with pytest.raises(SystemExit):  # a created item needs a description
        ci.main(["--create", "Nek"], repo=repo, clients=clients, wikidata=wd)


def test_created_label_drops_the_disambiguator():
    title = "Il Miglio Verde (film)"
    repo = Repo()
    rows = [Connect("it", title, "create", None, None, "")]
    assert ci.run(rows, repo, apply=True, create={("it", title): "film del 1999"}) == 0
    assert repo.calls == [("new", "Q48001", {"it": "Il Miglio Verde"}, {"it": "film del 1999"}, {"it": title})]


def test_writes_carry_the_connect_summary():
    repo = Repo({"Q47700": {"enwiki": "Christmas tree"}})
    rows = [Connect("en", "Christmas tree", "local", "Q47128", "Q47700", ""),
            Connect("it", "Nek", "create", None, None, "")]
    ci.run(rows, repo, apply=True, create={("it", "Nek"): "cantautore"})
    assert repo.summaries == ["Connect the article to its item"] * 2


FILM = "{{Film\n|titolo italiano = Il miglio verde\n}}"
GREEN_MILE = {"Q208263": ent("Q208263", p31=["Q11424"]), "Q1": ent("Q1", p31=["Q482994"])}


def test_interlanguage_link_gives_the_item():
    wd = Wikidata(links={("enwiki", "The Green Mile (film)"): "Q208263"}, entities=GREEN_MILE)
    (row,) = plan([Article("it", "Il Miglio Verde (film)", FILM + "\n[[en:The Green Mile (film)]]")], Repo(), wd)
    assert (row.action, row.wd) == ("import", "Q208263") and "enwiki:The Green Mile (film)" in row.reason


def test_interlanguage_link_goes_through_the_kind_filter():
    wd = Wikidata(links={("enwiki", "The Green Mile (soundtrack)"): "Q1"}, entities=GREEN_MILE)
    (row,) = plan([Article("it", "Il Miglio Verde (film)", FILM + "\n[[en:The Green Mile (soundtrack)]]")],
                  Repo(), wd)
    assert row.action == "author" and row.wd is None


def test_import_summary_naming_a_wikipedia_page_gives_the_item():
    wd = Wikidata(links={("itwiki", "Il miglio verde (film)"): "Q208263"}, entities=GREEN_MILE)
    art = Article("it", "Il Miglio Verde (film)", FILM, comment="Imported from wikipedia:it:Il miglio verde (film)")
    (row,) = plan([art], Repo(), wd)
    assert (row.action, row.wd) == ("import", "Q208263") and "import summary" in row.reason
    assert ci.summary_page("[[w:it:Il miglio verde (film)]]") == ("itwiki", "Il miglio verde (film)")
    assert ci.summary_page("Importata da [[wikipedia:Pulp Fiction]]") == ("enwiki", "Pulp Fiction")
    for vague in ("1 revision imported", "/* Plot */ fix", "[[wikipedia:A]] and [[wikipedia:B]]", ""):
        assert ci.summary_page(vague) is None


def test_import_summary_must_be_a_whole_page_import():
    # whole-page imports, as people and Special:Import write them
    for whole, page in (("Imported from wikipedia:it:Il miglio verde (film)", ("itwiki", "Il miglio verde (film)")),
                        ("Importata da [[wikipedia:Pulp Fiction]]", ("enwiki", "Pulp Fiction")),
                        ("1 revision imported from [[:wikipedia:Pulp Fiction]]", ("enwiki", "Pulp Fiction")),
                        ("3 revisioni importate da [[w:it:Nek]].", ("itwiki", "Nek")),
                        ("Import from w:Pulp Fiction", ("enwiki", "Pulp Fiction"))):
        assert ci.summary_page(whole) == page, whole
    # a summary that took only part of a page does not name the article's subject
    for partial in ("Imported a comparison from [[wikipedia:Unrelated film]]",
                    "Imported the plot section from [[wikipedia:Unrelated film]]",
                    "Added a table, imported from [[wikipedia:Unrelated film]]",
                    "Imported from [[wikipedia:Unrelated film]] (cast list only)"):
        assert ci.summary_page(partial) is None, partial


def test_body_wikidata_link_is_not_the_pages_item():
    wd = Wikidata(entities=GREEN_MILE)
    (row,) = plan([Article("it", "Il Miglio Verde (film)", FILM + "\nVedi [[d:Q208263]].")], Repo(), wd)
    assert row.action == "create"
    (row,) = plan([Article("it", "Il Miglio Verde (film)", FILM + "\n{{Interprogetto|wikidata=Q208263}}")],
                  Repo(), wd)
    assert (row.action, row.wd) == ("import", "Q208263")
    (row,) = plan([Article("it", "Il Miglio Verde (film)", FILM + "\n{{Collegamenti esterni|wikidata = Q208263}}")],
                  Repo(), wd)
    assert (row.action, row.wd) == ("import", "Q208263")


def test_redirect_hit_without_a_kind_goes_to_the_author():
    wd = Wikidata(links={("itwiki", "Matrix (trilogia)"): "Q13014087"},
                  entities={"Q13014087": ent("Q13014087", p31=["Q196600"], labels={"it": "Matrix"})})
    wd.resolved = {("itwiki", "Matrix (trilogia)"): "Matrix (franchise)"}
    (row,) = plan([Article("it", "Matrix (trilogia)", "Testo.")], Repo(), wd)
    assert row.action == "author" and "Q13014087" in row.reason and "Matrix (franchise)" in row.reason
    (row,) = plan([Article("it", "Matrix (trilogia)", FILM)], Repo(), wd)
    assert (row.action, row.wd) == ("import", "Q13014087")


def test_filing_failure_is_reported_and_the_run_continues(capsys):
    repo = Repo({"Q1": {"enwiki": "A"}, "Q2": {"enwiki": "B"}})
    rows = [Connect("en", "A", "local", "Q10", "Q1", ""), Connect("en", "B", "local", "Q20", "Q2", "")]
    filed = []

    def file_item(item):
        if item == "Q1":
            raise RuntimeError("Jev unavailable")
        filed.append(item)
    assert ci.run(rows, repo, apply=True, file_item=file_item) == 1
    assert [c[1] for c in repo.calls] == ["Q1", "Q2"] and filed == ["Q2"]
    out = capsys.readouterr().out
    assert "en:A: FILE-FAILED Q1: Jev unavailable" in out and "linked 2; file-failed 1; failed 0" in out


def test_jev_client_is_made_once_and_only_when_used(monkeypatch):
    from tools import file_items, jev
    made, got = [], {}
    monkeypatch.setattr(jev, "make_client", lambda: made.append(1) or object())
    monkeypatch.setattr(jev, "classify", lambda client, text, data: "classified")
    monkeypatch.setattr(file_items, "run", lambda *a, **kw: got.update(classify=kw["classify"]) or
                        type("R", (), {"exit_code": 0})())
    file_item = ci._filer(Repo(), {}, None, print)
    file_item("Q1")
    assert made == []  # no client until a page is classified
    assert got["classify"]("a") == "classified" and got["classify"]("b") == "classified"
    assert made == [1]


def test_create_refuses_a_title_linked_since_the_plan(capsys):
    repo = Repo({"Q9": {"it": "Nek"}})
    rows = [Connect("it", "Nek", "create", None, None, "")]
    assert ci.run(rows, repo, apply=True, create={("it", "Nek"): "cantautore"}) == 1
    assert repo.calls == [] and "it:Nek: FAILED it:Nek is now linked to Q9" in capsys.readouterr().out


def test_link_refuses_a_sitelink_set_by_someone_else_after_the_check(capsys):
    class Racing(Repo):
        def item_sitelinks(self, item):
            state = super().item_sitelinks(item)
            # another editor links it:Altro to Q9 between the check and the write
            self.links[item]["it"] = "Altro"
            self.revs[item] += 1
            return state
    repo = Racing({"Q9": {"en": "Nek"}})
    rows = [Connect("it", "Nek", "join", "Q1", "Q9", "")]
    assert ci.run(rows, repo, apply=True) == 1
    assert repo.links["Q9"]["it"] == "Altro" and repo.calls == []
    assert "it:Nek: FAILED" in capsys.readouterr().out


def test_gather_fetches_text_only_for_the_wikis_used():
    repo = Repo()
    fetched = []

    class Counting(Client):
        def revision(self, title):
            fetched.append((self.lang, title))
            return super().revision(title)
    clients = {"en": Counting("en", repo, {"A": "x"}), "it": Counting("it", repo, {"B": "y"})}
    articles, _ = ci.gather(clients, repo, ["it"])
    assert fetched == [("it", "B")] and [a.title for a in articles] == ["B"]
    fetched.clear()
    articles, _ = ci.gather(clients, repo)
    assert sorted(fetched) == [("en", "A"), ("it", "B")]


def test_create_spec_may_use_an_explicit_separator():
    articles = [Article("it", "Nek", ""), Article("en", "1+1=2", "")]
    assert ci.decisions(["it:Nek=cantautore"], articles, "description") == {("it", "Nek"): "cantautore"}
    assert ci.decisions(["en:1+1=2::an equation, a=b"], articles, "description") == {
        ("en", "1+1=2"): "an equation, a=b"}
    assert ci.decisions(["it:Nek::x=y"], articles, "description") == {("it", "Nek"): "x=y"}
    with pytest.raises(ValueError, match="description"):
        ci.decisions(["en:1+1=2::"], articles, "description")


def test_interlanguage_lookups_are_batched():
    class Counting(Wikidata):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls = []

        def by_sitelink(self, site, titles):
            self.calls.append((site, list(titles)))
            return super().by_sitelink(site, titles)
    wd = Counting(links={("enwiki", f"E{n}"): f"Q{n}" for n in range(1, 4)},
                  entities={f"Q{n}": ent(f"Q{n}", p31=["Q11424"]) for n in range(1, 4)})
    arts = [Article("it", f"I{n}", FILM + f"\n[[en:E{n}]]") for n in range(1, 4)]
    rows = plan(arts, Repo(), wd)
    assert [(r.title, r.wd) for r in rows] == [("I1", "Q1"), ("I2", "Q2"), ("I3", "Q3")]
    assert sorted(wd.calls) == [("enwiki", ["E1", "E2", "E3"]), ("itwiki", ["I1", "I2", "I3"])]


def test_local_copy_whose_other_sitelink_moved_is_flagged_not_trusted():
    # the mirror's Q47700 was copied from Q1; "Foo" has since moved to Q2 on Wikidata, "Fu" has not
    wd = Wikidata(links={("enwiki", "Foo"): "Q2", ("itwiki", "Fu"): "Q1"},
                  entities={"Q2": ent("Q2", sitelinks={"enwiki": "Foo"})})
    repo = Repo({"Q47700": {"enwiki": "Foo", "itwiki": "Fu"}})
    (row,) = plan([Article("en", "Foo", "Text.")], repo, wd)
    assert row.action == "author" and row.wd == "Q2"
    assert "Q47700" in row.reason and "itwiki:Fu" in row.reason and "Q1" in row.reason
    assert repo.calls == []


def test_mirror_conflict_asks_for_the_mirror_repair_not_wd():
    wd = Wikidata(links={("enwiki", "Foo"): "Q2", ("itwiki", "Fu"): "Q1"},
                  entities={"Q2": ent("Q2", sitelinks={"enwiki": "Foo"})})
    repo = Repo({"Q47700": {"enwiki": "Foo", "itwiki": "Fu"}})
    (row,) = plan([Article("en", "Foo", "Text.")], repo, wd)
    assert "--wd" not in row.reason  # --wd reaches the same check: it cannot settle the conflict
    assert "remove" in row.reason and "Q47700" in row.reason


def test_connected_mirror_with_a_moved_sitelink_is_not_joined():
    # Q47700 already has a connected article (it:Fu), so known() lists it under Q2 from its enwiki link alone
    wd = Wikidata(links={("enwiki", "Foo"): "Q2", ("itwiki", "Fu"): "Q1"},
                  entities={"Q2": ent("Q2", sitelinks={"enwiki": "Foo"})})
    repo = Repo({"Q47700": {"enwiki": "Foo", "itwiki": "Fu", "it": "Fu"}})
    existing = ci.known(repo, wd, ["Q47700"])
    assert existing["Q2"][0] == "Q47700"
    (row,) = plan([Article("en", "Foo", "Text.")], repo, wd, existing)
    assert row.action == "author" and row.wd == "Q2" and row.local is None
    assert "Q47700" in row.reason and row.reason.count("itwiki:Fu") == 1
    ci.run([row], repo, apply=True)
    assert repo.calls == []


def test_connected_mirror_with_consistent_sitelinks_still_joins():
    wd = Wikidata(links={("enwiki", "Foo"): "Q2", ("itwiki", "Fu"): "Q2"},
                  entities={"Q2": ent("Q2", sitelinks={"enwiki": "Foo", "itwiki": "Fu"})})
    repo = Repo({"Q47700": {"enwiki": "Foo", "itwiki": "Fu", "it": "Fu"}})
    (row,) = plan([Article("en", "Foo", "Text.")], repo, wd, ci.known(repo, wd, ["Q47700"]))
    assert (row.action, row.local) == ("join", "Q47700")


def test_kinds_are_the_narrow_classes_not_bread_or_franchise_in_general():
    kinds = ci.load_config()["kinds"]
    assert "Q7802" not in kinds["work"] and "Q196600" not in kinds["film"]  # bread / media franchise at large
    assert {"Q267726", "Q163886"} <= set(kinds["work"]) and "Q13014087" in kinds["film"]
    assert ci.passes(ent("Q267726"), "Q267726", kinds["work"])  # Easter bread itself
    assert ci.passes(ent("Q13014087", p31=["Q196600"]), "Q13014087", kinds["film"])  # the Matrix franchise itself
    assert not ci.passes(ent("Q9", p279=["Q7802"]), "Q9", kinds["work"])  # some other bread
    assert not ci.passes(ent("Q8", p31=["Q196600"]), "Q8", kinds["film"])  # some other franchise


def test_group_kind_filters_a_group_layout_page():
    conf = {"templates": {"en": {"Infobox band": {"layout": "group"}}}, "bio": {}, "exclude": {},
            "kinds": ci.load_config()["kinds"]}
    wd = Wikidata(links={("enwiki", "The Beatles"): "Q1299", ("enwiki", "Beatles Fan Club"): "Q2"},
                  entities={"Q1299": ent("Q1299", p31=["Q215380"]), "Q2": ent("Q2", p31=["Q5"])})
    box = "{{Infobox band\n| name = X\n}}"
    rows = by_title(plan([Article("en", "The Beatles", box), Article("en", "Beatles Fan Club", box)], Repo(), wd,
                         conf=conf))
    assert (rows["The Beatles"].action, rows["The Beatles"].wd) == ("import", "Q1299")
    assert rows["Beatles Fan Club"].action == "author"  # a person is not a group
