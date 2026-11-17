-- Shared helpers: HTML escaping, UI strings with language fallback, and reading Wikibase values
-- (labels, item links, dates), used by Module:SV/Infobox and Module:SV/Bio.
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

-- An entity's label in lang, else in English.
function p.label(id, lang)
	return mw.wikibase.getLabelByLang(id, lang) or mw.wikibase.getLabelByLang(id, 'en')
end

-- A link to an item: its page on this wiki, else its Wikipedia article in lang, else its label.
function p.itemLink(id, lang)
	local text = p.escape(p.label(id, lang) or id)
	local localTitle = mw.wikibase.getSitelink(id)
	if localTitle then
		return '[[' .. localTitle .. '|' .. text .. ']]'
	end
	local wpTitle = mw.wikibase.getSitelink(id, lang .. 'wiki')
	if wpTitle then
		return '[[wikipedia:' .. lang .. ':' .. wpTitle .. '|' .. text .. ']]'
	end
	return text
end

local GREGORIAN_DAYS = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }

local function gregorianDays(y, m)
	if m == 2 and y % 4 == 0 and (y % 100 ~= 0 or y % 400 == 0) then
		return 29
	end
	return GREGORIAN_DAYS[m] or 31
end

local ROMAN = { { 1000, 'M' }, { 900, 'CM' }, { 500, 'D' }, { 400, 'CD' }, { 100, 'C' }, { 90, 'XC' },
	{ 50, 'L' }, { 40, 'XL' }, { 10, 'X' }, { 9, 'IX' }, { 5, 'V' }, { 4, 'IV' }, { 1, 'I' } }

local function roman(n)
	local out = {}
	for _, pair in ipairs(ROMAN) do
		while n >= pair[1] do
			out[#out + 1] = pair[2]
			n = n - pair[1]
		end
	end
	return table.concat(out)
end

local function ordinalEn(n)
	local t = n % 100
	local suffix = (t < 11 or t > 13) and ({ 'st', 'nd', 'rd' })[n % 10] or 'th'
	return n .. suffix
end

-- Decade (8), century (7) or millennium (6) of a positive year. Message parameters:
-- $1 Roman numeral (decade: the decade's first year), $2 number, $3 English ordinal, $4 French ordinal.
local function coarseDate(y, precision, code)
	if precision == 8 then
		return p.msg('date_decade', code, tostring(y - y % 10))
	end
	local n = math.floor((y - 1) / (precision == 7 and 100 or 1000)) + 1
	return p.msg(precision == 7 and 'date_century' or 'date_millennium', code,
		roman(n), tostring(n), ordinalEn(n), roman(n) .. (n == 1 and 'er' or 'e'))
end

-- A Wikibase time value at its precision, in the content language.
function p.formatTime(value)
	local sign, year, month, day = value.time:match('^([+-])(%d+)-(%d%d)-(%d%d)')
	if not year then
		return p.escape(value.time)
	end
	local y = tonumber(year)
	local precision = tonumber(value.precision) or 9
	local lang = mw.language.getContentLanguage()
	local code = lang:getCode()
	local coarse = precision >= 6 and precision <= 8 and y >= 1
	if sign == '-' then
		-- formatDate rejects years before 0000, so BC dates show at most the year.
		return p.msg('bc_year', code, coarse and coarseDate(y, precision, code) or tostring(y))
	end
	if coarse then
		return coarseDate(y, precision, code)
	end
	if precision <= 9 then
		return tostring(y)
	end
	-- formatDate needs a four-digit year.
	local padded = string.format('%04d', y)
	if precision >= 11 then
		-- the day format starts with the day number (j), e.g. it "j F Y", de "j. F Y", es 'j "de" F "de" Y'
		local dayFormat = p.msg('date_format_day', code)
		local d = tonumber(day)
		if d > gregorianDays(y, tonumber(month)) then
			-- a Julian-only day such as 29 February 1500: formatDate would roll it over to 1 March
			return d .. lang:formatDate(dayFormat:sub(2), padded .. '-' .. month .. '-01')
		end
		return lang:formatDate(dayFormat, padded .. '-' .. month .. '-' .. day)
	end
	return lang:formatDate(p.msg('date_format_month', code), padded .. '-' .. month .. '-01')
end

-- The datavalue values of an entity's best statements for propId: the preferred ones when there are
-- any, else the normal ones (as Wikibase does). Statements with no value or unknown value are skipped.
function p.best(entity, propId)
	local statements = entity and entity.claims and propId and entity.claims[propId]
	local wanted = 'normal'
	for _, statement in ipairs(statements or {}) do
		if statement.rank == 'preferred' then
			wanted = 'preferred'
			break
		end
	end
	local out = {}
	for _, statement in ipairs(statements or {}) do
		local snak = statement.mainsnak
		local dv = snak and snak.snaktype == 'value' and snak.datavalue
		if dv and (statement.rank or 'normal') == wanted then
			out[#out + 1] = dv.value
		end
	end
	return out
end

return p
