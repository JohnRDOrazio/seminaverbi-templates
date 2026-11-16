-- {{SV infobox work}} / {{SV infobox person}}: rows read from the page's Wikibase item, labelled in
-- the wiki's language. Field lists and property IDs come from Module:SV/Data; parameters override.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local function label(id, lang)
	return mw.wikibase.getLabelByLang(id, lang) or mw.wikibase.getLabelByLang(id, 'en')
end

local function itemLink(id, lang)
	local text = Util.escape(label(id, lang) or id)
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
		return Util.msg('date_decade', code, tostring(y - y % 10))
	end
	local n = math.floor((y - 1) / (precision == 7 and 100 or 1000)) + 1
	return Util.msg(precision == 7 and 'date_century' or 'date_millennium', code,
		roman(n), tostring(n), ordinalEn(n), roman(n) .. (n == 1 and 'er' or 'e'))
end

local function formatTime(value)
	local sign, year, month, day = value.time:match('^([+-])(%d+)-(%d%d)-(%d%d)')
	if not year then
		return Util.escape(value.time)
	end
	local y = tonumber(year)
	local precision = tonumber(value.precision) or 9
	local lang = mw.language.getContentLanguage()
	local code = lang:getCode()
	local coarse = precision >= 6 and precision <= 8 and y >= 1
	if sign == '-' then
		-- formatDate rejects years before 0000, so BC dates show at most the year.
		return Util.msg('bc_year', code, coarse and coarseDate(y, precision, code) or tostring(y))
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
		local d = tonumber(day)
		if d > gregorianDays(y, tonumber(month)) then
			-- a Julian-only day such as 29 February 1500: formatDate would roll it over to 1 March
			return d .. ' ' .. lang:formatDate('F Y', padded .. '-' .. month .. '-01')
		end
		return lang:formatDate('j F Y', padded .. '-' .. month .. '-' .. day)
	end
	return lang:formatDate('F Y', padded .. '-' .. month .. '-01')
end

local function values(entity, field, lang)
	local statements = entity and entity.claims and entity.claims[field.propId]
	-- Like Wikibase's "best statements": the preferred ones when there are any, else the normal ones.
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
			local v = dv.value
			if field.kind == 'item' then
				out[#out + 1] = itemLink(v.id, lang)
			elseif field.kind == 'time' then
				out[#out + 1] = formatTime(v)
			elseif field.kind == 'monolingual' then
				if v.language ~= lang then
					out[#out + 1] = "''" .. Util.escape(v.text) .. "''"
				end
			else
				out[#out + 1] = Util.escape(v)
			end
		end
		-- An image field holds one file: several values would join into one broken file link.
		local max = field.kind == 'image' and 1 or field.max
		if max and #out >= max then
			break
		end
	end
	if field.kind == 'monolingual' then
		return out[1]
	end
	return #out > 0 and table.concat(out, ', ') or nil
end

local function fieldLabel(field, lang)
	local override = Util.msg('label_' .. field.key, lang)
	if override ~= 'label_' .. field.key then
		return override
	end
	return Util.escape(label(field.propId, lang) or field.key)
end

function p._render(kind, args, ctx)
	local data = mw.loadData('Module:SV/Data')
	local lang = ctx.lang
	local infoboxType = kind == 'person' and 'person' or (args.type or 'book')
	local fields = data.infobox[infoboxType] or data.infobox.book
	local id = args.item or ctx.entity_id
	local entity = id and mw.wikibase.getEntity(id)

	local caption = args.name
		or (entity and id and Util.escape(label(id, lang) or ''))
		or Util.escape(mw.title.getCurrentTitle().text)
	if caption == '' then
		caption = Util.escape(mw.title.getCurrentTitle().text)
	end

	local rows, image = {}, nil
	local fromItem = 0
	for _, f in ipairs(fields) do
		local field = { key = f.key, kind = f.kind, max = f.max, propId = data.wikibase[f.prop] }
		local value = args[f.key]
		if not value then
			value = values(entity, field, lang)
			if value then
				fromItem = fromItem + 1
			end
		elseif f.kind == 'image' then
			value = Util.escape(value)
		end
		if value then
			if f.kind == 'image' then
				image = '[[File:' .. value .. '|frameless|upright=1.15]]'
			elseif f.kind == 'monolingual' then
				rows[#rows + 1] = '<tr><td colspan="2" class="sv-infobox-original">' .. value .. '</td></tr>'
			else
				rows[#rows + 1] = '<tr><th>' .. fieldLabel(field, lang) .. '</th><td>' .. value .. '</td></tr>'
			end
		end
	end

	local out = {
		-- A tag returned as a string is shown as text; it has to go through extensionTag.
		ctx.frame:extensionTag('templatestyles', '', { src = 'SV infobox/styles.css' }),
		'<table class="sv-infobox sv-infobox-' .. infoboxType .. '">',
		'<caption>' .. caption .. '</caption>',
	}
	if image then
		out[#out + 1] = '<tr><td colspan="2" class="sv-infobox-image">' .. image .. '</td></tr>'
	end
	for _, r in ipairs(rows) do
		out[#out + 1] = r
	end
	out[#out + 1] = '</table>'
	if not entity or fromItem == 0 then
		out[#out + 1] = '[[Category:' .. Util.msg('infobox_missing_category', lang) .. ']]'
	end
	return table.concat(out, '\n')
end

function p.work(frame)
	return p._render('work', Args.get(frame), {
		lang = Util.lang(),
		frame = frame,
		entity_id = mw.wikibase.getEntityIdForCurrentPage(),
	})
end

function p.person(frame)
	return p._render('person', Args.get(frame), {
		lang = Util.lang(),
		frame = frame,
		entity_id = mw.wikibase.getEntityIdForCurrentPage(),
	})
end

return p
