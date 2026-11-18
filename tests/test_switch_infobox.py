import pytest
import requests

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

    def sitelinks(self, items):
        out = {}
        for (site, title), item in self.links.items():
            if item in items:
                out.setdefault(item, {})[site] = title
        return out

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


def test_image_refused_stays_on_the_call(capsys):
    repo, client = film_world(refuse={P["image"]})
    code = si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print)
    assert code == 0
    assert [p for _, p, _ in repo.added] == [P["director"], P["publication_date"]]
    title, text, summary = client.edits[0]
    assert text.startswith("{{SV infobox|image=Bloodshot poster.jpg}}")
    assert summary == "Switch to the SV infobox; facts on the item"


class FlakyRepo(Repo):
    """add_claim answers HTTP 502 (or times out) `failures` times for `prop`, then works."""

    def __init__(self, *a, prop=None, failures=0, error="502", **kw):
        super().__init__(*a, **kw)
        self.prop, self.failures, self.error, self.calls = prop, failures, error, 0

    def add_claim(self, item, prop, datavalue, tags=None, summary=None):
        if prop == self.prop and self.failures:
            self.failures -= 1
            self.calls += 1
            if self.error == "timeout":
                raise requests.Timeout("read timed out")
            response = requests.Response()
            response.status_code = int(self.error)
            raise requests.HTTPError(f"{self.error} Server Error: Bad Gateway", response=response)
        super().add_claim(item, prop, datavalue, tags, summary)


def flaky_world(**kw):
    repo = FlakyRepo([entity("Q100", [FILM], {"en": "Bloodshot"})],
                     {("en", "Dave Wilson (director)"): "Q501", ("en", "Bloodshot (film)"): "Q100"}, **kw)
    client = Client("en", repo, {"Bloodshot (film)": FILM_TEXT}, images={"Bloodshot poster.jpg": "commons"})
    return repo, client


def test_a_transient_server_error_is_retried(capsys, monkeypatch):
    waits = []
    monkeypatch.setattr(si, "_sleep", waits.append)
    for error in ("502", "503", "timeout"):
        repo, client = flaky_world(prop=P["image"], failures=2, error=error)
        assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 0
        assert (("Q100", P["image"], {"type": "string", "value": "Bloodshot poster.jpg"})) in repo.added
        assert client.edits[0][1].startswith("{{SV infobox}}")  # a statement, not image= on the call
    assert waits[:2] == [2, 4]


def test_a_timed_out_write_that_was_saved_is_not_written_again(monkeypatch):
    monkeypatch.setattr(si, "_sleep", lambda s: None)
    repo, client = flaky_world()
    real = repo.add_claim

    def saved_then_timeout(item, prop, datavalue, tags=None, summary=None):
        real(item, prop, datavalue, tags, summary)
        if prop == P["image"] and len([p for _, p, _ in repo.added if p == prop]) == 1:
            raise requests.Timeout("read timed out")
    repo.add_claim = saved_then_timeout
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=lambda *a: None) == 0
    assert [p for _, p, _ in repo.added].count(P["image"]) == 1 and len(client.edits) == 1


def test_a_persistent_server_error_fails_the_page_instead_of_keeping_the_image(capsys, monkeypatch):
    monkeypatch.setattr(si, "_sleep", lambda s: None)
    repo, client = flaky_world(prop=P["image"], failures=99)
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 1
    out = capsys.readouterr().out
    assert client.edits == [] and repo.calls == si.WRITE_TRIES
    assert "FAILED writing" in out and "kept as image=" not in out
    # another page of the run still goes ahead
    repo, client = flaky_world(prop=P["director"], failures=99)
    client.pages["Other"], client.revid["Other"] = "{{Infobox film\n| name = Other\n}}\nText.", 10
    repo.links["en", "Other"] = "Q101"
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 1
    assert [t for t, _, _ in client.edits] == ["Other"]


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
    assert si.plan_page(page("{{SV infobox}}\nText."), {}, MAPPING, resolver()) is None
    assert si.plan_page(page("Just text.\n{{SV categories}}"), {}, MAPPING, resolver()) is None


