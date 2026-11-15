-- Section helpers shared by every article: the discernment heading, the work-in-progress notice,
-- the references list, and the "managed in git" notice on SV template pages.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local REPO = 'https://github.com/JohnRDOrazio/seminaverbi-templates'

function p.discernment(frame)
	return '== ' .. Util.msg('discernment_heading', Util.lang()) .. ' ==\n'
end

function p.wip(frame)
	local args = Args.get(frame)
	local lang = Util.lang()
	local text = Util.msg('wip_notice', lang, Util.escape(args.user or '?'), Util.escape(args.date or '?'))
	return '<div class="sv-wip">' .. text .. '</div>[[Category:' .. Util.msg('wip_category', lang) .. ']]'
end

function p.references(frame)
	local args = Args.get(frame)
	local group = args.group and (' group="' .. Util.escape(args.group) .. '"') or ''
	return '== ' .. Util.msg('references_heading', Util.lang()) .. ' ==\n<references' .. group .. ' />'
end

function p.managed(frame)
	return '<div class="sv-managed">' .. Util.msg('managed_notice', Util.lang(), REPO) .. '</div>'
end

return p
