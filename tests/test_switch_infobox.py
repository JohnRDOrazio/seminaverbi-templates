import pytest

from sync.wiki import SyncError
from tools import switch_infobox as si
from tools.switch_infobox import Unresolved, Value

MAPPING = si.load_mapping()
P = MAPPING["props"]
FILM, PERSON = "Q13367", "Q13121"  # local items config/infobox_types.json maps to film / person


def item_dv(q):
    return {"type": "wikibase-entityid", "value": {"entity-type": "item", "numeric-id": int(q[1:]), "id": q}}


def statement(dv):
    return {"mainsnak": {"snaktype": "value", "datavalue": dv}, "rank": "normal"}


def entity(qid, instance_of=(), labels=None, **claims):
    """A local item; claims as prop key (config/wikibase.json) -> list of datavalues."""
    out = {"id": qid, "labels": {k: {"language": k, "value": v} for k, v in (labels or {}).items()},
           "claims": {P["instance_of"]: [statement(item_dv(q)) for q in instance_of]}}
    for key, dvs in claims.items():
        out["claims"][P[key]] = [statement(dv) for dv in dvs]
    return out


def wd_entity(qid, sitelinks=None, p31=(), demonyms=None):
    claims = {"P31": [statement(item_dv(q)) for q in p31]}
    claims["P1549"] = [statement({"type": "monolingualtext", "value": {"text": t, "language": lang}})
                       for lang, t in (demonyms or [])]
    return {"id": qid, "claims": claims, "sitelinks": {s: {"site": s, "title": t} for s, t in (sitelinks or {}).items()}}


class Wikidata:
    def __init__(self, links=None, entities=None, hits=None):
        self.links, self.ents, self.hits = links or {}, entities or {}, hits or {}
        self.resolved = {}

    def by_sitelink(self, site, titles):
        return {t: self.links[site, t] for t in titles if (site, t) in self.links}

    def prefetch(self, qids):
        pass

    def entity(self, qid):
        return self.ents.get(qid, {"id": qid, "claims": {}, "sitelinks": {}})

    def search(self, text, lang):
        return list(self.hits.get((text, lang), []))


class Repo:
    def __init__(self, entities=None, links=None, refuse=()):
        self.entities = {e["id"]: e for e in (entities or [])}
        self.links = dict(links or {})  # (site, title) -> local item
        self.refuse, self.added, self.summaries = set(refuse), [], []

    def items_for(self, site, titles):
        return {t: self.links[site, t] for t in titles if (site, t) in self.links}

    def entity(self, item):
        return self.entities.setdefault(item, {"id": item, "labels": {}, "claims": {}})

    def add_claim(self, item, prop, datavalue, tags=None, summary=None):
        if prop in self.refuse:
            raise SyncError(f"en: adding {prop} to {item} failed: refused")
        assert "import" not in datavalue
        self.added.append((item, prop, datavalue))
        self.summaries.append(summary)
        self.entity(item)["claims"].setdefault(prop, []).append(statement(datavalue))


class Client:
    def __init__(self, lang, repo, pages, cats=None, images=None, render=None, preview=None):
        self.lang, self.repo, self.pages = lang, repo, dict(pages)
        self.revid = {t: 10 for t in pages}
        self.cats = cats or {}
        self.images = images or {}
        self._render = render or (lambda title, text: "")
        self._preview = preview
        self.edits, self.rendered = [], []

    def articles(self):
        return list(self.pages)

    def revision(self, title):
        return (self.revid[title], self.pages[title]) if title in self.pages else None

    def page_categories(self, title):
        return set(self.cats.get(title, set()))

    def preview_categories(self, title, text):
        return self._preview(title, text) if self._preview else set(self.cats.get(title, set()))

    def revision_categories(self, revid):
        return set().union(*self.cats.values()) if self.cats else set()

    def edit(self, title, text, summary, tags=None, createonly=False, baserevid=None):
        assert baserevid == self.revid[title]
        self.edits.append((title, text, summary))
        self.pages[title] = text
        self.revid[title] += 1
        return {"newrevid": self.revid[title]}

    def image_source(self, name):
        return self.images.get(name)

    def render(self, title, text):
        self.rendered.append(text)
        return self._render(title, text)


