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

-- True when the item has a (non-deprecated) statement for propId with a known or unknown value;
-- "no value" does not count.
local function hasStatement(entity, propId)
	for _, statement in ipairs(entity and entity.claims and entity.claims[propId] or {}) do
		local snaktype = statement.mainsnak and statement.mainsnak.snaktype
		if statement.rank ~= 'deprecated' and (snaktype == 'value' or snaktype == 'somevalue') then
			return true
		end
	end
	return false
end

-- "Place, date" (or the language's order) for one end of a life; nil when both are unknown. The
-- keys name the person infobox rows (config/infobox.json): the place climbs as that row's does.
local function lifeEnd(entity, wb, placeKey, dateKey, lang)
	local place = firstValue(entity, wb[placeKey])
	local date = firstValue(entity, wb[dateKey])
	place = place and place.id and Util.place(place.id, (Util.layoutRow('person', placeKey) or {}).climb, lang)
	-- a place with no label to show (in lang or English) is left out rather than shown as its ID
	place = place and Util.label(place, lang) and Util.itemLink(place, lang)
	date = date and date.time and Util.formatTime(date)
	if place and date then
		return Util.msg('bio_place_date', lang, place, date)
	end
	if place then
		return Util.msg('bio_place_only', lang, place)
	end
	return date
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

-- Accented vowels to plain ones, for plurals whose stress mark goes (es inglés → ingleses).
local UNACCENT = { ['á'] = 'a', ['é'] = 'e', ['í'] = 'i', ['ó'] = 'o', ['ú'] = 'u', ['ê'] = 'e', ['ô'] = 'o' }

local function unaccent(w)
	for accented, plain in pairs(UNACCENT) do
		w = w:gsub(accented, plain)
	end
	return w
end

-- The regular plurals of a singular demonym form (masculine or feminine), by language.
local function regularPlurals(lang, w)
	local out = {}
	local function add(ending, replacement)
		if endsWith(w, ending) then
			out[#out + 1] = w:sub(1, #w - #ending) .. replacement
		end
	end
	if lang == 'it' then
		-- italiano → italiani, italiana → italiane, statunitense → statunitensi, tedesco → tedeschi
		add('o', 'i')
		add('a', 'e')
		add('a', 'i')
		add('e', 'i')
		add('co', 'chi')
		add('go', 'ghi')
		add('ca', 'che')
		add('ga', 'ghe')
	elseif lang == 'es' or lang == 'pt' or lang == 'fr' or lang == 'en' then
		out[#out + 1] = w .. 's'
		if lang == 'es' then
			-- español → españoles, inglés → ingleses
			out[#out + 1] = w .. 'es'
			out[#out + 1] = unaccent(w) .. 'es'
		elseif lang == 'pt' then
			add('ês', 'eses')
			add('ão', 'ães')
			add('ão', 'ões')
			add('ol', 'óis')
			add('al', 'ais')
			add('el', 'éis')
			add('r', 'res')
			add('z', 'zes')
		elseif lang == 'fr' then
			add('al', 'aux')
		end
	end
	return out
end

-- A German demonym usable as an adjective: lowercase (after any hyphen) and ending in -sch.
local function germanAdjective(w)
	local last = w:match('([^-]*)$')
	local first = mw.ustring.sub(last, 1, 1)
	return endsWith(w, 'sch') and first ~= '' and mw.ustring.lower(first) == first and mw.ustring.upper(first) ~= first
end

-- The nationality adjective agreeing with the person, from the country's demonym values in lang,
-- or nil when the masculine form or the agreement cannot be decided (never guessed).
function p._demonym(lang, forms, female)
	-- Wikidata writes fr (and some es/it/pt) demonyms as capitalised nouns; the adjective is lower case
	local lower = lang == 'fr' or lang == 'es' or lang == 'it' or lang == 'pt'
	local unique, seen = {}, {}
	for _, f in ipairs(forms or {}) do
		if type(f) == 'string' and f ~= '' then
			f = lower and mw.ustring.lower(f) or f
			if not seen[f] then
				seen[f] = true
				unique[#unique + 1] = f
			end
		end
	end
	-- the values carry no number qualifiers: drop each value that is the regular plural of another
	local plural = {}
	for _, f in ipairs(unique) do
		for _, pl in ipairs(regularPlurals(lang, f)) do
			if pl ~= f and seen[pl] then
				plural[pl] = true
			end
		end
	end
	local singular = {}
	for _, f in ipairs(unique) do
		if not plural[f] then
			singular[#singular + 1] = f
		end
	end
	unique = singular
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
	-- a form that may itself be the feminine: it/es/pt italiana, pt alemã, fr française (or belge,
	-- which cannot be told apart from it). endsWith compares bytes, so the UTF-8 'ã' is listed apart.
	if ((lang == 'it' or lang == 'es' or lang == 'pt') and (endsWith(masc, 'a') or endsWith(masc, 'ã')))
		or (lang == 'fr' and endsWith(masc, 'e')) then
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
		-- by sound: a /juː/ or /w/ start takes "a", a silent h takes "an". Prefixes are as long as the
		-- sound needs: "uganda" (not "ugly"), "unanim" (unanimous, but not unable or unaware).
		for _, start in ipairs({ 'eu', 'uni', 'unanim', 'uk', 'uganda', 'uru', 'usa', 'use', 'usu', 'uti', 'ura',
			'uro', 'ute', 'once' }) do
			if lower:sub(1, #start) == start then
				return 'a ' .. phrase
			end
		end
		-- "one" as a word ("one", "one-man"), not "onerous"
		if lower:match('^one$') or lower:match('^one[%s%-]') then
			return 'a ' .. phrase
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

-- Endings of one-word occupation nouns that are the same for women and men (la cantante, la
-- giornalista, la journaliste). A phrase may hold a masculine adjective (cantante lirico), so only a
-- single word counts; German has no such ending.
local EPICENE = { it = { 'ista', 'ante', 'ente', 'iatra' }, es = { 'ista', 'ante', 'ente' },
	pt = { 'ista', 'ante', 'ente' }, fr = { 'iste', 'logue' } }

local function epicene(lang, w)
	if w:find('[%s%-]') then
		return false
	end
	for _, ending in ipairs(EPICENE[lang] or {}) do
		if endsWith(w, ending) then
			return true
		end
	end
	return false
end

-- An occupation's word for the person, or nil when the right form cannot be known. gender is
-- 'male', 'female', 'other' (a stated gender that is neither) or nil (none stated). The label is the
-- masculine: it serves a man, and anyone with no stated gender as the language's default; a woman
-- gets the female form, a stated other gender only a form shared by both. English does not inflect.
function p._occupationWord(id, label, lang, wb, gender)
	if gender == 'male' or gender == nil then
		return label
	end
	local form = monolingual(mw.wikibase.getEntity(id), wb.female_form, lang)[1]
	if lang == 'en' then
		return gender == 'female' and form or label
	end
	if gender == 'female' and form then
		return form
	end
	if form == label or epicene(lang, label) then
		return label
	end
	return nil
end

-- "un cantautore italiano" / "an Italian singer-songwriter", or nil when no occupation can be named.
-- At most as many occupations as the person infobox's occupation row shows.
local function predicate(entity, lang, wb, gender)
	local female = gender == 'female'
	local words = {}
	local max = (Util.layoutRow('person', 'occupation') or {}).max
	for _, v in ipairs(Util.best(entity, wb.occupation)) do
		local label = v.id and mw.wikibase.getLabelByLang(v.id, lang)
		local word = label and p._occupationWord(v.id, label, lang, wb, gender)
		if word and not (max and #words >= max) then
			words[#words + 1] = Util.escape(word)
		end
	end
	local occupation = p._list(lang, words)
	if not occupation then
		return nil
	end
	local country = firstValue(entity, wb.country_of_citizenship)
	local forms = country and country.id
		and monolingual(mw.wikibase.getEntity(country.id), wb.demonym, lang) or {}
	local nationality = p._demonym(lang, forms, female)
	if gender == 'other' and nationality ~= p._demonym(lang, forms, true) then
		-- the adjective agrees with the gender: only an invariant one (statunitense) is safe
		nationality = nil
	end
	nationality = nationality and Util.escape(nationality)
	local phrase = occupation
	if nationality then
		if lang == 'en' or lang == 'de' then
			phrase = nationality .. ' ' .. occupation
		else
			phrase = occupation .. ' ' .. nationality
		end
	end
	-- the article agrees with a known gender; English has no agreement to get wrong
	if gender == 'male' or female or lang == 'en' then
		phrase = p._article(lang, phrase, female)
	end
	return phrase
end

-- 'male', 'female', 'other' (a stated gender that is neither) or nil (none stated).
function p._gender(entity, wb)
	local v = entity and firstValue(entity, wb.sex_or_gender)
	if not (v and v.id) then
		return nil
	end
	return v.id == wb.male and 'male' or v.id == wb.female and 'female' or 'other'
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
	local born = lifeEnd(entity, wb, 'birth_place', 'birth_date', lang)
	local died = dead and lifeEnd(entity, wb, 'death_place', 'death_date', lang)
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

	local gender = p._gender(entity, wb)
	local phrase = entity and predicate(entity, lang, wb, gender)
	if gender == 'other' and dead and Util.msg('bio_is_dead', lang) ~= Util.msg('bio_is_dead_f', lang) then
		-- the past copula agrees with the gender (it: è stato / è stata): no form is safe
		phrase = nil
	end
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

-- The sentence on the page ends as it.wiki's Bio ends it: a full stop (unless the free text already
-- ends the sentence), then the references.
function p.main(frame)
	local args = Args.get(frame)
	local id = args.item or mw.wikibase.getEntityIdForCurrentPage()
	local entity = id and mw.wikibase.getEntity(id)
	local text = {}
	for k, v in pairs(args) do
		if k ~= 'ref' then
			text[k] = v
		end
	end
	local out = p._sentence(entity, Util.lang(), text)
	-- From the end of the sentence, peel off citations (a <ref>, a self-closing <ref/>, or the strip marker a
	-- parsed <ref> becomes) and closing markup (runs of apostrophes, </tag>), in whatever order they come. The
	-- full stop goes after the prose and its formatting and before the citations, as Bio places it;
	-- punctuation already ending the prose (inside its formatting) ends the sentence. Citations followed by
	-- more prose stay where they are.
	local closers, cites = '', ''
	while true do
		local body, piece = out:match('^(.-)(%s*\127[^\127]*\127)$')
		if not body then
			body, piece = out:match('^(.-)(%s*<ref[^>]-/>)$')
		end
		if not body then
			-- (anchored: one scan finds the last <ref, not one scan per position)
			local at = out:match('^.*()<ref[%s>]')
			if at and out:sub(at):match('^<ref[^>]*>.-</ref>%s*$') and not out:sub(at + 4):find('<ref', 1, true) then
				body, piece = out:sub(1, at - 1), out:sub(at)
			end
		end
		if body then
			cites = piece .. cites
		else
			body, piece = out:match("^(.-)(''+)$")
			if not body then
				body, piece = out:match('^(.-)(</%w+>)$')
			end
			if not body then
				break
			end
			closers = piece .. closers
		end
		out = body
	end
	local prose = out:gsub('%s+$', '')
	local stop = (prose:match('[%.!?]$') or endsWith(prose, '…')) and '' or '.'
	return out .. closers .. stop .. cites .. (args.ref or '')
end

return p
