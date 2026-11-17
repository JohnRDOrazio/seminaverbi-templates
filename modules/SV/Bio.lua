-- {{SV bio}}: a person's opening sentence from the page's Wikibase item, in the wiki's language:
-- name (note) (birth place and date – death place and date) is/was an occupation of a nationality.
-- No categories; a missing fact leaves its part out. Grammar words come from i18n (Module:SV/Data),
-- the agreement rules are below.
local Args = require('Module:SV/Args')
local Util = require('Module:SV/Util')

local p = {}

local VOWEL = { a = true, e = true, i = true, o = true, u = true }

local function firstValue(entity, propId)
	return Util.best(entity, propId)[1]
end

-- True when the item has any (non-deprecated) statement for propId, known value or not.
local function hasStatement(entity, propId)
	for _, statement in ipairs(entity and entity.claims and entity.claims[propId] or {}) do
		if statement.rank ~= 'deprecated' then
			return true
		end
	end
	return false
end

-- "Place, date" (or the language's order) for one end of a life; nil when both are unknown.
local function lifeEnd(entity, placeProp, dateProp, lang)
	local place = firstValue(entity, placeProp)
	local date = firstValue(entity, dateProp)
	place = place and place.id and Util.itemLink(place.id, lang)
	date = date and date.time and Util.formatTime(date)
	if place and date then
		return Util.msg('bio_place_date', lang, place, date)
	end
	return place or date
end

local function endsWith(s, suffix)
	return suffix == '' or s:sub(-#suffix) == suffix
end

-- Ending → feminine ending (nil: the ending is invariant), checked in order before -o → -a and -e.
local ES_FEMININE = { { 'és', 'esa' }, { 'án', 'ana' }, { 'ón', 'ona' }, { 'ín', 'ina' },
	{ 'a' }, { 'í' }, { 'ú' } }
local PT_FEMININE = { { 'ão', 'ã' }, { 'ês', 'esa' }, { 'ol', 'ola' }, { 'or', 'ora' }, { 'a' }, { 'l' } }

-- The regular feminine of a masculine adjective, or nil when the language has no safe rule for it.
local function regularFeminine(lang, w)
	if lang == 'en' then
		return w
	elseif lang == 'it' or lang == 'es' or lang == 'pt' then
		for _, rule in ipairs(lang == 'es' and ES_FEMININE or lang == 'pt' and PT_FEMININE or {}) do
			if endsWith(w, rule[1]) then
				return rule[2] and (w:sub(1, #w - #rule[1]) .. rule[2]) or w
			end
		end
		if endsWith(w, 'o') then
			return w:sub(1, -2) .. 'a'
		elseif endsWith(w, 'e') then
			return w
		end
		-- es: any other final consonant takes -a (español → española)
		if lang == 'es' and w:sub(-1):match('[b-df-hj-np-tv-z]') then
			return w .. 'a'
		end
	elseif lang == 'fr' then
		if endsWith(w, 'e') then
			return w
		elseif endsWith(w, 'ien') or endsWith(w, 'éen') then
			return w .. 'ne'
		end
		-- endings that double the consonant or change (breton, cruel, muet, grec, vif, roux, étranger)
		for _, ending in ipairs({ 'c', 'on', 'el', 'et', 'f', 'x', 'er' }) do
			if endsWith(w, ending) then
				return nil
			end
		end
		return w .. 'e'
	end
	return nil
end

-- A German demonym usable as an adjective: lowercase (after any hyphen) and ending in -sch.
local function germanAdjective(w)
	local last = w:match('([^-]*)$')
	return endsWith(w, 'sch') and last:sub(1, 1):match('%l') ~= nil
end

-- The nationality adjective agreeing with the person, from the country's demonym values in lang,
-- or nil when the masculine form or the agreement cannot be decided (never guessed).
function p._demonym(lang, forms, female)
	local unique, seen = {}, {}
	for _, f in ipairs(forms or {}) do
		if type(f) == 'string' and f ~= '' and not seen[f] then
			seen[f] = true
			unique[#unique + 1] = f
		end
	end
	if lang == 'de' then
		local adjectives = {}
		for _, f in ipairs(unique) do
			if germanAdjective(f) then
				adjectives[#adjectives + 1] = f
			end
		end
		if #adjectives ~= 1 then
			return nil
		end
		-- after ein / eine (mixed declension), or with no article (strong, masculine noun)
		return adjectives[1] .. (female and 'e' or 'er')
	end
	-- the masculine form: the one value that is not the regular feminine of another value
	local candidates = {}
	for _, f in ipairs(unique) do
		local isFeminine = false
		for _, g in ipairs(unique) do
			if g ~= f and regularFeminine(lang, g) == f then
				isFeminine = true
			end
		end
		if not isFeminine then
			candidates[#candidates + 1] = f
		end
	end
	if #candidates ~= 1 then
		return nil
	end
	local masc = candidates[1]
	-- a lone form that may itself be the feminine (it/es/pt italiana, fr italienne)
	if ((lang == 'it' or lang == 'es' or lang == 'pt') and endsWith(masc, 'a'))
		or (lang == 'fr' and endsWith(masc, 'enne')) then
		return nil
	end
	if female then
		return regularFeminine(lang, masc)
	end
	return masc
end

local function startsWithVowel(w)
	return VOWEL[w:sub(1, 1):lower()] == true
end

-- The indefinite article before a phrase (the phrase starts with its first word).
function p._article(lang, phrase, female)
	local lower = phrase:lower()
	if lang == 'en' then
		-- by sound: a /juː/ or /w/ start takes "a", a silent h takes "an"
		for _, start in ipairs({ 'eu', 'uni', 'use', 'usu', 'uti', 'one', 'once' }) do
			if lower:sub(1, #start) == start then
				return 'a ' .. phrase
			end
		end
		for _, start in ipairs({ 'hour', 'honest', 'honour', 'honor', 'heir' }) do
			if lower:sub(1, #start) == start then
				return 'an ' .. phrase
			end
		end
		return (startsWithVowel(lower) and 'an ' or 'a ') .. phrase
	end
	if lang == 'it' then
		if female then
			if startsWithVowel(lower) then
				return "un'" .. phrase
			end
			return 'una ' .. phrase
		end
		local two = lower:sub(1, 2)
		if (lower:sub(1, 1) == 's' and not VOWEL[lower:sub(2, 2)]) or lower:match('^[zxy]')
			or two == 'gn' or two == 'ps' or two == 'pn' then
			return 'uno ' .. phrase
		end
	end
	return Util.msg(female and 'bio_article_f' or 'bio_article_m', lang) .. ' ' .. phrase
end

-- Words joined as the language joins a list: "a, b and c". Spanish y becomes e before an i sound.
function p._list(lang, words)
	local n = #words
	if n == 0 then
		return nil
	elseif n == 1 then
		return words[1]
	end
	local conj = Util.msg('bio_list_and', lang)
	if lang == 'es' then
		local last = words[n]:lower()
		local rest = last:match('^h?i(.?)') or last:match('^h?í(.?)')
		if rest and not VOWEL[rest] then
			conj = 'e'
		end
	end
	local head = {}
	for i = 1, n - 1 do
		head[i] = words[i]
	end
	return table.concat(head, ', ') .. ' ' .. conj .. ' ' .. words[n]
end

-- The values of a monolingual-text property in lang.
local function monolingual(entity, propId, lang)
	local out = {}
	for _, v in ipairs(Util.best(entity, propId)) do
		if v.language == lang then
			out[#out + 1] = v.text
		end
	end
	return out
end

-- "un cantautore italiano" / "an Italian singer-songwriter", or nil when there is no occupation.
local function predicate(entity, lang, wb, gender)
	local female = gender == 'female'
	local agree = gender ~= nil
	local words = {}
	for _, v in ipairs(Util.best(entity, wb.occupation)) do
		local label = v.id and mw.wikibase.getLabelByLang(v.id, lang)
		if label then
			local word = label
			if female then
				local form = monolingual(mw.wikibase.getEntity(v.id), wb.female_form, lang)[1]
				if form then
					word = form
				elseif lang ~= 'en' then
					-- no female form: the label would not agree with the article or the adjective
					agree = false
				end
			end
			words[#words + 1] = Util.escape(word)
		end
	end
	local occupation = p._list(lang, words)
	if not occupation then
		return nil
	end
	local nationality
	if agree or not female then
		local country = firstValue(entity, wb.country_of_citizenship)
		local forms = country and country.id
			and monolingual(mw.wikibase.getEntity(country.id), wb.demonym, lang) or {}
		nationality = p._demonym(lang, forms, female and agree)
		nationality = nationality and Util.escape(nationality)
	end
	local phrase = occupation
	if nationality then
		if lang == 'en' or lang == 'de' then
			phrase = nationality .. ' ' .. occupation
		else
			phrase = occupation .. ' ' .. nationality
		end
	end
	-- English always takes the article: it has no agreement to get wrong
	if agree or lang == 'en' then
		phrase = p._article(lang, phrase, female)
	end
	return phrase
end

function p._sentence(entity, lang, args)
	local wb = mw.loadData('Module:SV/Data').wikibase
	args = args or {}
	local id = entity and entity.id
	local name = args.name
		or (id and Util.label(id, lang) and Util.escape(Util.label(id, lang)))
		or Util.escape(mw.title.getCurrentTitle().text)

	local head = name
	if args.note then
		head = head .. ', ' .. args.note
	end

	local dead = hasStatement(entity, wb.death_date) or hasStatement(entity, wb.death_place)
	local born = lifeEnd(entity, wb.birth_place, wb.birth_date, lang)
	local died = dead and lifeEnd(entity, wb.death_place, wb.death_date, lang)
	local paren
	if born and died then
		paren = Util.msg('bio_paren_span', lang, born, died)
	elseif died then
		paren = Util.msg('bio_paren_died', lang, died)
	elseif born and dead then
		paren = Util.msg('bio_paren_span', lang, born, '?')
	elseif born then
		paren = Util.msg('bio_paren_born', lang, born)
	end
	if paren then
		head = head .. ' ' .. paren
	end

	local genderValue = firstValue(entity, wb.sex_or_gender)
	local gender = genderValue and (genderValue.id == wb.male and 'male' or genderValue.id == wb.female and 'female')
		or nil
	local phrase = entity and predicate(entity, lang, wb, gender)
	local out = head
	if phrase then
		local copula = not dead and 'bio_is_living' or gender == 'female' and 'bio_is_dead_f' or 'bio_is_dead'
		out = head .. (args.note and ',' or '') .. ' ' .. Util.msg(copula, lang) .. ' ' .. phrase
	end
	if args.rest then
		out = out .. (args.rest:match('^[,;:.)]') and '' or ' ') .. args.rest
	end
	if args.ref then
		out = out .. args.ref
	end
	return out
end

function p.main(frame)
	local args = Args.get(frame)
	local id = args.item or mw.wikibase.getEntityIdForCurrentPage()
	local entity = id and mw.wikibase.getEntity(id)
	return p._sentence(entity, Util.lang(), args)
end

return p