def resolver(lang="en", repo=None, wikidata=None, client=None):
    repo = repo or Repo()
    return si.Resolver(lang, repo, wikidata or Wikidata(), client or Client(lang, repo, {}))


def page(text, lang="en", title="Bloodshot (film)", item="Q100", label="Bloodshot"):
    return si.Page(lang, title, item, 10, text, label)


def claims_of(ent):
    return si.item_claims(ent)


FILM_TEXT = """{{Infobox film
| name = Bloodshot
| image = Bloodshot poster.jpg
| director = [[Dave Wilson (director)|Dave Wilson]]
| released = {{Film date|2020|2|21}}
| budget = $45 million
}}
'''Bloodshot''' is a 2020 film.
{{SV categories}}
"""


def film_world(**kw):
    repo = Repo([entity("Q100", [FILM], {"en": "Bloodshot"})],
                {("en", "Dave Wilson (director)"): "Q501", ("en", "Bloodshot (film)"): "Q100"}, **kw)
    client = Client("en", repo, {"Bloodshot (film)": FILM_TEXT}, images={"Bloodshot poster.jpg": "commons"})
    return repo, client


def test_infobox_film_maps_to_film_layout_and_rows():
    repo, client = film_world()
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.layout == "film" and plan.blocked is None and plan.unresolved == []
    assert [(prop, dv) for prop, dv, _ in plan.writes] == [
        (P["image"], {"type": "string", "value": "Bloodshot poster.jpg"}),
        (P["director"], item_dv("Q501")),
        (P["publication_date"], {"type": "time", "value": {
            "time": "+2020-02-21T00:00:00Z", "timezone": 0, "before": 0, "after": 0, "precision": 11,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}}),
    ]
    # the name equals the item's label: the caption shows it, nothing to write
    assert plan.new_text == "{{SV infobox}}\n'''Bloodshot''' is a 2020 film.\n"


def test_wikilink_resolves_through_local_sitelink():
    repo = Repo(links={("en", "Dave Wilson"): "Q500"})
    assert si.convert("item", "[[Dave Wilson]]", "en", resolver(repo=repo)) == Value([item_dv("Q500")], "[[Dave Wilson]]")


def test_wikilink_resolves_through_wikipedia_and_imports():
    wd = Wikidata(links={("enwiki", "Dave Wilson (director)"): "Q999", ("enwiki", "Vin Diesel"): "Q178166"},
                  entities={"Q999": wd_entity("Q999", {"enwiki": "Dave Wilson (director)"}),
                            "Q178166": wd_entity("Q178166", {"enwiki": "Vin Diesel"})})
    repo = Repo(links={("enwiki", "Vin Diesel"): "Q777"})  # already imported: its Wikipedia sitelink is local
    value = si.convert("item", "{{Plainlist|\n* [[Dave Wilson (director)|Dave Wilson]]\n* [[Vin Diesel]]\n}}", "en",
                       resolver(repo=repo, wikidata=wd))
    assert value.datavalues == [{"type": "wikibase-entityid", "import": "Q999"}, item_dv("Q777")]


def test_disambiguation_page_is_unresolved():
    wd = Wikidata(links={("enwiki", "Dave Wilson"): "Q5"},
                  entities={"Q5": wd_entity("Q5", {"enwiki": "Dave Wilson"}, p31=["Q4167410"])})
    assert isinstance(si.convert("item", "[[Dave Wilson]]", "en", resolver(wikidata=wd)), Unresolved)


def test_plain_name_is_unresolved():
    repo = Repo(links={("en", "Dave Wilson"): "Q500"})
    assert isinstance(si.convert("item", "Dave Wilson", "en", resolver(repo=repo)), Unresolved)
    # one linked name and one plain one: the row is not covered
    assert isinstance(si.convert("item", "[[Dave Wilson]], Jeff Wadlow", "en", resolver(repo=repo)), Unresolved)


