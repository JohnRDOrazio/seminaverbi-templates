-- A quotation that is not from the Bible (Bible passages use SV Bible quote): the text, who said it
-- and where, and optionally the original-language text shown above a translation.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local RTL = { ar = true, he = true, fa = true, ur = true, yi = true, syc = true }

-- A language code safe to put in an attribute (e.g. it, grc, zh-hant), or nil.
local function langCode(code)
	if code and (code:match('^%a%a%a?$') or code:match('^%a%a%a?%-%a+$')) then
		return code:lower()
	end
	return nil
end

function p.quote(frame)
	local args = Args.get(frame)
	if not args.text then
		return '<strong class="error">' .. Util.msg('quote_missing_text', Util.lang()) .. '</strong>'
	end
	local out = { '<blockquote class="sv-quote">' }
	if args.original then
		local code = langCode(args.original_lang)
		local attrs = code and (' lang="' .. code .. '" dir="' .. (RTL[code:match('^%a+')] and 'rtl' or 'ltr') .. '"') or ''
		out[#out + 1] = '<div class="sv-quote-original"' .. attrs .. '>' .. args.original .. '</div>'
	end
	out[#out + 1] = '<div class="sv-quote-text">' .. args.text .. '</div>'
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
