-- A quotation that is not from the Bible (Bible passages use SV Bible quote): the text, who said it
-- and where, and optionally the original-language text shown above a translation.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local RTL = { ar = true, he = true, fa = true, ur = true, yi = true, syc = true }

-- A BCP 47-shaped language tag safe to put in an attribute (it, grc, zh-Hant, he-Hebr-IL, es-419), or nil.
local function langCode(code)
	if not code or not code:match('^%a%a%a?') then
		return nil
	end
	local rest = code:gsub('^%a%a%a?', '', 1)
	while rest ~= '' do
		local subtag = rest:match('^%-(%w%w?%w?%w?%w?%w?%w?%w?)')
		if not subtag then
			return nil
		end
		rest = rest:sub(#subtag + 2)
	end
	return code:lower()
end

-- lang/dir attributes for a language tag, '' when there is none or it is malformed.
local function langAttrs(code)
	return code and (' lang="' .. code .. '" dir="' .. (RTL[code:match('^%a+')] and 'rtl' or 'ltr') .. '"') or ''
end

-- The name of a language in the wiki's language, shown in small text below its version of the quote
-- (MediaWiki's own localized names, so every wiki gets them in its language); nothing when unknown.
local function langLabel(code, wikiLang)
	local name = code and mw.language.fetchLanguageName(code, wikiLang) or ''
	if name == '' then
		return nil
	end
	return '<div class="sv-quote-lang">' .. Util.escape(name) .. '</div>'
end

function p.quote(frame)
	local args = Args.get(frame)
	local wikiLang = Util.lang()
	if not args.text then
		return '<strong class="error">' .. Util.msg('quote_missing_text', wikiLang) .. '</strong>'
	end
	local out = {
		-- A tag returned as a string is shown as text; it has to go through extensionTag.
		frame:extensionTag('templatestyles', '', { src = 'SV quote/styles.css' }),
		'<blockquote class="sv-quote">',
	}
	-- with an original, each version is labelled with its language
	local textCode = langCode(args.text_lang)
	if args.original then
		local code = langCode(args.original_lang)
		out[#out + 1] = '<div class="sv-quote-original"' .. langAttrs(code) .. '>' .. args.original .. '</div>'
		out[#out + 1] = langLabel(code, wikiLang)  -- nil (no label) appends nothing
	end
	out[#out + 1] = '<div class="sv-quote-text"' .. langAttrs(textCode) .. '>' .. args.text .. '</div>'
	if args.original then
		out[#out + 1] = langLabel(textCode or wikiLang, wikiLang)
	end
	local credit = {}
	if args.author then credit[#credit + 1] = args.author end
	if args.source then credit[#credit + 1] = args.source end
	if credit[1] then
		out[#out + 1] = '<div class="sv-quote-source">— ' .. table.concat(credit, ', ') .. '</div>'
	end
	out[#out + 1] = '</blockquote>'
	return table.concat(out, '\n')
end

return p