def test_dates():
    r = resolver()

    def time(raw):
        v = si.convert("time", raw, "en", r)
        return None if isinstance(v, Unresolved) else (v.datavalues[0]["value"]["time"], v.datavalues[0]["value"]["precision"])

    assert time("{{Film date|2016|2|19}}") == ("+2016-02-19T00:00:00Z", 11)
    assert time("{{Start date|1999|11}}") == ("+1999-11-00T00:00:00Z", 10)
    assert time("1999") == ("+1999-00-00T00:00:00Z", 9)
    assert time("[[1971]]<ref>a</ref>") == ("+1971-00-00T00:00:00Z", 9)
    assert time("1999-11-17") == ("+1999-11-17T00:00:00Z", 11)
    assert time("17 November 1999") == ("+1999-11-17T00:00:00Z", 11)
    assert si.convert("time", "17 novembre 1999", "it", r).datavalues[0]["value"]["time"] == "+1999-11-17T00:00:00Z"
    assert time("circa 1999") is None
    assert time("1999 or 2000") is None
    assert time("{{Film date|2016|2|19|Berlin|2016|3|1|US}}") is None  # two dates: which one?
    assert time("31 February 1999") is None


def test_image_on_commons_written():
    repo, client = film_world()
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert (P["image"], {"type": "string", "value": "Bloodshot poster.jpg"}, "Bloodshot poster.jpg") in plan.writes
    # a file another repository holds (enwiki: a non-free poster) stays on the call, no claim
    client.images["Bloodshot poster.jpg"] = "other"
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert not [w for w in plan.writes if w[0] == P["image"]]
    assert plan.new_text.startswith("{{SV infobox|image=Bloodshot poster.jpg}}")
    # a file no repository has is unresolved
    client.images.clear()
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert [row for row, _ in plan.unresolved] == ["image"]


def test_image_refused_stays_on_the_call(capsys):
    repo, client = film_world(refuse={P["image"]})
    code = si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print)
    assert code == 0
    assert [p for _, p, _ in repo.added] == [P["director"], P["publication_date"]]
    title, text, summary = client.edits[0]
    assert text.startswith("{{SV infobox|image=Bloodshot poster.jpg}}")
    assert summary == "Switch to the SV infobox; facts on the item"


def test_row_already_on_the_item_is_not_written():
    repo, client = film_world()
    repo.entity("Q100")["claims"][P["director"]] = [statement(item_dv("Q42"))]
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert P["director"] not in [w[0] for w in plan.writes]


UNRESOLVED_TEXT = FILM_TEXT.replace("| budget", "| starring = Vin Diesel\n| budget")


def test_unresolved_row_blocks_the_page(capsys):
    repo, client = film_world()
    client.pages["Bloodshot (film)"] = UNRESOLVED_TEXT
    plan = si.plan_page(page(UNRESOLVED_TEXT), claims_of(repo.entity("Q100")), MAPPING,
                        resolver(repo=repo, client=client))
    assert plan.unresolved == [("cast", "Vin Diesel")] and plan.blocked
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 0
    assert repo.added == [] and client.edits == []
    assert "BLOCKED" in capsys.readouterr().out


def test_accept_loss_lets_it_switch(capsys):
    repo, client = film_world()
    client.pages["Bloodshot (film)"] = UNRESOLVED_TEXT
    si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True,
           accept={("en", "Bloodshot (film)")}, out=print)
    out = capsys.readouterr().out
    assert len(client.edits) == 1 and "dropped cast: Vin Diesel" in out
    assert len(repo.added) == 3


def test_nested_parameter_templates_do_not_break_parsing():
    text = ("Intro {{Nowrap|x}}.\n{{Infobox film\n| name = {{lang|en|Bloodshot}}\n"
            "| director = {{Plainlist|\n* [[Dave Wilson (director)|Dave Wilson]]<ref>{{cite web|title=a|url=b}}</ref>\n}}\n"
            "| released = {{Film date|2020|2|21|{{small|US}}}}<!-- {{Film date|2019}} -->\n"
            "| caption = [[File:X.jpg|thumb|a {{small|b}}]]\n}}\nBody {{cite web|title=c}}.")
    repo, client = film_world()
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.unresolved == []
    assert [w[0] for w in plan.writes] == [P["director"], P["publication_date"]]
    assert plan.new_text == "Intro {{Nowrap|x}}.\n{{SV infobox}}\nBody {{cite web|title=c}}."


