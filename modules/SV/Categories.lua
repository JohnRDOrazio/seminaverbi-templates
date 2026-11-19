-- The main page's category navigation, drawn from the shared category tree (config/categories.json):
-- one card per top-level field of the arts and sciences, then the Gospel themes as chips grouped
-- under their own heading. Each category shows how many articles it holds: leaf categories are
-- counted (mw.site.stats.pagesInCategory, an expensive call, so only leaves, about 170 of them) and
-- a branch shows the sum of its leaves, which is exact because every article has one field.
local Util = require('Module:SV/Util')

local p = {}

local function name(names, lang)
	return names[lang] or names.en
end

local function link(names, lang)
	local n = name(names, lang)
	return '[[:Category:' .. n .. '|' .. n .. ']]'
end

local function count(n)
	if n > 0 then
		return ' <span class="sv-cattree-count">' .. n .. '</span>'
	end
	return ''
end

-- The number of articles in a node: a leaf's own, a branch's the sum of its children's.
local function total(node, lang, counts)
	local n
	if node.children and node.children[1] then
		n = 0
		for _, child in ipairs(node.children) do
			n = n + total(child, lang, counts)
		end
	else
		-- past $wgExpensiveParserFunctionLimit the call raises an error: show no count rather than no tree
		local ok, pages = pcall(mw.site.stats.pagesInCategory, name(node.names, lang), 'pages')
		n = ok and pages or 0
	end
	counts[node.key] = n
	return n
end

local function leaf(node, lang, counts)
	if counts[node.key] > 0 then
		return link(node.names, lang) .. count(counts[node.key])
	end
	return '<span class="sv-cattree-empty">' .. link(node.names, lang) .. '</span>'
end

-- Below a card, a branch is a collapsed block titled by its link; a leaf is a list item.
local function render(node, lang, counts, out)
	if node.children and node.children[1] then
		out[#out + 1] = '<div class="sv-cattree-node mw-collapsible mw-collapsed">\n<div class="sv-cattree-title">'
			.. link(node.names, lang) .. count(counts[node.key]) .. '</div>\n<div class="mw-collapsible-content">'
		for _, child in ipairs(node.children) do
			render(child, lang, counts, out)
		end
		out[#out + 1] = '</div>\n</div>'
	else
		out[#out + 1] = '* ' .. leaf(node, lang, counts)
	end
end

local function card(field, lang, counts, out)
	out[#out + 1] = '<div class="sv-cattree-card">\n<div class="sv-cattree-card-title">'
		.. link(field.names, lang) .. count(counts[field.key]) .. '</div>\n<div class="sv-cattree-card-body">'
	for _, child in ipairs(field.children or {}) do
		render(child, lang, counts, out)
	end
	out[#out + 1] = '</div>\n</div>'
end

local function group(node, lang, counts, out)
	local chips = {}
	for _, theme in ipairs(node.children or {}) do
		chips[#chips + 1] = '<span class="sv-cattree-chip">' .. link(theme.names, lang) .. count(counts[theme.key]) .. '</span>'
	end
	out[#out + 1] = '<div class="sv-cattree-group">\n<div class="sv-cattree-group-title">' .. link(node.names, lang)
		.. '</div>\n<div class="sv-cattree-chips">' .. table.concat(chips, ' ') .. '</div>\n</div>'
end

function p.tree(frame)
	local lang = Util.lang()
	local data = mw.loadData('Module:SV/Data').categories
	local counts = {}
	for _, field in ipairs(data.fields) do
		total(field, lang, counts)
	end
	for _, g in ipairs(data.themes) do
		for _, theme in ipairs(g.children or {}) do
			total(theme, lang, counts)
		end
	end
	local out = {
		frame:extensionTag('templatestyles', '', { src = 'SV category tree/styles.css' }),
		'<div class="sv-cattree">',
		'<div class="sv-cattree-fields">',
	}
	for _, field in ipairs(data.fields) do
		card(field, lang, counts, out)
	end
	out[#out + 1] = '</div>'
	local themesRoot = name(data.themes_root.names, lang)
	out[#out + 1] = '<div class="sv-cattree-themes-heading">[[:Category:' .. themesRoot .. '|'
		.. Util.msg('cattree_themes_heading', lang) .. ']]</div>'
	out[#out + 1] = '<div class="sv-cattree-themes">'
	for _, g in ipairs(data.themes) do
		group(g, lang, counts, out)
	end
	out[#out + 1] = '</div>\n</div>'
	return table.concat(out, '\n')
end

return p
