-- {{SV cite}}: one citation style for every wiki, with fixed English parameter names.
-- Parameters are wikitext and go into text as they are (MediaWiki's Sanitizer makes text safe);
-- escaping them would break entities, nested templates and strip markers. Only URLs are encoded.
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
	return value
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
		add(args.author .. ' (' .. date(args.date) .. ').')
	elseif args.author then
		add(args.author .. '.')
	elseif args.date then
		add('(' .. date(args.date) .. ').')
	end

	local title = args.title
	if args.url then
		-- a "]" in the link text would close the external link early
		title = '[' .. url(args.url) .. ' ' .. title:gsub('%]', '&#93;') .. ']'
	end
	if kind == 'book' then
		title = "''" .. title .. "''"
	elseif QUOTED[kind] then
		title = '"' .. title .. '"'
	end
	add(title .. '.')

	if args.work and WITH_WORK[kind] then
		add("''" .. args.work .. "''.")
	end
	if args.publisher then
		add(args.publisher .. '.')
	end
	if args.isbn then
		add('ISBN ' .. args.isbn .. '.')
	end
	if args.pages then
		add(Util.msg('cite_pages', lang) .. ' ' .. args.pages .. '.')
	end
	if args.language then
		add('(' .. args.language .. ')')
	end
	if args['archive-url'] then
		local when = args['archive-date'] and (' ' .. date(args['archive-date'])) or ''
		add(Util.msg('cite_archived', lang) .. ' [' .. url(args['archive-url']) .. when .. '].')
	end
	if args['access-date'] then
		add(Util.msg('cite_accessed', lang) .. ' ' .. date(args['access-date']) .. '.')
	end
	if args.quote then
		add('«' .. args.quote .. '»')
	end
	return '<cite class="sv-cite">' .. table.concat(parts, ' ') .. '</cite>'
end

function p.main(frame)
	return p._render(Args.get(frame), Util.lang())
end

return p