def test_two_infoboxes_block_the_page():
    text = "{{Infobox film|name=A}}\n{{Infobox album|name=B}}\nText."
    plan = si.plan_page(page(text), {}, MAPPING, resolver())
    assert plan.blocked and "two Wikipedia infoboxes" in plan.blocked and plan.new_text is None


def test_page_with_sv_infobox_is_left_alone():
    text = "{{SV infobox}}\n{{Infobox film|director=[[A]]}}\nText."
    assert si.plan_page(page(text), {}, MAPPING, resolver()) is None
    assert si.plan_page(page("Just text.\n{{SV categories}}"), {}, MAPPING, resolver()) is None


def test_sv_categories_removed_when_sv_infobox_added():
    text = "{{Infobox film\n| name = Bloodshot\n}}\nText.\n{{SV categories|sortkey=Bloodshot 2020}}\n[[Category:Other]]"
    repo, client = film_world()
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.new_text == "{{SV infobox|sortkey=Bloodshot 2020}}\nText.\n[[Category:Other]]"


def test_layout_differing_from_the_items_type_keeps_type():
    text = "{{Album\n| titolo = La buona novella\n| tipo = Singolo\n}}\nTesto."
    repo = Repo([entity("Q100", [], {"it": "La buona novella"})])
    plan = si.plan_page(page(text, "it", "La buona novella"), claims_of(repo.entity("Q100")), MAPPING,
                        resolver("it", repo=repo))
    assert plan.layout == "song" and plan.new_text.startswith("{{SV infobox|type=song}}")


BIO = """{{Bio
|Nome = Fabrizio Cristiano
|Cognome = De André
|PostCognomeVirgola = noto come '''Fabrizio De André'''
|Sesso = M
|LuogoNascita = Genova
|GiornoMeseNascita = 18 febbraio
|AnnoNascita = 1940
|NoteNascita = <ref>Anagrafe.</ref>
|Epoca = 1900
|Attività = cantautore
|Nazionalità = italiano
|PostNazionalità = , autore di [[La buona novella]]
}}
Testo.
{{SV categories}}"""


def bio_world(**claims):
    repo = Repo([entity("Q200", [PERSON], {"it": "Fabrizio De André"}, **claims)],
                {("it", "Genova"): "Q30", ("it", "Cantautore"): "Q31", ("it", "Italia"): "Q32",
                 ("it", "Fabrizio De André"): "Q200"})
    wd = Wikidata(hits={("italiano", "it"): ["Q652", "Q38"]},
                  entities={"Q652": wd_entity("Q652"),
                            "Q38": wd_entity("Q38", {"itwiki": "Italia"}, demonyms=[("it", "italiano")])})
    repo.links["itwiki", "Italia"] = "Q32"
    return repo, wd


def bio_page():
    return si.Page("it", "Fabrizio De André", "Q200", 10, BIO, "Fabrizio De André")


def test_bio_becomes_sv_bio_with_free_text():
    repo, wd = bio_world()
    plan = si.plan_page(bio_page(), claims_of(repo.entity("Q200")), MAPPING, resolver("it", repo=repo, wikidata=wd))
    assert plan.blocked is None, plan.unresolved
    written = {prop: dv for prop, dv, _ in plan.writes}
    assert written[P["sex_or_gender"]] == item_dv("Q246")
    assert written[P["birth_place"]] == item_dv("Q30")
    assert written[P["birth_date"]]["value"]["time"] == "+1940-02-18T00:00:00Z"
    assert written[P["occupation"]] == item_dv("Q31")
    assert written[P["country_of_citizenship"]] == item_dv("Q32")
    assert plan.new_text == ("{{SV infobox}}\n{{SV bio|name=Fabrizio Cristiano De André|note=noto come '''Fabrizio De "
                             "André'''|rest=, autore di [[La buona novella]]|ref=<ref>Anagrafe.</ref>}}\nTesto.")
    assert set(plan.bio.facts) == {"Genova", "18 febbraio 1940", "cantautore", "italiano"}


