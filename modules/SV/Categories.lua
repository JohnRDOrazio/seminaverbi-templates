-- The main page's category navigation, drawn from the shared category tree (config/categories.json):
-- the fields of the arts and sciences, then the Gospel themes under their own heading.
local Util = require('Module:SV/Util')

local p = {}

local function link(names, lang)
	local name = names[lang] or names.en
	return '[[:Category:' .. name .. '|' .. name .. ']]'
end

-- A node with children is a collapsible block titled by its link; a leaf is a list item.
local function render(node, lang, out)
	if node.children and node.children[1] then
		out[#out + 1] = '<div class="sv-cattree-node mw-collapsible">\n<div class="sv-cattree-title">'
			.. link(node.names, lang) .. '</div>\n<div class="mw-collapsible-content">'
		for _, child in ipairs(node.children) do
			render(child, lang, out)
		end
		out[#out + 1] = '</div>\n</div>'
	else
		out[#out + 1] = '* ' .. link(node.names, lang)
	end
end

function p.tree(frame)
	local lang = Util.lang()
	local data = mw.loadData('Module:SV/Data').categories
	local out = {
		frame:extensionTag('templatestyles', '', { src = 'SV category tree/styles.css' }),
		'<div class="sv-cattree">',
	}
	for _, field in ipairs(data.fields) do
		render(field, lang, out)
	end
	local themesRoot = data.themes_root.names[lang] or data.themes_root.names.en
	out[#out + 1] = '<div class="sv-cattree-themes-heading">[[:Category:' .. themesRoot .. '|'
		.. Util.msg('cattree_themes_heading', lang) .. ']]</div>'
	out[#out + 1] = '<div class="sv-cattree-themes">'
	for _, group in ipairs(data.themes) do
		render(group, lang, out)
	end
	out[#out + 1] = '</div>\n</div>'
	return table.concat(out, '\n')
end

return p
