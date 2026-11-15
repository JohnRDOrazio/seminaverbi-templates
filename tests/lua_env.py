"""A Lua 5.1 runtime with just enough of Scribunto's mw library to unit-test Module:SV/*."""
from __future__ import annotations

from pathlib import Path

import lupa.lua51 as lupa

ROOT = Path(__file__).resolve().parent.parent

_PRELUDE = r"""
local py = ...
local cache = {}
mw = { text = {}, ustring = string, wikibase = {}, language = {}, title = {} }
function mw.text.trim(s) return (tostring(s):gsub('^%s+', ''):gsub('%s+$', '')) end
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
function lang:formatDate(fmt, iso) return fmt .. '|' .. iso end
function mw.language.getContentLanguage() return lang end
function mw.title.getCurrentTitle() return { text = py.page_title } end
function mw.wikibase.getEntityIdForCurrentPage() return py.page_entity end
function mw.wikibase.getEntity(id) return py.entity(id) end
function mw.wikibase.getLabelByLang(id, l) return py.label(id, l) end
function mw.wikibase.getSitelink(id, site) return py.sitelink(id, site or py.lang) end
function make_frame(args, pargs)
    local parent = { args = pargs }
    return { args = args, getParent = function() return parent end }
end
"""


class LuaEnv:
    def __init__(self, lang="en", data=None, entities=None, page_entity=None, page_title="Test page"):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lang = lang
        self.entities = entities or {}
        self._data = data
        table = self.lua.table_from
        bridge = {
            "lang": lang,
            "page_title": page_title,
            "page_entity": page_entity,
            "data": self._load_data,
            "source": self._source,
            "entity": lambda i: table(self.entities[i], recursive=True) if i in self.entities else None,
            "label": lambda i, l: self.entities.get(i, {}).get("labels", {}).get(l),
            "sitelink": lambda i, s: self.entities.get(i, {}).get("sitelinks", {}).get(s),
        }
        self.lua.execute(_PRELUDE, table(bridge))
        self._make_frame = self.lua.globals().make_frame

    def _load_data(self):
        if self._data is not None:
            return self.lua.table_from(self._data, recursive=True)
        try:
            from sync.pages import compile_data
        except ImportError:
            return self.lua.table()
        return self.lua.execute(compile_data(ROOT))

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
