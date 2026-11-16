-- Shared helpers: HTML escaping and UI strings with language fallback.
local p = {}

local ENTITIES = { ['&'] = '&amp;', ['<'] = '&lt;', ['>'] = '&gt;', ['"'] = '&quot;' }

function p.escape(s)
	return (tostring(s):gsub('[&<>"]', ENTITIES))
end

-- UI string for key in lang, falling back to English, then to the key itself.
-- Extra arguments replace $1, $2, ...
function p.msg(key, lang, ...)
	local i18n = mw.loadData('Module:SV/Data').i18n or {}
	local text = (i18n[lang] or {})[key] or (i18n.en or {})[key] or key
	local params = { ... }
	return (text:gsub('%$(%d)', function(n)
		return params[tonumber(n)] or ('$' .. n)
	end))
end

function p.lang()
	return mw.language.getContentLanguage():getCode()
end

return p
