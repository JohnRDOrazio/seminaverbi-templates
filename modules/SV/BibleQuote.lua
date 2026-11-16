-- {{SV Bible quote}}: markup filled in by MediaWiki:BibleGet.js (classes and data-* attributes
-- must stay compatible with it). Default version and supported versions come from Module:SV/Data.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local SPINNER = '<div class="lds-spinner">' .. string.rep('<div></div>', 12) .. '</div>'

local function isSupported(bible, version)
	for _, v in ipairs(bible.versions) do
		if v == version then
			return true
		end
	end
	return false
end

local function isInline(value)
	return not (value == '0' or value == 'false' or value == 'no')
end

function p._render(args, lang)
	local bible = mw.loadData('Module:SV/Data').bible
	if not args.ref then
		return '<strong class="error">' .. Util.escape(Util.msg('bible_missing_ref', lang)) .. '</strong>'
	end
	local ref = Util.escape(args.ref)
	local version = args.version or bible.defaults[lang] or bible.defaults.en
	local inline = isInline(args.inline)
	local attrs = ' data-ref="' .. ref .. '" data-version="' .. Util.escape(version)
		.. '" data-inline="' .. (inline and 'true' or 'false') .. '"'

	if not isSupported(bible, version) then
		local reason = Util.escape(Util.msg('bible_unsupported', lang, version))
		return '<span class="bibleQuoteRefBroken"' .. attrs .. ' title="' .. reason .. '">' .. ref
			.. '</span><sup class="bibleQuoteRefBrokenReason" title="' .. reason .. '">[!]</sup>'
	end

	local out
	if inline then
		out = '<span class="bibleQuoteRef"' .. attrs .. '>' .. ref .. '</span>'
	else
		out = '<div class="bibleQuoteRef"' .. attrs .. '>' .. SPINNER .. '</div>'
	end
	local versionLang = bible.version_lang[version]
	if versionLang and versionLang ~= lang then
		local name = Util.msg('langname_' .. versionLang, lang)
		out = out .. ' <small class="sv-bible-note">(' .. Util.escape(Util.msg('bible_note', lang, name)) .. ')</small>'
	end
	return out
end

function p.main(frame)
	return p._render(Args.get(frame), Util.lang())
end

return p
