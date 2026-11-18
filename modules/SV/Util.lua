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

-- The row of an infobox layout (config/infobox.json) with this key, or nil. {{SV bio}} reads the
-- person rows so the sentence and the infobox show the same facts.
function p.layoutRow(layout, key)
	for _, row in ipairs(mw.loadData('Module:SV/Data').infobox[layout] or {}) do
		if row.key == key then
			return row
		end
	end
	return nil
end

function p.lang()
	return mw.language.getContentLanguage():getCode()
end

-- An entity's label in lang, else in English.
function p.label(id, lang)
	return mw.wikibase.getLabelByLang(id, lang) or mw.wikibase.getLabelByLang(id, 'en')
end

-- A link to an item: its page on this wiki, else its Wikipedia article in lang, else its label.
-- text, when given, replaces the label as the link text; it is shown literally, markup and all.
function p.itemLink(id, lang, text)
	text = text and mw.text.nowiki(text) or p.escape(p.label(id, lang) or id)
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

local JULIAN = 'http://www.wikidata.org/entity/Q1985786'

-- The Gregorian date of a Julian calendar date, through the Julian day number.
local function julianToGregorian(y, m, d)
	local a = math.floor((14 - m) / 12)
	local yy, mm = y + 4800 - a, m + 12 * a - 3
	local jdn = d + math.floor((153 * mm + 2) / 5) + 365 * yy + math.floor(yy / 4) - 32083
	local b = jdn + 32044
	local c = math.floor((4 * b + 3) / 146097)
	local e = b - math.floor(146097 * c / 4)
	local f = math.floor((4 * e + 3) / 1461)
	local g = e - math.floor(1461 * f / 4)
	local h = math.floor((5 * g + 2) / 153)
	local day = g - math.floor((153 * h + 2) / 5) + 1
	local month = h + 3 - 12 * math.floor(h / 10)
	return 100 * c + f - 4800 + math.floor(h / 10), month, day
end

-- A Wikibase time value at its precision, in the content language. A Julian calendar day stored as
-- 1582-10-15 or later is shown as its Gregorian date, as reference works do (Bach: Julian 21 March
-- 1685 = 31 March 1685); earlier dates, and coarser precisions, are shown as stored.
function p.formatTime(value)
	local sign, year, month, day = value.time:match('^([+-])(%d+)-(%d%d)-(%d%d)')
	if not year then
		return p.escape(value.time)
	end
	local y = tonumber(year)
	local precision = tonumber(value.precision) or 9
	if value.calendarmodel == JULIAN and sign == '+' and precision >= 11
		and y * 10000 + tonumber(month) * 100 + tonumber(day) >= 15821015 then
		local gy, gm, gd = julianToGregorian(y, tonumber(month), tonumber(day))
		y, year, month, day = gy, string.format('%04d', gy), string.format('%02d', gm), string.format('%02d', gd)
	end
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

-- The item IDs of every non-deprecated statement for propId, whatever its rank, in item order.
function p.itemIds(entity, propId)
	local out = {}
	for _, statement in ipairs(entity and entity.claims and propId and entity.claims[propId] or {}) do
		local snak = statement.mainsnak
		local dv = snak and snak.snaktype == 'value' and snak.datavalue
		if dv and dv.value and dv.value.id and statement.rank ~= 'deprecated' then
			out[#out + 1] = dv.value.id
		end
	end
	return out
end

-- The most levels of "subclass of" between a place's class and a class in the climb list.
local SUBCLASS_STEPS = 3

-- The most steps a place climbs: Wikidata nests a street in a borough in a city.
local CLIMB_STEPS = 4

-- The place to show for a place item. Wikidata often records a birth or death place at its finest
-- grain (Brixton, Lafayette Street) where reference works name the town (London, New York): while
-- the place is an instance of one of the classes in climb (a place row's "climb" list in
-- config/infobox.json), go up its best "located in the administrative territorial entity" value.
-- The climb stops at a place of any other class, where the data ends, or before a place with no
-- label to show; it returns the last place reached.
function p.place(id, climb, lang)
	if not (id and climb and climb[1]) then
		return id
	end
	local wb = mw.loadData('Module:SV/Data').wikibase
	local fine = {}
	for _, class in ipairs(climb) do
		fine[class] = true
	end
	-- a class in the list, or a subclass of one (hospital building -> building) at most SUBCLASS_STEPS
	-- levels up. Breadth first, so each ancestor is reached first by its shortest path and a path cut by
	-- the depth limit cannot hide a shorter one through the same ancestor; only whole answers are kept.
	local known = {}
	local function isFineClass(class)
		if fine[class] then
			return true
		end
		if known[class] ~= nil or not wb.subclass_of then
			return known[class] or false
		end
		local seen, level = { [class] = true }, { class }
		for _ = 1, SUBCLASS_STEPS do
			local nextLevel = {}
			for _, c in ipairs(level) do
				for _, parent in ipairs(p.itemIds(mw.wikibase.getEntity(c), wb.subclass_of)) do
					if fine[parent] then
						known[class] = true
						return true
					end
					if not seen[parent] then
						seen[parent] = true
						nextLevel[#nextLevel + 1] = parent
					end
				end
			end
			level = nextLevel
		end
		known[class] = false
		return false
	end
	for _ = 1, CLIMB_STEPS do
		local entity = mw.wikibase.getEntity(id)
		local isFine = false
		for _, class in ipairs(p.itemIds(entity, wb.instance_of)) do
			isFine = isFine or isFineClass(class)
		end
		local parent = isFine and p.best(entity, wb.located_in)[1]
		if not (parent and parent.id and p.label(parent.id, lang)) then
			return id
		end
		id = parent.id
	end
	return id
end

return p
