from lua_env import LuaEnv
from test_infobox import item, mono, time

# Scribunto's formatDate, for the formats Module:SV/Util uses: j, F, Y and "quoted" literals.
_FORMAT_DATE = r"""
local MONTHS = {
    it = { 'gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto',
           'settembre', 'ottobre', 'novembre', 'dicembre' },
    en = { 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
           'October', 'November', 'December' },
    es = { 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
           'octubre', 'noviembre', 'diciembre' },
    fr = { 'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre',
           'octobre', 'novembre', 'décembre' },
    de = { 'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September',
           'Oktober', 'November', 'Dezember' },
    pt = { 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
           'outubro', 'novembro', 'dezembro' },
}
local l = mw.language.getContentLanguage()
function l:formatDate(fmt, iso)
    local y, m, d = iso:match('^(%d%d%d%d)%-(%d%d)%-(%d%d)$')
    local out, i, quoted = {}, 1, false
    while i <= #fmt do
        local c = fmt:sub(i, i)
        if c == '"' then
            quoted = not quoted
        elseif quoted then
            out[#out + 1] = c
        elseif c == 'j' then
            out[#out + 1] = tostring(tonumber(d))
        elseif c == 'F' then
            out[#out + 1] = MONTHS[self.code][tonumber(m)]
        elseif c == 'Y' then
            out[#out + 1] = tostring(tonumber(y))
        else
            out[#out + 1] = c
        end
        i = i + 1
    end
    return table.concat(out)
end
"""

NEK, BOWIE = "Q100", "Q200"
SASSUOLO, LONDON, NEW_YORK = "Q10", "Q11", "Q12"
SINGER, ACTOR = "Q20", "Q21"
ITALY, UK = "Q30", "Q31"
MALE, FEMALE = "Q246", "Q247"


def nek(gender=MALE, **claims):
    base = {
        "P4712": [time("+1972-01-06T00:00:00Z", 11)],
        "P818": [item(SASSUOLO)],
        "P63": [item(SINGER)],
        "P1617": [item(ITALY)],
    }
    if gender:
        base["P1018"] = [item(gender)]
    base.update(claims)
    return {"id": NEK, "labels": {lang: "Nek" for lang in ("it", "en", "es", "fr", "de", "pt")},
            "claims": {k: v for k, v in base.items() if v is not None}}


BASE = {
    NEK: nek(),
    BOWIE: {
        "id": BOWIE,
        "labels": {"it": "David Bowie"},
        "claims": {
            "P4712": [time("+1947-01-08T00:00:00Z", 11)],
            "P818": [item(LONDON)],
            "P4723": [time("+2016-01-10T00:00:00Z", 11)],
            "P915": [item(NEW_YORK)],
            "P63": [item(SINGER)],
            "P1617": [item(UK)],
            "P1018": [item(MALE)],
        },
    },
    SASSUOLO: {"labels": {"it": "Sassuolo", "en": "Sassuolo"}},
    LONDON: {"labels": {"it": "Londra"}},
    NEW_YORK: {"labels": {"it": "New York"}},
    SINGER: {
        "labels": {"it": "cantautore", "en": "singer-songwriter", "es": "cantautor",
                   "fr": "auteur-compositeur-interprète", "de": "Liedermacher", "pt": "cantautor"},
        "claims": {"P1451": [mono("cantautrice", "it"), mono("cantautora", "es"),
                             mono("Liedermacherin", "de")]},
    },
    ACTOR: {"labels": {"it": "attore", "en": "actor"}, "claims": {"P1451": [mono("attrice", "it")]}},
    ITALY: {
        "labels": {"it": "Italia"},
        "claims": {"P489": [mono("italiano", "it"), mono("italiana", "it"), mono("Italian", "en"),
                            mono("italiano", "es"), mono("italiana", "es"), mono("italien", "fr"),
                            mono("italienne", "fr"), mono("italienisch", "de"), mono("Italiener", "de"),
                            mono("italiano", "pt")]},
    },
    UK: {"labels": {"it": "Regno Unito"}, "claims": {"P489": [mono("britannico", "it")]}},
}


def env(lang, entities=None, page_entity=NEK):
    e = LuaEnv(lang=lang, entities=entities or BASE, page_entity=page_entity, page_title="Pagina")
    e.lua.execute(_FORMAT_DATE)
    return e


def bio(lang="it", entities=None, page_entity=NEK, **args):
    e = env(lang, entities, page_entity)
    entity = e.lua.globals().mw.wikibase.getEntity(page_entity)
    return e.call("Module:SV/Bio", "_sentence", entity, lang, e.lua.table_from(args))


def with_nek(**kw):
    return dict(BASE, **{NEK: nek(**kw)})