def test_page_with_sv_infobox_and_a_wikipedia_box_is_reported_for_the_author(capsys):
    text = "{{SV infobox}}\n{{Infobox film|director=[[A]]}}\nText."
    plan = si.plan_page(page(text), {}, MAPPING, resolver())
    assert plan is not None and plan.new_text is None and plan.writes == []
    assert plan.blocked == "{{SV infobox}} already on the page, and Infobox film too: for the author"
    repo, client = film_world()
    client.pages["Bloodshot (film)"] = text
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 0
    out = capsys.readouterr().out
    assert client.edits == [] and repo.added == []
    assert "blocked: en:Bloodshot (film): {{SV infobox}} already on the page" in out
    # {{Bio}} next to an {{SV infobox}} too
    bio = si.plan_page(si.Page("it", "F", "Q200", 10, "{{SV infobox}}\n" + BIO, "F"), {}, MAPPING, resolver("it"))
    assert bio.blocked == "{{SV infobox}} already on the page, and Bio too: for the author"


def test_sv_infobox_of_another_item_does_not_count_as_the_pages_own():
    # a section's {{SV infobox|item=…}} renders another work; the page's own Wikipedia box still switches
    text = "{{Infobox film\n| name = Bloodshot\n}}\nText.\n== Sequel ==\n{{SV infobox|item=Q999}}\nMore."
    repo, client = film_world()
    assert not si.has_sv_infobox(text, "Q100")
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan is not None and plan.blocked is None
    assert plan.new_text == "{{SV infobox}}\nText.\n== Sequel ==\n{{SV infobox|item=Q999}}\nMore."
    assert si.has_sv_infobox("{{SV infobox|type=song}}\nText.", "Q100")


def test_sv_infobox_naming_the_pages_own_item_is_the_pages_box():
    # {{SV infobox|item=Q100}} on the page of Q100 is its own box: no second {{SV infobox}} is added
    for own in ("Q100", "q100", " Q100 "):
        text = f"{{{{SV infobox|item={own}}}}}\n{{{{Infobox film|name=Bloodshot}}}}\nText."
        assert si.has_sv_infobox(text, "Q100")
        plan = si.plan_page(page(text, item="Q100"), {}, MAPPING, resolver())
        assert plan.new_text is None and "already on the page" in plan.blocked
    assert not si.has_sv_infobox("{{SV infobox|item=Q1000}}\nText.", "Q100")


def test_embedded_infobox_blocks_the_page():
    # enwiki's |module={{Infobox musical artist|embed=yes|…}} inside Infobox person: its facts would be lost
    text = ("{{Infobox person|name=X|module={{Infobox musical artist|embed=yes|genre=[[Rock music|Rock]]"
            "|label=[[Apple Records]]}}}}\nText.")
    plan = si.plan_page(page(text, title="X", label="X"), {}, MAPPING, resolver())
    assert plan is not None and plan.new_text is None and plan.writes == []
    assert plan.blocked == "embedded infobox (Infobox musical artist): for the author"


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
    assert plan.bio.lacks == {"death_date", "death_place"}


def test_a_bio_page_without_an_infobox_needs_a_person_item():
    # no Wikipedia box tells the kind: the item must be a person before {{Bio}} facts go onto it
    def plan_for(instance_of, wd_p31=None):
        repo, wd = bio_world()
        repo.entity("Q200")["claims"][P["instance_of"]] = [statement(item_dv(q)) for q in instance_of]
        if wd_p31 is not None:
            repo.links["itwiki", "Fabrizio De André"] = "Q200"
            wd.links["itwiki", "Fabrizio De André"] = "Q1"
            wd.ents["Q1"] = wd_entity("Q1", {"itwiki": "Fabrizio De André"}, p31=wd_p31)
        return si.plan_page(bio_page(), claims_of(repo.entity("Q200")), MAPPING, resolver("it", repo=repo, wikidata=wd))
    assert plan_for([PERSON]).blocked is None
    assert plan_for([FILM]).blocked == "{{Bio}} on a page whose item is a film, not a person: for the author"
    assert plan_for([FILM]).writes == []
    assert plan_for([], wd_p31=["Q5"]).blocked is None  # the kind from Wikidata, as connect_items checks it
    assert plan_for([], wd_p31=["Q11424"]).blocked == ("{{Bio}} on a page whose item (Wikidata Q1) is not a "
                                                       "person: for the author")
    assert plan_for([]).blocked == "{{Bio}} on a page whose item has no known kind: for the author"
    # a Wikipedia infobox gives the kind: no extra check
    repo, client = film_world()
    repo.entity("Q100")["claims"][P["instance_of"]] = []
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.blocked is None


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


