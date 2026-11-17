import json

from conftest import ROOT
from lua_env import LuaEnv


def link(lang="en", *positional):
    # Scribunto passes positional template parameters with numeric keys
    env = LuaEnv(lang=lang)
    return env.call("Module:SV/Cathopedia", "link", env.frame({}, dict(enumerate(positional, 1))))


def msg(lang, key):
    return json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))[key]


def test_cathopedia_it_no_note():
    assert link("it", "Concilio Vaticano II") == (
        "[[cathopedia:Concilio Vaticano II|Concilio Vaticano II]]"
        f"[[Category:{msg('it', 'cathopedia_category')}]]")


def test_cathopedia_other_lang_note():
    assert link("fr", "Concilio Vaticano II", "Concile Vatican II") == (
        f"[[cathopedia:Concilio Vaticano II|Concile Vatican II]] {msg('fr', 'cathopedia_note')}"
        f"[[Category:{msg('fr', 'cathopedia_category')}]]")


def test_cathopedia_missing_title():
    out = link("de")
    assert out.startswith('<strong class="error">')
    assert msg("de", "cathopedia_missing_title") in out
    assert "[[cathopedia:" not in out


def test_cathopedia_brackets_in_text():
    out = link("en", "X", "a]]b")
    assert out.startswith("[[cathopedia:X|a&#93;&#93;b]]")