def test_bio_facts_on_the_item_are_not_written():
    repo, wd = bio_world(sex_or_gender=[item_dv("Q246")], birth_place=[item_dv("Q30")],
                         occupation=[item_dv("Q31")], country_of_citizenship=[item_dv("Q32")],
                         birth_date=[{"type": "time", "value": {"time": "+1940-02-18T00:00:00Z", "precision": 11}}])
    plan = si.plan_page(bio_page(), claims_of(repo.entity("Q200")), MAPPING, resolver("it", repo=repo, wikidata=wd))
    assert plan.writes == [] and plan.blocked is None


def bio_render(new_sentence):
    def render(title, text):
        if text.startswith("{{Bio"):
            return ('<div><p><b>Fabrizio</b> (<a href="x">Genova</a>, 18 febbraio 1940), è stato un '
                    '<a>cantautore</a> <a>italiano</a>.</p></div>')
        if text.startswith("{{SV bio"):
            return f"<div><p>{new_sentence}</p></div>"
        return '<table class="sv-infobox"><caption>F</caption></table>'
    return render


def test_bio_sentence_mismatch_blocks(capsys):
    repo, wd = bio_world()
    client = Client("it", repo, {"Fabrizio De André": BIO},
                    render=bio_render("Fabrizio De André (Genova, 18 febbraio 1941) è stato un cantautore italiano"))
    si.run(repo, {"it": client}, wd, MAPPING, {"it": set()}, apply=True, out=print)
    out = capsys.readouterr().out
    assert client.edits == []
    assert "BLOCKED" in out and "18 febbraio 1940" in out and "18 febbraio 1941" in out


def test_bio_sentence_match_saves():
    repo, wd = bio_world()
    client = Client("it", repo, {"Fabrizio De André": BIO},
                    render=bio_render("Fabrizio De André (<a>Genova</a>, 18 febbraio 1940) è stato un cantautore "
                                      "italiano"))
    si.run(repo, {"it": client}, wd, MAPPING, {"it": set()}, apply=True, out=print)
    assert len(client.edits) == 1 and repo.summaries[0] == "Switch to the SV infobox; facts on the item"
    assert client.edits[0][2] == "Passa all'infobox SV; dati sull'elemento"


def test_dry_run_shows_the_future_infobox_and_writes_nothing(capsys):
    repo, client = film_world()
    client._render = lambda title, text: ('<table class="sv-infobox sv-infobox-film"><caption>Bloodshot</caption>'
                                          '<tr><th>director</th><td><a href="/x">Dave Wilson</a></td></tr></table>')
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=False, out=print) == 0
    out = capsys.readouterr().out
    assert repo.added == [] and client.edits == []
    assert "director: Dave Wilson" in out
    # the converted values reach the preview as row parameters
    assert "director=[[Dave Wilson (director)|Dave Wilson]]" in client.rendered[0]


def test_save_is_guarded(capsys):
    repo, client = film_world()
    client.cats = {"Bloodshot (film)": {"Action films", "Other"}}
    client._preview = lambda title, text: {"Other"}
    code = si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": {"Action films"}}, apply=True, out=print)
    assert code == 1 and client.edits == []
    assert "UNSAFE" in capsys.readouterr().out