def test_rendered_rows_skip_the_kind_label_and_the_source_footer():
    rendered = ('<table class="sv-infobox sv-infobox-film"><caption><span class="sv-infobox-kind">Film</span>B'
                '</caption><tbody><tr><th>cast</th><td>A</td></tr><tr><td colspan="2" class="sv-infobox-source">'
                'Dati dall\'elemento <a href="/x">Q1</a></td></tr></tbody></table>')
    assert si.infobox_rows(rendered) == ["caption: B", "cast: A"]


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


def test_missing_image_is_dropped_not_blocking(capsys):
    # a file no repository holds renders as a broken link: not a fact the old box shows
    repo, client = film_world()
    client.images.clear()
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.unresolved == [] and plan.blocked is None
    assert plan.missing_images == ["Bloodshot poster.jpg"]
    assert P["image"] not in [w[0] for w in plan.writes] and "image=" not in plan.new_text
    si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print)
    assert "image Bloodshot poster.jpg: missing in every repository, dropped" in capsys.readouterr().out
    assert len(client.edits) == 1


def test_a_row_the_shown_layout_lacks_is_still_written():
    # Infobox film on an item typed as a TV series: the tv layout has no director row, the fact stays
    repo, client = film_world()
    repo.entity("Q100")["claims"][P["instance_of"]] = [statement(item_dv("Q6022"))]
    plan = si.plan_page(page(FILM_TEXT), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.layout == "tv"
    assert (P["director"], item_dv("Q501")) in [(p, dv) for p, dv, _ in plan.writes]
    assert "director" not in plan.preview


def test_a_row_the_shown_layout_lacks_is_unresolved_when_unconvertible():
    repo, client = film_world()
    repo.entity("Q100")["claims"][P["instance_of"]] = [statement(item_dv("Q6022"))]
    text = FILM_TEXT.replace("[[Dave Wilson (director)|Dave Wilson]]", "Dave Wilson")
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert ("director", "Dave Wilson") in plan.unresolved and plan.blocked


def test_superscript_digits_are_not_a_date():
    assert isinstance(si.convert("time", "{{Start date|²}}", "en", resolver()), Unresolved)
    assert isinstance(si.convert("time", "{{Start date|2008|²}}", "en", resolver()), Unresolved)


def test_non_ascii_digits_are_not_a_date():
    # Arabic-Indic digits: \d would take them, int() would read them
    for raw in ("٢٠٠٨", "٢٠٠٨-١١-١٧", "٢٠٠٨-١١", "١٧ November ١٩٩٩", "November ١٧, ١٩٩٩", "November ١٩٩٩"):
        assert isinstance(si.convert("time", raw, "en", resolver()), Unresolved), raw
    assert si.DATE.findall("nato nel ١٩٤٠") == []


def test_dates_before_the_gregorian_reform_carry_the_julian_calendar_model():
    # Wikipedia gives dates before 15 October 1582 in the Julian calendar, as Wikidata stores them
    def model(raw):
        return si.convert("time", raw, "en", resolver()).datavalues[0]["value"]["calendarmodel"]
    julian, gregorian = "http://www.wikidata.org/entity/Q1985786", "http://www.wikidata.org/entity/Q1985727"
    assert model("{{Birth date|1452|4|15}}") == julian
    assert model("15 aprile 1452") == julian
    assert model("1582-10-04") == julian and model("1500") == julian
    assert model("1582-10-15") == gregorian and model("31 March 1685") == gregorian and model("1999") == gregorian
    # a Julian-only leap day exists; a Gregorian one still does not
    assert model("29 February 1500") == julian
    assert isinstance(si.convert("time", "29 February 1700", "en", resolver()), Unresolved)


def test_one_resolver_is_built_per_wiki(monkeypatch):
    built = []
    real = si.Resolver

    def counting(*args):
        built.append(args[0])
        return real(*args)
    monkeypatch.setattr(si, "Resolver", counting)
    repo, client = film_world()
    client.pages["Other"], client.revid["Other"] = FILM_TEXT, 10
    repo.links["en", "Other"] = "Q101"
    si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=False, out=lambda *a: None)
    assert built == ["en"]


