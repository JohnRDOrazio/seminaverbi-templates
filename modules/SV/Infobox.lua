-- {{SV infobox work}} / {{SV infobox person}}: rows read from the page's Wikibase item, labelled in
-- the wiki's language. Field lists and property IDs come from Module:SV/Data; parameters override.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local label, itemLink, formatTime = Util.label, Util.itemLink, Util.formatTime

local function values(entity, field, lang)
	local out = {}
	for _, v in ipairs(Util.best(entity, field.propId)) do
		if field.kind == 'item' then
			out[#out + 1] = itemLink(Util.place(v.id, field.climb, lang), lang)
		elseif field.kind == 'time' then
			out[#out + 1] = formatTime(v)
		elseif field.kind == 'monolingual' then
			if v.language ~= lang then
				out[#out + 1] = "''" .. Util.escape(v.text) .. "''"
			end
		else
			out[#out + 1] = Util.escape(v)
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

-- Tree item IDs from Module:SV/Data: field nodes (any level below the root) and leaf Gospel themes.
local function treeItems(data)
	local fields, themes = {}, {}
	local function walkFields(nodes)
		for _, n in ipairs(nodes or {}) do
			if n.item then
				fields[n.item] = true
			end
			walkFields(n.children)
		end
	end
	local function walkThemes(nodes)
		for _, n in ipairs(nodes or {}) do
			if n.children and n.children[1] then
				walkThemes(n.children)
			elseif n.item then
				themes[n.item] = true
			end
		end
	end
	walkFields(data.categories.fields)
	walkThemes(data.categories.themes)
	return fields, themes
end

local itemIds = Util.itemIds

-- [[Category:…]] for the item's SV field (one) and SV Gospel themes, in the wiki's language; anything
-- unexpected goes to a tracking category instead of being dropped silently.
function p._categories(entity, lang, sortkey)
	if not entity then
		return ''
	end
	local data = mw.loadData('Module:SV/Data')
	local fieldItems, themeItems = treeItems(data)
	local out, seen, tracked = {}, {}, {}
	sortkey = sortkey and (sortkey:gsub('&#32;', ' ')) -- a blank sort key, which template parameters cannot carry
	local suffix = sortkey and sortkey ~= '' and ('|' .. sortkey) or ''
	local function track(key)
		if not tracked[key] then
			tracked[key] = true
			out[#out + 1] = '[[Category:' .. Util.msg(key, lang) .. ']]'
		end
	end
	local function add(id, allowed)
		if not allowed[id] then
			track('category_not_tree')
			return
		end
		local title = mw.wikibase.getSitelink(id)
		if not title then
			track('category_not_on_wiki')
			return
		end
		local name = title:match('^[^:]+:(.+)$') or title
		if not seen[name] then
			seen[name] = true
			out[#out + 1] = '[[Category:' .. name .. suffix .. ']]'
		end
	end
	local fields = itemIds(entity, data.wikibase.sv_field)
	if fields[1] then
		add(fields[1], fieldItems)
	end
	if fields[2] then
		track('category_two_fields')
	end
	for _, id in ipairs(itemIds(entity, data.wikibase.sv_theme)) do
		add(id, themeItems)
	end
	return table.concat(out, '\n')
end

-- The layout for an item: the first of its "instance of" values that config/infobox_types.json maps,
-- else person for a human, else the generic work layout (second result true: the type is unknown).
function p.layoutFor(entity, data)
	local types = data.infobox_types.types
	local values = itemIds(entity, data.wikibase.instance_of)
	for _, id in ipairs(values) do
		if types[id] then
			return types[id], false
		end
	end
	return 'work', true
end

function p._render(kind, args, ctx)
	local data = mw.loadData('Module:SV/Data')
	local lang = ctx.lang
	local id = args.item or ctx.entity_id
	local entity = id and mw.wikibase.getEntity(id)
	local infoboxType, unknownType
	if kind == 'person' then
		infoboxType = 'person'
	elseif kind == 'auto' and not args.type then
		infoboxType, unknownType = p.layoutFor(entity, data)
	else
		infoboxType = args.type or 'book'
	end
	local fields = data.infobox[infoboxType] or data.infobox.book

	local caption = args.name
		or (entity and id and Util.escape(label(id, lang) or ''))
		or Util.escape(mw.title.getCurrentTitle().text)
	if caption == '' then
		caption = Util.escape(mw.title.getCurrentTitle().text)
	end

	local rows, image = {}, nil
	local fromItem = 0
	for _, f in ipairs(fields) do
		local field = { key = f.key, kind = f.kind, max = f.max, climb = f.climb, propId = data.wikibase[f.prop] }
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
		'<caption><span class="sv-infobox-kind">' .. Util.msg('infobox_kind_' .. infoboxType, lang)
			.. '</span>' .. caption .. '</caption>',
	}
	if image then
		out[#out + 1] = '<tr><td colspan="2" class="sv-infobox-image">' .. image .. '</td></tr>'
	end
	for _, r in ipairs(rows) do
		out[#out + 1] = r
	end
	if entity and id then
		-- Where the facts come from: the item on the repository wiki (en), reached by interwiki elsewhere.
		local target = (lang == 'en' and '' or 'seminaverbi:') .. 'Item:' .. id
		out[#out + 1] = '<tr><td colspan="2" class="sv-infobox-source">'
			.. Util.msg('infobox_source', lang, '[[' .. target .. '|' .. id .. ']]') .. '</td></tr>'
	end
	out[#out + 1] = '</table>'
	if not entity or fromItem == 0 then
		out[#out + 1] = '[[Category:' .. Util.msg('infobox_missing_category', lang) .. ']]'
	end
	if unknownType then
		out[#out + 1] = '[[Category:' .. Util.msg('infobox_unknown_type', lang) .. ']]'
	end
	local cats = p._categories(entity, lang, args.sortkey)
	if cats ~= '' then
		out[#out + 1] = cats
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

function p.auto(frame)
	return p._render('auto', Args.get(frame), {
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

function p.categories(frame)
	local args = Args.get(frame)
	local id = args.item or mw.wikibase.getEntityIdForCurrentPage()
	local entity = id and mw.wikibase.getEntity(id)
	return p._categories(entity, Util.lang(), args.sortkey)
end

return p