def test_imports_needed_stop_the_apply_without_a_log(capsys):
    wd = Wikidata(links={("enwiki", "Dave Wilson (director)"): "Q999"},
                  entities={"Q999": wd_entity("Q999", {"enwiki": "Dave Wilson (director)"})})
    repo = Repo([entity("Q100", [FILM], {"en": "Bloodshot"})], {("en", "Bloodshot (film)"): "Q100"})
    client = Client("en", repo, {"Bloodshot (film)": FILM_TEXT}, images={"Bloodshot poster.jpg": "commons"})
    assert si.run(repo, {"en": client}, wd, MAPPING, {"en": set()}, apply=True, out=print) == 3
    assert "EnrichItems.php --wiki=en --import Q999" in capsys.readouterr().out
    assert repo.added == [] and client.edits == []
    si.run(repo, {"en": client}, wd, MAPPING, {"en": set()}, apply=True, out=print,
           import_log="IMPORT Q999 = Q48001 (created)\n")
    assert (("Q100", P["director"], item_dv("Q48001"))) in repo.added and len(client.edits) == 1


def test_second_run_plans_nothing(capsys):
    repo, client = film_world()
    si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print)
    assert len(client.edits) == 1
    capsys.readouterr()
    added = len(repo.added)
    si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print)
    assert len(repo.added) == added and len(client.edits) == 1
    assert "switchable 0" in capsys.readouterr().out


def test_accept_loss_titles_resolve():
    pages = {("en", "Bloodshot (film)"), ("it", "Daredevil"), ("en", "Daredevil")}
    assert si.accepted(["Bloodshot (film)", "it:Daredevil"], pages) == {("en", "Bloodshot (film)"), ("it", "Daredevil")}
    with pytest.raises(ValueError, match="both"):
        si.accepted(["Daredevil"], pages)


def test_interwiki_wikipedia_links_resolve_on_that_wikipedia():
    wd = Wikidata(links={("enwiki", "Jeff Rake"): "Q10", ("itwiki", "Jeff Rake"): "Q11"},
                  entities={"Q10": wd_entity("Q10", {"enwiki": "Jeff Rake"}),
                            "Q11": wd_entity("Q11", {"itwiki": "Jeff Rake"})})
    repo = Repo(links={("enwiki", "Jeff Rake"): "Q610", ("itwiki", "Jeff Rake"): "Q611"})
    r = resolver("en", repo=repo, wikidata=wd)
    # the wikis' "wikipedia:" interwiki is en.wikipedia; "wikipedia:it:" goes on to it.wikipedia
    assert si.convert("item", "[[wikipedia:Jeff Rake|Jeff Rake]]", "en", r).datavalues == [item_dv("Q610")]
    assert si.convert("item", "[[w:it:Jeff Rake]]", "en", r).datavalues == [item_dv("Q611")]
    assert isinstance(si.convert("item", "[[wikt:rake]]", "en", r), Unresolved)


def test_the_items_known_type_wins_over_the_old_box():
    # it.wiki's {{Artista musicale}} boxes bands too: the item says musical group
    text = "{{Artista musicale\n| immagine = Beatles.jpg\n}}\nTesto."
    repo = Repo([entity("Q100", ["Q2404"], {"it": "The Beatles"})])
    client = Client("it", repo, {}, images={"Beatles.jpg": "commons"})
    plan = si.plan_page(page(text, "it", "Beatles"), claims_of(repo.entity("Q100")), MAPPING,
                        resolver("it", repo=repo, client=client))
    assert plan.layout == "group" and plan.new_text == "{{SV infobox}}\nTesto."


def test_image_caption_is_not_free_text():
    text = BIO.replace("|Epoca = 1900", "|Epoca = 1900\n|Didascalia = Autoritratto (1623)\n|Didascalia2 = {{Premio|x}}")
    repo, wd = bio_world()
    plan = si.plan_page(si.Page("it", "Fabrizio De André", "Q200", 10, text, "Fabrizio De André"),
                        claims_of(repo.entity("Q200")), MAPPING, resolver("it", repo=repo, wikidata=wd))
    assert "Autoritratto" not in plan.bio.call and "Premio" not in plan.bio.call


def test_sv_bio_missing_on_the_wiki_blocks_with_the_reason(capsys):
    repo, wd = bio_world()
    redlink = ('<p><a href="/w/index.php?title=Template:SV_bio&amp;action=edit&amp;redlink=1" class="new">'
               'Template:SV bio</a></p>')
    client = Client("it", repo, {"Fabrizio De André": BIO}, render=lambda title, text: (
        bio_render("")(title, text) if text.startswith("{{Bio") else redlink))
    si.run(repo, {"it": client}, wd, MAPPING, {"it": set()}, apply=True, out=print)
    assert client.edits == [] and "{{SV bio}} is not on it yet" in capsys.readouterr().out