def test_nationality_prefers_the_demonyms_of_the_persons_citizenships():
    # two countries answer to "americano" (United States, and a historical state): the person's own P27 decides
    usa, other = wd_entity("Q30", {"itwiki": "Stati Uniti d'America"}, demonyms=[("it", "americano")]), \
        wd_entity("Q999", demonyms=[("it", "Americano ")])
    person = wd_entity("Q1", {"itwiki": "Mario"})
    person["claims"]["P27"] = [statement(item_dv("Q30"))]
    wd = Wikidata(links={("itwiki", "Mario"): "Q1"}, hits={("americano", "it"): ["Q999", "Q30"]},
                  entities={"Q30": usa, "Q999": other, "Q1": person})
    repo = Repo(links={("itwiki", "Mario"): "Q200", ("itwiki", "Stati Uniti d'America"): "Q60"})
    assert si.Resolver("it", repo, wd, None).country("americano", "Q200") == item_dv("Q60")
    # without the person's item the search alone is ambiguous: never guessed
    assert isinstance(si.Resolver("it", repo, wd, None).country("americano"), Unresolved)


def test_bio_gate_ignores_wording_but_not_a_dropped_fact():
    old = ("<p><b>Anna Magnani</b> (<a>Roma</a>, 7 marzo 1908 – <a>Roma</a>, 26 settembre 1973), è stata "
           "un'<a>attrice</a> nonché <a>cantante</a> <a>italiana</a>, attiva nella Città del cinema degli anni "
           "trenta</p>")
    # conjunctions, prepositions, word order, accents and case: the same facts
    new = ("<p>Anna Magnani (<a>Roma</a>, 7 marzo 1908 – <a>Roma</a>, 26 settembre 1973) è stata un'attrice e "
           "cantante italiana, attiva negli anni Trenta e nella citta del cinema</p>")
    assert si.compare_sentences(old, new, set()) == ([], [], [])
    # an occupation dropped still blocks
    dropped = new.replace("un'attrice e cantante", "un'attrice")
    assert si.compare_sentences(old, dropped, set())[0] == ["cantante"]
    # and so does a word of the free text
    assert si.compare_sentences(old, new.replace("del cinema", "del teatro"), set())[:2] == (["cinema"], ["teatro"])


def test_the_life_span_is_the_parenthesis_with_a_date():
    html_ = ("<p>Fabrizio De André (detto <a>Faber</a>) (<a>Genova</a>, 18 febbraio 1940 – <a>Milano</a>, 11 gennaio "
             "1999) è stato un <a>cantautore</a> italiano</p>")
    _, facts, span, _ = si._facts(html_)
    assert span == "Genova, 18 febbraio 1940 – Milano, 11 gennaio 1999"
    assert "italiano" in facts and "detto" not in facts
    # a link in a parenthesis without a date is no life span: no span at all
    assert si._facts("<p>Faber (<a>Genova</a>) è un <a>cantautore</a></p>")[2] == ""


def test_nationality_matches_a_whole_demonym_only():
    wd = Wikidata(hits={("italiano", "it"): ["Q1", "Q2", "Q38"]},
                  entities={"Q1": wd_entity("Q1", demonyms=[("it", "italoamericano")]),
                            "Q2": wd_entity("Q2", demonyms=[("it", "italiano-americano"), ("en", "italiano")]),
                            "Q38": wd_entity("Q38", {"itwiki": "Italia"}, demonyms=[("it", "Italiano")])})
    repo = Repo(links={("itwiki", "Italia"): "Q32"})
    assert si.Resolver("it", repo, wd, None).country("italiano") == item_dv("Q32")
    # a demonym that only contains the word is no match
    wd.ents["Q38"] = wd_entity("Q38", {"itwiki": "Italia"}, demonyms=[("it", "italiano antico")])
    assert isinstance(si.Resolver("it", repo, wd, None).country("italiano"), Unresolved)


TV = """{{Infobox television
| name = Fringe
| first_aired = %s
| released = %s
}}
Text."""


def tv_plan(first, released):
    repo = Repo([entity("Q100", ["Q6022"], {"en": "Fringe"})], {("en", "Fringe"): "Q100"})
    return si.plan_page(page(TV % (first, released), title="Fringe", label="Fringe"),
                        claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo))


