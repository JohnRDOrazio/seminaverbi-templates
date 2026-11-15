from lua_env import LuaEnv


def call(fn, lang="en", **args):
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/Sections", fn, env.frame(args, {}))


def test_discernment_heading_it():
    assert call("discernment", "it") == "== Semi del Verbo e punti di attenzione ==\n"


def test_discernment_heading_en():
    assert call("discernment") == "== Seeds of the Word and points of caution ==\n"


def test_wip_fills_user_and_date():
    out = call("wip", "it", user="Johnrdorazio", date="16 settembre 2020")
    assert out.startswith('<div class="sv-wip">')
    assert "Questa voce è in lavorazione (Johnrdorazio, 16 settembre 2020)." in out
    assert out.endswith("[[Category:Voci SV in lavorazione]]")


def test_wip_escapes_user():
    assert "<b>" not in call("wip", user="<b>x", date="d")


def test_references_group():
    assert call("references") == "== References ==\n<references />"
    assert call("references", "fr", group="n") == '== Références ==\n<references group="n" />'


def test_managed_links_repo():
    out = call("managed")
    assert out.startswith('<div class="sv-managed">')
    assert "https://github.com/JohnRDOrazio/seminaverbi-templates" in out
