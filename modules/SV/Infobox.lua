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

local function formatTime(value)
	local sign, year, month, day = value.time:match('^([+-])(%d+)-(%d%d)-(%d%d)')
	if not year then
		return Util.escape(value.time)
	end
	year = tostring(tonumber(year))
	if sign == '-' then
		year = '-' .. year
	end
	local precision = tonumber(value.precision) or 9
	local lang = mw.language.getContentLanguage()
	if precision >= 11 then
		return lang:formatDate('j F Y', year .. '-' .. month .. '-' .. day)
	elseif precision == 10 then
		return lang:formatDate('F Y', year .. '-' .. month .. '-01')
	end
	return year
end

local function values(entity, field, lang)
	local statements = entity and entity.claims and entity.claims[field.propId]
	local out = {}
	for _, statement in ipairs(statements or {}) do
		local snak = statement.mainsnak
		local dv = snak and snak.snaktype == 'value' and snak.datavalue
		if dv and statement.rank ~= 'deprecated' then
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
		if field.max and #out >= field.max then
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