def test_second_value_of_a_single_row_is_tried_and_duplicates_reported(capsys):
    plan = tv_plan("circa 2008", "{{Start date|2008|9|9}}")
    assert plan.unresolved == [] and plan.blocked is None
    assert [dv["value"]["time"] for _, dv, _ in plan.writes] == ["+2008-09-09T00:00:00Z"]
    plan = tv_plan("2008", "2009")
    assert [dv["value"]["time"] for _, dv, _ in plan.writes] == ["+2008-00-00T00:00:00Z"]
    assert plan.duplicates == [("publication_date", "2009")]
    si._report(plan, {}, print)
    assert "duplicate value for publication_date, not written: 2009" in capsys.readouterr().out
    assert tv_plan("2008", "{{Start date|2008}}").duplicates == []  # the same date twice
    assert tv_plan("circa 2008", "about 2009").unresolved == [("publication_date", "circa 2008"),
                                                              ("publication_date", "about 2009")]


OLD_F = ("<p><b>Anna Magnani</b> (<a>Roma</a>, 7 marzo 1908 – <a>Roma</a>, 26 settembre 1973), è stata "
         "un'<a>attrice</a> <a>italiana</a>.</p>")


def test_bio_gate_compares_both_ways_with_feminine_forms():
    lacks = set()
    same = "<p>Anna Magnani (<a>Roma</a>, 7 marzo 1908 – <a>Roma</a>, 26 settembre 1973) è stata un'attrice italiana</p>"
    assert si.compare_sentences(OLD_F, same, lacks) == ([], [], [])
    # a feminine nationality the old sentence states and the new one lacks blocks
    other = same.replace("italiana", "francese")
    missing, extra, _ = si.compare_sentences(OLD_F, other, lacks)
    assert missing == ["italiana"] and extra == ["francese"]
    # an occupation the old sentence did not state is not an addition: Bio had occupations
    more = same.replace("un'attrice italiana", "un'attrice e cantante italiana")
    assert si.compare_sentences(OLD_F, more, lacks)[1] == ["cantante"]


def test_bio_gate_reports_what_bio_could_not_state_as_additions():
    old = "<p><b>Anna</b> (<a>Roma</a>, 7 marzo 1908), è un'<a>attrice</a> <a>italiana</a>.</p>"
    new = ("<p>Anna (<a>Roma</a>, 7 marzo 1908 – <a>Milano</a>, 26 settembre 1973) è stata un'attrice "
           "italiana</p>")
    assert si.compare_sentences(old, new, {"death_date", "death_place"}) == ([], [], ["Milano", "26 settembre 1973"])
    # Bio had a death place: a different one cannot be an addition
    assert si.compare_sentences(old, new, {"death_date"})[1] == ["Milano"]


def test_bio_with_sesso_f_passes_the_gate_on_matching_feminine_sentences(capsys):
    text = BIO.replace("|Sesso = M", "|Sesso = F").replace("cantautore", "attrice")
    repo, wd = bio_world()
    repo.links["it", "Attrice"] = "Q33"
    old = ("<p><b>F</b> (<a>Genova</a>, 18 febbraio 1940), è stata un'<a>attrice</a> <a>italiana</a>"
           ", autore di <a>La buona novella</a>.</p>")

    def render(new):
        return lambda title, t: old if t.startswith("{{Bio") else (f"<p>{new}</p>" if t.startswith("{{SV bio")
                                                                  else "")
    good = ("F (<a>Genova</a>, 18 febbraio 1940) è stata un'attrice italiana, autore di "
            "<a>La buona novella</a>")
    client = Client("it", repo, {"Fabrizio De André": text}, render=render(good))
    si.run(repo, {"it": client}, wd, MAPPING, {"it": set()}, apply=True, out=print)
    assert len(client.edits) == 1
    repo, wd = bio_world()
    repo.links["it", "Attrice"] = "Q33"
    client = Client("it", repo, {"Fabrizio De André": text}, render=render(good.replace("italiana", "italiano")))
    si.run(repo, {"it": client}, wd, MAPPING, {"it": set()}, apply=True, out=print)
    out = capsys.readouterr().out
    assert client.edits == [] and "BLOCKED" in out and "old:" in out and "new:" in out


