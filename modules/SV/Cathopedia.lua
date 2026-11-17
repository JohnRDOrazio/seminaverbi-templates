-- A link to Cathopedia (Italian; https://it.cathopedia.org) through the shared "cathopedia:" interwiki
-- prefix. Outside the Italian wiki it tells the reader the page is in Italian. Every use is tracked
-- in a category, so Cathopedia's place across the wikis can be listed.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

function p.link(frame)
	local args = Args.get(frame)
	local lang = Util.lang()
	local title = args[1]
	if not title then
		return '<strong class="error">' .. Util.msg('cathopedia_missing_title', lang) .. '</strong>'
	end
	local text = (args[2] or title):gsub('%]', '&#93;')
	local out = '[[cathopedia:' .. title .. '|' .. text .. ']]'
	if lang ~= 'it' then
		out = out .. ' ' .. Util.msg('cathopedia_note', lang)
	end
	return out .. '[[Category:' .. Util.msg('cathopedia_category', lang) .. ']]'
end

return p
