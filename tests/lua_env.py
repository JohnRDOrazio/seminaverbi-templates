"""A Lua 5.1 runtime with just enough of Scribunto's mw library to unit-test Module:SV/*."""
from __future__ import annotations

from pathlib import Path

import lupa.lua51 as lupa

ROOT = Path(__file__).resolve().parent.parent

_PRELUDE = r"""
local py = ...
local cache = {}
mw = { text = {}, ustring = {}, wikibase = {}, language = {}, title = {} }
-- mw.ustring is byte-based here except for the case and substring functions, which are
-- Unicode-aware as in Scribunto (so tests catch ASCII-only %l / lower checks).
for k, v in pairs(string) do mw.ustring[k] = v end
mw.ustring.lower, mw.ustring.upper, mw.ustring.sub = py.ulower, py.uupper, py.usub
function mw.text.trim(s) return (tostring(s):gsub('^%s+', ''):gsub('%s+$', '')) end
-- as MediaWiki's: the characters that could start markup become numeric entities
function mw.text.nowiki(s)
    return (tostring(s):gsub('["&\'<=>%[%]{|}]', function(c) return '&#' .. c:byte() .. ';' end))
end
local function load(name)
    if cache[name] == nil then
        if name == 'Module:SV/Data' then
            cache[name] = py.data()
        else
            local src = py.source(name)
            if src == nil then error('module not found: ' .. name) end
            local chunk = assert(loadstring(src, '=' .. name))
            cache[name] = chunk()
        end
    end
    return cache[name]
end
require = load
mw.loadData = load
local lang = { code = py.lang }
function lang:getCode() return self.code end
function lang:formatDate(fmt, iso)
    -- Scribunto hands the string to PHP DateTime and rejects years before 0000: mimic that.
    if not iso:match('^%d%d%d%d%-%d%d%-%d%d$') then error('bad date for formatDate: ' .. iso) end
    -- PHP DateTime throws on an impossible month or day number (it rolls over e.g. 02-30 silently).
    local m, d = tonumber(iso:sub(6, 7)), tonumber(iso:sub(9, 10))
    if m < 1 or m > 12 or d > 31 then error('bad date for formatDate: ' .. iso) end
    return fmt .. '|' .. iso
end
function mw.language.getContentLanguage() return lang end
-- a few CLDR names, as Scribunto's mw.language.fetchLanguageName(code, inLanguage) gives them ('' when unknown)
local LANGUAGE_NAMES = {
    it = { en = 'inglese', it = 'italiano', la = 'latino', de = 'tedesco' },
    en = { en = 'English', it = 'Italian', la = 'Latin', de = 'German' },
    de = { en = 'Englisch', it = 'Italienisch', de = 'Deutsch' },
}
function mw.language.fetchLanguageName(code, inLanguage)
    return (LANGUAGE_NAMES[inLanguage] or {})[code] or ''
end
function mw.title.getCurrentTitle() return { text = py.page_title, namespace = py.namespace } end
function mw.wikibase.getEntityIdForCurrentPage() return py.page_entity end
function mw.wikibase.getEntity(id) return py.entity(id) end
function mw.wikibase.getLabelByLang(id, l) return py.label(id, l) end
function mw.wikibase.getSitelink(id, site) return py.sitelink(id, site or py.lang) end
-- mw.site.stats.pagesInCategory(name, 'pages'): the counts a test gives (0 otherwise); every call is
-- recorded, since each one is an expensive parser function call on the wiki.
mw.site = { stats = {} }
function mw.site.stats.pagesInCategory(name, which) return py.pages_in_category(name, which) end
function make_frame(args, pargs)
    local parent = { args = pargs }
    local f = { args = args, getParent = function() return parent end }
    -- Scribunto's frame:extensionTag returns a strip marker; the mock returns a visible one
    -- (see tag() in lua_env.py) so tests can tell a real tag call from a literal tag string.
    function f:extensionTag(name, content, attrs)
        local keys = {}
        for k in pairs(attrs or {}) do keys[#keys + 1] = k end
        table.sort(keys)
        local parts = {}
        for _, k in ipairs(keys) do parts[#parts + 1] = k .. '=' .. attrs[k] end
        return '\127TAG:' .. name .. '|' .. table.concat(parts, ',') .. '|' .. (content or '') .. '\127'
    end
    return f
end
"""


class LuaEnv:
    def __init__(self, lang="en", data=None, entities=None, page_entity=None, page_title="Test page",
                 patch_data=None, category_counts=None, namespace=0):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lang = lang
        self.entities = entities or {}
        self._data = data
        self._patch = patch_data
        self.category_counts = category_counts or {}
        self.category_calls = []
        table = self.lua.table_from
        bridge = {
            "lang": lang,
            "page_title": page_title,
            "namespace": namespace,
            "page_entity": page_entity,
            "ulower": lambda x: x.lower(),
            "uupper": lambda x: x.upper(),
            "usub": lambda x, i, j=-1: x[(i - 1 if i > 0 else max(len(x) + i, 0)):(len(x) + j + 1 if j < 0 else j)],
            "data": self._load_data,
            "source": self._source,
            "entity": lambda i: table(self.entities[i], recursive=True) if i in self.entities else None,
            "label": lambda i, l: self.entities.get(i, {}).get("labels", {}).get(l),
            "sitelink": lambda i, s: self.entities.get(i, {}).get("sitelinks", {}).get(s),
            "pages_in_category": self._pages_in_category,
        }
        self.lua.execute(_PRELUDE, table(bridge))
        self._make_frame = self.lua.globals().make_frame

    def _pages_in_category(self, name, which):
        self.category_calls.append((name, which))
        return self.category_counts.get(name, 0)

    def _load_data(self):
        if self._data is not None:
            return self.lua.table_from(self._data, recursive=True)
        try:
            from sync.pages import compile_data
        except ImportError:
            return self.lua.table()
        table = self.lua.execute(compile_data(ROOT))
        if self._patch:
            self._patch(table)
        return table

    @staticmethod
    def _source(name):
        prefix = "Module:SV/"
        if not name.startswith(prefix):
            return None
        path = ROOT / "modules" / "SV" / (name[len(prefix):] + ".lua")
        return path.read_text(encoding="utf-8") if path.exists() else None

    def require(self, name):
        return self.lua.globals().require(name)

    def call(self, module, fn, *args):
        return self.require(module)[fn](*args)

    def frame(self, args=None, parent_args=None):
        t = self.lua.table_from
        return self._make_frame(t(args or {}), t(parent_args or {}))


def tag(name, content="", **attrs):
    """What the mock frame:extensionTag returns for this call."""
    a = ",".join(f"{k}={attrs[k]}" for k in sorted(attrs))
    return f"\x7fTAG:{name}|{a}|{content}\x7f"