def test_every_unwritten_value_of_a_one_value_row_is_reported(capsys):
    plan = tv_plan("2008", "circa 2009")
    assert [dv["value"]["time"] for _, dv, _ in plan.writes] == ["+2008-00-00T00:00:00Z"]
    assert plan.unconvertible == [("publication_date", "circa 2009")] and plan.duplicates == []
    plan = tv_plan("circa 2008", "{{Start date|2008|9|9}}")
    assert plan.unconvertible == [("publication_date", "circa 2008")] and plan.unresolved == []
    si._report(plan, {}, print)
    assert "value for publication_date not convertible, not written: circa 2008" in capsys.readouterr().out
    # nothing converts: the row is unresolved with every value, not also reported as unconvertible
    plan = tv_plan("circa 2008", "about 2009")
    assert plan.unconvertible == [] and [raw for _, raw in plan.unresolved] == ["circa 2008", "about 2009"]


def test_page_plans_do_not_share_their_default_lists():
    required = dict(lang="en", title="A", item="Q1", revid=1, layout="work", writes=[], unresolved=[], new_text=None,
                    bio=None, blocked=None, params={}, preview={}, dropped=[], text="")
    one, two = si.PagePlan(**required), si.PagePlan(**{**required, "writes": [], "unresolved": [], "dropped": []})
    for field in ("covered", "missing_images", "duplicates", "unconvertible"):
        getattr(one, field).append("x")
        assert getattr(two, field) == [], field


def test_main_reports_missing_credentials_but_does_not_hide_other_key_errors(capsys, monkeypatch):
    monkeypatch.delenv("SV_EDITOR_USER", raising=False)
    monkeypatch.delenv("SV_EDITOR_PASSWORD", raising=False)
    monkeypatch.setattr(si, "WikiClient", lambda lang: pytest.fail("no client before the credentials"))
    assert si.main(["--wiki", "en", "--apply"]) == 1
    assert "SV_EDITOR_USER" in capsys.readouterr().err

    def broken(*a, **kw):
        raise KeyError("a bug")
    monkeypatch.setattr(si, "run", broken)
    repo, client = film_world()
    with pytest.raises(KeyError):
        si.main(["--wiki", "en"], repo=repo, clients={"en": client}, wikidata=Wikidata())


def test_bio_gate_keeps_numerals_and_without_as_facts():
    old = "<p>Juan (<a>Madrid</a>, 1 enero 1950) es un <a>ciclista</a> español, dos veces campeón sin rival</p>"
    new = "<p>Juan (<a>Madrid</a>, 1 enero 1950) es un <a>ciclista</a> español, veces campeón rival</p>"
    assert sorted(si.compare_sentences(old, new, set())[0]) == ["dos", "sin"]


def test_a_wiki_error_in_the_person_check_blocks_only_that_page():
    repo, wd = bio_world()
    repo.entity("Q200")["claims"][P["instance_of"]] = []
    res = resolver("it", repo=repo, wikidata=wd)

    def http_502():
        response = requests.Response()
        response.status_code = 502
        return requests.HTTPError("502 Server Error: Bad Gateway", response=response)
    # an API error, an HTTP error status and a timeout alike
    for error in (si.SyncError("502 Bad Gateway"), http_502(), requests.Timeout("502: read timed out")):
        def down(item, error=error):
            raise error
        res.wikidata_item = down
        plan = si.plan_page(bio_page(), claims_of(repo.entity("Q200")), MAPPING, res)
        assert plan.blocked and "502" in plan.blocked and plan.writes == []


def test_the_demonym_search_runs_once_per_adjective():
    wd = Wikidata(hits={("italiano", "it"): ["Q38"]},
                  entities={"Q38": wd_entity("Q38", {"itwiki": "Italia"}, demonyms=[("it", "italiano")])})
    calls = []
    search = wd.search
    wd.search = lambda text, lang: calls.append(text) or search(text, lang)
    res = si.Resolver("it", Repo(links={("itwiki", "Italia"): "Q32"}), wd, None)
    assert res.country("italiano", "Q1") == res.country("italiano", "Q2") == item_dv("Q32")
    assert calls == ["italiano"]


def test_a_page_changed_after_planning_writes_no_statement(capsys):
    repo, client = film_world()
    read = client.revision
    calls = []

    def edited_after_the_plan(title):
        calls.append(title)
        if len(calls) > 1:  # an editor saved the page after it was planned
            client.revid[title] = 11
        return read(title)
    client.revision = edited_after_the_plan
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 1
    out = capsys.readouterr().out
    assert repo.added == [] and client.edits == []
    assert "changed since it was read" in out


