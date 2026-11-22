-- {{SV Bible quote}}: the BibleGet extension's <biblequote> tag (BibleGet 2.0), which fetches the
-- quote at parse time. The tag's ref is the form BibleGet resolves; its body is what readers see.
-- Default version and supported versions come from Module:SV/Data.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local function isSupported(bible, version)
	for _, v in ipairs(bible.versions) do
		if v == version then
			return true
		end
	end
	return false
end

-- The reference as BibleGet should receive it: each ';'-separated part whose book abbreviation the
-- wiki uses but BibleGet does not resolve (bible.query_books[lang]) gets the form BibleGet knows.
-- The longest matching abbreviation wins ("1 Joh" before "Joh"); it must end at a space or a digit.
local function queryRef(ref, map)
	if not map then
		return ref
	end
	local parts = {}
	for part in (ref .. ';'):gmatch('([^;]*);') do
		local lead, rest = part:match('^(%s*)(.*)$')
		local best
		for abbr, book in pairs(map) do
			if rest:sub(1, #abbr) == abbr and (#rest == #abbr or rest:sub(#abbr + 1, #abbr + 1):match('[%s%d]'))
				and (not best or #abbr > #best) then
				best = abbr
			end
		end
		if best then
			local nextChar = rest:sub(#best + 1, #best + 1)
			rest = map[best] .. (nextChar:match('%s') and ' ' or '') .. rest:sub(#best + 1):gsub('^%s+', '')
		end
		parts[#parts + 1] = lead .. rest
	end
	return table.concat(parts, ';')
end

local function isInline(value)
	return not (value == '0' or value == 'false' or value == 'no')
end

function p._render(args, lang, frame)
	local bible = mw.loadData('Module:SV/Data').bible
	if not args.ref then
		return '<strong class="error">' .. Util.escape(Util.msg('bible_missing_ref', lang)) .. '</strong>'
	end
	local version = args.version or bible.defaults[lang] or bible.defaults.en

	if not isSupported(bible, version) then
		-- The extension's fallback markup, without a request to BibleGet.
		local reason = Util.escape(Util.msg('bible_unsupported', lang, version))
		return '<span class="bibleget-failed">' .. Util.escape(args.ref)
			.. '</span><sup class="bibleget-failed-marker" title="' .. reason .. '">[!]</sup>'
	end

	-- A tag returned as a string is shown as text; it has to go through extensionTag, which hands the
	-- values to the extension as they are (the extension escapes them).
	local out = frame:extensionTag('biblequote', args.ref, {
		ref = queryRef(args.ref, bible.query_books and bible.query_books[lang]),
		version = version,
		inline = isInline(args.inline) and 'true' or 'false',
	})
	local versionLang = bible.version_lang[version]
	if versionLang and versionLang ~= lang then
		local name = Util.msg('langname_' .. versionLang, lang)
		out = out .. ' <small class="sv-bible-note">(' .. Util.escape(Util.msg('bible_note', lang, name)) .. ')</small>'
	end
	return out
end

function p.main(frame)
	return p._render(Args.get(frame), Util.lang(), frame)
end

return p