def test_rendered_rows_read_as_text():
    rendered = ('<table class="sv-infobox sv-infobox-film"><caption>B</caption><tbody><tr><th>cast</th><td>'
                '<a href="/a">A</a>, <a href="/b">B</a><sup class="reference"><a href="#c">[1]</a></sup></td></tr>'
                '</tbody></table>')
    assert si.infobox_rows(rendered) == ["caption: B", "cast: A, B"]


def test_a_fact_another_page_of_the_item_writes_covers_the_row(capsys):
    # en and it share the item: the date en converts covers it's range "2015 – 2018" in the same run
    repo, en = film_world()
    it_text = "{{Film\n| titolo originale = Bloodshot\n| anno uscita = [[2015]] – [[2018]]\n}}\nTesto."
    repo.links["it", "Bloodshot (film)"] = "Q100"
    it = Client("it", repo, {"Bloodshot (film)": it_text})
    assert si.run(repo, {"en": en, "it": it}, Wikidata(), MAPPING, {"en": set(), "it": set()}, apply=True,
                  out=print) == 0
    assert len(en.edits) == 1 and len(it.edits) == 1
    assert [p for _, p, _ in repo.added].count(P["publication_date"]) == 1


def test_relying_on_a_page_that_failed_is_not_saved(capsys):
    repo, en = film_world(refuse={P["director"]})  # en fails before its date is written
    it_text = "{{Film\n| anno uscita = [[2015]] – [[2018]]\n}}\nTesto."
    repo.links["it", "Bloodshot (film)"] = "Q100"
    it = Client("it", repo, {"Bloodshot (film)": it_text})
    assert si.run(repo, {"en": en, "it": it}, Wikidata(), MAPPING, {"en": set(), "it": set()}, apply=True,
                  out=print) == 1
    assert it.edits == [] and "relies on" in capsys.readouterr().out


def test_dry_run_names_the_items_to_enrich_from_wikidata_first(capsys):
    repo, client = film_world()
    si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=False, out=print, tools_dir="/t")
    assert "EnrichItems.php --wiki=en --item Q100" in capsys.readouterr().out


def test_dry_run_lists_the_imports_of_blocked_pages_apart(capsys):
    wd = Wikidata(links={("enwiki", "Dave Wilson (director)"): "Q999"},
                  entities={"Q999": wd_entity("Q999", {"enwiki": "Dave Wilson (director)"})})
    repo = Repo([entity("Q100", [FILM], {"en": "Bloodshot"})], {("en", "Bloodshot (film)"): "Q100"})
    client = Client("en", repo, {"Bloodshot (film)": UNRESOLVED_TEXT}, images={"Bloodshot poster.jpg": "commons"})
    si.run(repo, {"en": client}, wd, MAPPING, {"en": set()}, apply=False, out=print)
    out = capsys.readouterr().out
    assert "imports needed 0" in out and "blocked pages would also need: Q999" in out


def test_main_wires_the_cli(capsys):
    repo, client = film_world()
    client.pages["Bloodshot (film)"] = UNRESOLVED_TEXT
    client.pages["Other"] = FILM_TEXT
    client.revid["Other"] = 10
    repo.links["en", "Other"] = "Q101"
    code = si.main(["--wiki", "en", "--page", "en:Bloodshot (film)", "--accept-loss", "Bloodshot (film)"],
                   repo=repo, clients={"en": client}, wikidata=Wikidata())
    out = capsys.readouterr().out
    assert code == 0 and "en:Bloodshot (film)" in out and "en:Other" not in out
    assert "dropped cast: Vin Diesel" in out and "(dry run: nothing written)" in out
    with pytest.raises(SystemExit):
        si.main(["--wiki", "en", "--accept-loss", "Nowhere"], repo=repo, clients={"en": client}, wikidata=Wikidata())