def test_a_row_written_in_part_is_finished_on_the_next_run(capsys):
    text = FILM_TEXT.replace("Dave Wilson]]", "Dave Wilson]] and [[Jane Roe]]")
    repo, client = film_world()
    repo.links["en", "Jane Roe"] = "Q502"
    client.pages["Bloodshot (film)"] = text
    real = repo.add_claim

    def down_on_the_second_director(item, prop, datavalue, tags=None, summary=None):
        if datavalue == item_dv("Q502"):
            raise SyncError("en: adding P57 failed: database locked")
        real(item, prop, datavalue, tags, summary)
    repo.add_claim = down_on_the_second_director
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 1
    assert client.edits == [] and repo.entity("Q100")["claims"][P["director"]] == [statement(item_dv("Q501"))]
    # the next run sees director A on the item: it writes B, not A again, before the switch
    repo.add_claim = real
    capsys.readouterr()
    assert si.run(repo, {"en": client}, Wikidata(), MAPPING, {"en": set()}, apply=True, out=print) == 0
    assert [dv for _, p, dv in repo.added if p == P["director"]] == [item_dv("Q501"), item_dv("Q502")]
    assert len(client.edits) == 1
    # a director the box does not show: the item's property was complete before, nothing is written
    repo, client = film_world()
    repo.links["en", "Jane Roe"] = "Q502"
    repo.entity("Q100")["claims"][P["director"]] = [statement(item_dv("Q501")), statement(item_dv("Q42"))]
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert P["director"] not in [w[0] for w in plan.writes] and P["director"] in plan.covered
    # every value of the box on the item already: covered
    repo.entity("Q100")["claims"][P["director"]] = [statement(item_dv("Q502")), statement(item_dv("Q501"))]
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert P["director"] not in [w[0] for w in plan.writes] and P["director"] in plan.covered


def test_a_row_on_the_item_whose_box_value_does_not_convert_stays_covered():
    repo, client = film_world()
    repo.entity("Q100")["claims"][P["director"]] = [statement(item_dv("Q501"))]
    text = FILM_TEXT.replace("Dave Wilson]]", "Dave Wilson]] and Jane Roe")
    plan = si.plan_page(page(text), claims_of(repo.entity("Q100")), MAPPING, resolver(repo=repo, client=client))
    assert plan.blocked is None and plan.unresolved == [] and P["director"] in plan.covered


def test_bio_gate_compares_birth_and_death_facts_in_their_places():
    old = ("<p>Anna (<a>Roma</a>, 7 marzo 1908 – <a>Milano</a>, 26 settembre 1973), è stata un'<a>attrice</a> "
           "<a>italiana</a>.</p>")
    swapped = ("<p>Anna (<a>Milano</a>, 26 settembre 1973 – <a>Roma</a>, 7 marzo 1908) è stata un'attrice "
               "italiana</p>")
    missing, extra, additions = si.compare_sentences(old, swapped, set())
    assert sorted(missing) == ["26 settembre 1973", "7 marzo 1908", "Milano", "Roma"]
    assert sorted(extra) == sorted(missing) and additions == []
    # only the places swapped: the places block, the dates do not
    places = ("<p>Anna (<a>Milano</a>, 7 marzo 1908 – <a>Roma</a>, 26 settembre 1973) è stata un'attrice "
              "italiana</p>")
    assert sorted(si.compare_sentences(old, places, set())[0]) == ["Milano", "Roma"]
    # the same town at both ends is stated in both places
    same = old.replace("Milano", "Roma")
    assert si.compare_sentences(same, same.replace("<p>Anna", "<p>Anna Magnani"), set()) == ([], [], [])
    # a death place Bio could not state may be the birth town: an addition
    born = "<p>Anna (<a>Roma</a>, 7 marzo 1908), è un'<a>attrice</a> <a>italiana</a>.</p>"
    died = ("<p>Anna (<a>Roma</a>, 7 marzo 1908 – <a>Roma</a>, 26 settembre 1973) è stata un'attrice "
            "italiana</p>")
    assert si.compare_sentences(born, died, {"death_date", "death_place"}) == ([], [], ["Roma", "26 settembre 1973"])
    assert si.compare_sentences(born, died, {"death_date"})[1] == ["Roma"]