NOTE = "pseudonimo di Filippo Neviani"


def test_it_male_living():
    assert bio(note=NOTE) == \
        "Nek, pseudonimo di Filippo Neviani (Sassuolo, 6 gennaio 1972), è un cantautore italiano"


def test_it_female():
    assert bio(entities=with_nek(gender=FEMALE), note=NOTE) == \
        "Nek, pseudonimo di Filippo Neviani (Sassuolo, 6 gennaio 1972), è una cantautrice italiana"


def test_it_dead_male():
    assert bio(page_entity=BOWIE, note="pseudonimo di David Robert Jones") == \
        "David Bowie, pseudonimo di David Robert Jones (Londra, 8 gennaio 1947 – New York, 10 gennaio 2016)," \
        " è stato un cantautore britannico"


def test_it_dead_female():
    ents = dict(BASE, **{BOWIE: dict(BASE[BOWIE], claims=dict(BASE[BOWIE]["claims"], P1018=[item(FEMALE)]))})
    # "britannico" alone: its feminine follows the -o → -a rule
    assert bio(page_entity=BOWIE, entities=ents) == \
        "David Bowie (Londra, 8 gennaio 1947 – New York, 10 gennaio 2016) è stata una cantautrice britannica"


def test_en():
    assert bio("en") == "Nek (born 6 January 1972 in Sassuolo) is an Italian singer-songwriter"


def test_es():
    assert bio("es") == "Nek (Sassuolo, 6 de enero de 1972) es un cantautor italiano"


def test_fr():
    assert bio("fr") == "Nek (Sassuolo, 6 janvier 1972) est un auteur-compositeur-interprète italien"


def test_de():
    assert bio("de") == "Nek (* 6. Januar 1972 in Sassuolo) ist ein italienischer Liedermacher"


def test_de_female():
    assert bio("de", entities=with_nek(gender=FEMALE)) == \
        "Nek (* 6. Januar 1972 in Sassuolo) ist eine italienische Liedermacherin"


def test_pt():
    assert bio("pt") == "Nek (Sassuolo, 6 de janeiro de 1972) é um cantautor italiano"


def test_unknown_gender():
    assert bio(entities=with_nek(gender=None)) == "Nek (Sassuolo, 6 gennaio 1972) è cantautore italiano"
    assert bio(entities=with_nek(gender="Q999")) == "Nek (Sassuolo, 6 gennaio 1972) è cantautore italiano"


def test_missing_birth_data():
    assert bio(entities=with_nek(P4712=None, P818=None)) == "Nek è un cantautore italiano"
    assert bio("en", entities=with_nek(P4712=None, P818=None)) == "Nek is an Italian singer-songwriter"


def test_birth_place_only_or_date_only():
    assert bio(entities=with_nek(P4712=None)) == "Nek (Sassuolo) è un cantautore italiano"
    assert bio("en", entities=with_nek(P818=None)) == "Nek (born 6 January 1972) is an Italian singer-songwriter"


def test_no_occupation():
    assert bio(entities=with_nek(P63=None), note=NOTE) == \
        "Nek, pseudonimo di Filippo Neviani (Sassuolo, 6 gennaio 1972)"


def test_rest_and_ref():
    out = bio(rest=", noto per Laura non c'è", ref="<ref>Fonte</ref>")
    assert out == "Nek (Sassuolo, 6 gennaio 1972) è un cantautore italiano, noto per Laura non c'è<ref>Fonte</ref>"
    assert bio(rest="attivo dal 1991") == "Nek (Sassuolo, 6 gennaio 1972) è un cantautore italiano attivo dal 1991"


def test_name_param():
    assert bio(name="Filippo Neviani").startswith("Filippo Neviani (Sassuolo")


def test_several_occupations():
    ents = with_nek(P63=[item(SINGER), item(ACTOR)])
    assert bio(entities=ents) == "Nek (Sassuolo, 6 gennaio 1972) è un cantautore e attore italiano"
    ents = with_nek(gender=FEMALE, P63=[item(SINGER), item(ACTOR)])
    assert bio(entities=ents) == "Nek (Sassuolo, 6 gennaio 1972) è una cantautrice e attrice italiana"


def test_occupation_without_label_in_language_is_left_out():
    ents = with_nek(P63=[item(ACTOR), item(SINGER)])
    assert bio("es", entities=ents) == "Nek (Sassuolo, 6 de enero de 1972) es un cantautor italiano"


def test_female_without_female_form_drops_agreement():
    # fr has no female form of the occupation: no article, plain label, no guessed nationality
    assert bio("fr", entities=with_nek(gender=FEMALE)) == \
        "Nek (Sassuolo, 6 janvier 1972) est auteur-compositeur-interprète"


