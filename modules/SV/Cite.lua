-- {{SV cite}}: one citation style for every wiki, with fixed English parameter names.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local QUOTED = { web = true, journal = true, interview = true }
local WITH_WORK = { web = true, journal = true }

local function url(u)
	return (u:gsub('[%]%[ ]', function(c)
		return string.format('%%%02X', c:byte())
	end))
end

local function date(value)
	if value:match('^%d%d%d%d%-%d%d%-%d%d$') then
		return mw.language.getContentLanguage():formatDate('j F Y', value)
	end
	return Util.escape(value)
end

function p._render(args, lang)
	if not args.title then
		return '<strong class="error">' .. Util.escape(Util.msg('cite_missing_title', lang)) .. '</strong>'
	end
	local kind = args.type or 'web'
	local parts = {}
	local function add(s)
		parts[#parts + 1] = s
	end

	if args.author and args.date then
		add(Util.escape(args.author) .. ' (' .. date(args.date) .. ').')
	elseif args.author then
		add(Util.escape(args.author) .. '.')
	elseif args.date then
		add('(' .. date(args.date) .. ').')
	end

	local title = Util.escape(args.title)
	if args.url then
		title = '[' .. url(args.url) .. ' ' .. title .. ']'
	end
	if kind == 'book' then
		title = "''" .. title .. "''"
	elseif QUOTED[kind] then
		title = '"' .. title .. '"'
	end
	add(title .. '.')

	if args.work and WITH_WORK[kind] then
		add("''" .. Util.escape(args.work) .. "''.")
	end
	if args.publisher then
		add(Util.escape(args.publisher) .. '.')
	end
	if args.isbn then
		add('ISBN ' .. Util.escape(args.isbn) .. '.')
	end
	if args.pages then
		add(Util.msg('cite_pages', lang) .. ' ' .. Util.escape(args.pages) .. '.')
	end
	if args.language then
		add('(' .. Util.escape(args.language) .. ')')
	end
	if args['archive-url'] then
		local when = args['archive-date'] and (' ' .. date(args['archive-date'])) or ''
		add(Util.msg('cite_archived', lang) .. ' [' .. url(args['archive-url']) .. when .. '].')
	end
	if args['access-date'] then
		add(Util.msg('cite_accessed', lang) .. ' ' .. date(args['access-date']) .. '.')
	end
	if args.quote then
		add('«' .. Util.escape(args.quote) .. '»')
	end
	return '<cite class="sv-cite">' .. table.concat(parts, ' ') .. '</cite>'
end

function p.main(frame)
	return p._render(Args.get(frame), Util.lang())
end

return p
