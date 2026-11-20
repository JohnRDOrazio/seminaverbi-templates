-- The box of one article proposal (a page on the English wiki, where trusted users vote with VoteNY).
-- The pipeline bot fills the parameters; any of them may be missing or empty. Names of the field
-- and the themes come from Module:SV/Data in the page language.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local STATUSES = { open = true, approved = true, created = true, declined = true }
local CONNECTIONS = { direct = true, seeds = true, entertainment = true, none = true }
local FARM_HOST = '.seminaverbi.bibleget.io/wiki/'

-- Text shown literally: no wiki markup can come out of it.
local function plain(s)
	return mw.text.nowiki(s)
end

-- 0.83 -> "83%"; nil when it is not a number.
local function percent(value)
	local n = tonumber(value)
	if not n then
		return nil
	end
	return string.format('%d%%', math.floor(n * 100 + 0.5))
end

-- A URL as an external link, or its text when it is not a plain http(s) URL.
local function externalLink(url, label)
	if url:match('^https?://[^%s%[%]<>"|{}]+$') then
		return '[' .. url .. (label and (' ' .. label) or '') .. ']'
	end
	return plain(url)
end

-- The name of a node of the category tree (config/categories.json) with this key, in lang.
local function nodeName(nodes, key, lang)
	for _, node in ipairs(nodes or {}) do
		if node.key == key then
			return node.names[lang] or node.names.en
		end
		local found = node.children and nodeName(node.children, key, lang)
		if found then
			return found
		end
	end
	return nil
end

local function themeName(categories, key, lang)
	return nodeName(categories.themes, key, lang) or nodeName(categories.fields, key, lang) or key
end

local function row(label, value)
	return '<div class="sv-proposal-row"><span class="sv-proposal-label">' .. label .. '</span> ' .. value .. '</div>'
end

local function withPercent(text, value)
	local pct = percent(value)
	return pct and (text .. ' <span class="sv-proposal-p">(' .. pct .. ')</span>') or text
end

local function themes(args, categories, lang, out)
	local items = {}
	local i = 1
	while args['theme' .. i] do
		local status = args['theme' .. i .. '_status']
		local line = '<li' .. (status and (' class="sv-proposal-theme-' .. Util.escape((status:gsub('[^%w%-]', ''))) .. '"') or '') .. '>'
			.. withPercent('<strong>' .. Util.escape(themeName(categories, args['theme' .. i], lang)) .. '</strong>',
				args['theme' .. i .. '_p'])
		local evidence, source = args['theme' .. i .. '_evidence'], args['theme' .. i .. '_source']
		if evidence then
			line = line .. '<div class="sv-proposal-excerpt">' .. plain(evidence)
				.. (source and (' ' .. externalLink(source)) or '') .. '</div>'
		end
		items[#items + 1] = line .. '</li>'
		i = i + 1
	end
	if #items > 0 then
		out[#out + 1] = '<div class="sv-proposal-row"><span class="sv-proposal-label">' .. Util.msg('proposal_themes', lang)
			.. '</span><ul>' .. table.concat(items) .. '</ul></div>'
	end
end

function p.main(frame)
	local args = Args.get(frame)
	local lang = Util.lang()
	local categories = mw.loadData('Module:SV/Data').categories
	local status = args.status
	if not STATUSES[status] then
		status = 'open'
	end
	local qid = args.qid and args.qid:match('^Q%d+$')
	local label = args['label_' .. lang] or args.label_en or qid or ''
	local firstWiki = args.first_wiki and args.first_wiki:match('^%a%a$')

	local out = {
		frame:extensionTag('templatestyles', '', { src = 'SV proposal/styles.css' }),
		'<div class="sv-proposal notheme">',
		'<div class="sv-proposal-title">' .. Util.msg('proposal_title', lang, '<strong>' .. plain(label) .. '</strong>') .. '</div>',
	}
	local state = Util.msg('proposal_status_' .. status, lang)
	local meta = {}
	if qid then
		meta[#meta + 1] = '<span class="sv-proposal-wikidata">' .. externalLink('https://www.wikidata.org/wiki/' .. qid, qid) .. '</span>'
	end
	if args.domain then
		meta[#meta + 1] = plain(args.domain)
	end
	out[#out + 1] = row(state, table.concat(meta, ' · '))
	if args.connection then
		local name = CONNECTIONS[args.connection] and Util.msg('proposal_connection_' .. args.connection, lang) or plain(args.connection)
		out[#out + 1] = '<div class="sv-proposal-row">' .. withPercent(name, args.connection_p) .. '</div>'
	end
	if args.field then
		out[#out + 1] = row(Util.msg('proposal_field', lang),
			withPercent(Util.escape(nodeName(categories.fields, args.field, lang) or args.field), args.field_p))
	end
	themes(args, categories, lang, out)
	if args.outline then
		local items = {}
		local rest = args.outline .. ' ; '
		while rest ~= '' do
			local at = rest:find(' ; ', 1, true)
			local section = mw.text.trim(rest:sub(1, at - 1))
			rest = rest:sub(at + 3)
			if section ~= '' then
				items[#items + 1] = '<li>' .. plain(section) .. '</li>'
			end
		end
		if #items > 0 then
			out[#out + 1] = '<div class="sv-proposal-row"><span class="sv-proposal-label">' .. Util.msg('proposal_outline', lang)
				.. '</span><ul>' .. table.concat(items) .. '</ul></div>'
		end
	end
	if args.sources then
		local items = {}
		for url in args.sources:gmatch('%S+') do
			items[#items + 1] = '<li>' .. externalLink(url, plain(url)) .. '</li>'
		end
		out[#out + 1] = '<div class="sv-proposal-row"><span class="sv-proposal-label">' .. Util.msg('proposal_sources', lang)
			.. '</span><ul>' .. table.concat(items) .. '</ul></div>'
	end
	if args.first_wiki then
		out[#out + 1] = row(Util.msg('proposal_first_wiki', lang), plain(args.first_wiki))
	end
	if status == 'declined' and args.reason then
		out[#out + 1] = row(Util.msg('proposal_reason', lang), plain(args.reason))
	elseif status == 'created' and args.article and firstWiki then
		out[#out + 1] = '<div class="sv-proposal-row">' .. externalLink('https://' .. firstWiki .. FARM_HOST
			.. mw.uri.encode(args.article, 'WIKI'), plain(args.article)) .. '</div>'
	end
	out[#out + 1] = '</div>'
	if status == 'open' then
		out[#out + 1] = frame:extensionTag('vote', '')
	end
	-- Proposals live on the English wiki, and the bot ranks them by the English status word.
	out[#out + 1] = '[[Category:' .. Util.msg('proposal_category', 'en', status) .. ']]'
	return table.concat(out, '\n')
end

return p