def test_place_link():
    ents = dict(BASE, **{SASSUOLO: dict(BASE[SASSUOLO], sitelinks={"it": "Sassuolo"})})
    assert bio(entities=ents) == "Nek ([[Sassuolo|Sassuolo]], 6 gennaio 1972) è un cantautore italiano"


def test_no_categories_no_newline():
    for lang in ("it", "en", "es", "fr", "de", "pt"):
        for ents in (BASE, with_nek(P63=None), with_nek(gender=None), {NEK: {"labels": {}, "claims": {}}}):
            out = bio(lang, entities=ents, ref="<ref>x</ref>")
            assert "[[Category:" not in out and "\n" not in out and "()" not in out


def test_main_uses_page_item():
    e = env("it")
    frame = e.frame({}, {"note": NOTE})
    assert e.call("Module:SV/Bio", "main", frame) == \
        "Nek, pseudonimo di Filippo Neviani (Sassuolo, 6 gennaio 1972), è un cantautore italiano"


# --- nationality: masculine form and agreement by language rules ---

def nationality(lang, forms, female=False):
    e = env(lang)
    t = e.lua.table_from
    return e.call("Module:SV/Bio", "_demonym", lang, t(forms), female)


def test_demonym_it_es_pt():
    for lang in ("it", "es", "pt"):
        assert nationality(lang, ["italiana", "italiano"]) == "italiano"
        assert nationality(lang, ["italiano"], True) == "italiana"
        assert nationality(lang, ["italiana", "italiano"], True) == "italiana"
    assert nationality("it", ["francese"], True) == "francese"
    assert nationality("es", ["estadounidense"], True) == "estadounidense"
    # a lone form ending in -a might be the feminine itself: undecidable
    assert nationality("it", ["italiana"]) is None
    # consonant endings: no rule, so no feminine
    assert nationality("es", ["español"]) == "español"
    assert nationality("es", ["español"], True) is None
    # two unrelated forms: cannot tell which is masculine
    assert nationality("it", ["italiano", "italico"]) is None


def test_demonym_fr():
    assert nationality("fr", ["français"]) == "français"
    assert nationality("fr", ["français"], True) == "française"
    assert nationality("fr", ["belge"], True) == "belge"
    assert nationality("fr", ["française", "français"]) == "français"
    # -ien / -éen double the n
    assert nationality("fr", ["italienne", "italien"]) == "italien"
    assert nationality("fr", ["italien"], True) == "italienne"
    assert nationality("fr", ["européen"], True) == "européenne"
    # other final consonants that may double (-on, -el, -et) or change (grec → grecque): no rule
    assert nationality("fr", ["breton"], True) is None
    assert nationality("fr", ["grec"], True) is None
    assert nationality("fr", ["grec", "grecque"]) is None


def test_demonym_de():
    assert nationality("de", ["italienisch", "Italiener"]) == "italienischer"
    assert nationality("de", ["italienisch"], True) == "italienische"
    # a noun (Italiener) is not an adjective
    assert nationality("de", ["Italiener", "Italienerin"]) is None


def test_demonym_en():
    assert nationality("en", ["Italian"]) == "Italian"
    assert nationality("en", ["Italian"], True) == "Italian"
    assert nationality("en", ["British", "Briton"]) is None
    assert nationality("en", []) is None


def test_en_article():
    e = env("en")
    for word, art in (("Italian", "an"), ("actor", "an"), ("singer", "a"), ("Umbrian", "an"),
                      ("English", "an"), ("opera singer", "an"), ("British", "a")):
        assert e.call("Module:SV/Bio", "_article", "en", word, False) == art + " " + word


def test_it_article():
    e = env("it")
    cases = (("cantautore", False, "un cantautore"), ("scrittore", False, "uno scrittore"),
             ("zoologo", False, "uno zoologo"), ("psicologo", False, "uno psicologo"),
             ("gnomo", False, "uno gnomo"), ("attore", False, "un attore"),
             ("attrice", True, "un'attrice"), ("cantautrice", True, "una cantautrice"),
             ("scrittrice", True, "una scrittrice"))
    for word, female, expected in cases:
        assert e.call("Module:SV/Bio", "_article", "it", word, female) == expected


def test_es_conjunction():
    e = env("es")
    t = e.lua.table_from
    assert e.call("Module:SV/Bio", "_list", "es", t(["cantante", "actor"])) == "cantante y actor"
    assert e.call("Module:SV/Bio", "_list", "es", t(["cantante", "intérprete"])) == "cantante e intérprete"
    assert e.call("Module:SV/Bio", "_list", "es", t(["a", "b", "c"])) == "a, b y c"
