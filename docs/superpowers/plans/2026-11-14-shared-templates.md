# SeminaVerbi Shared Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A family of `SV …` templates and Lua modules, kept in git and pushed by a sync script, that renders identically on all six SeminaVerbi wikis (infobox from Wikibase, citation, Bible quote, section helpers), plus a server-side script that fills the thin Wikibase items the infobox reads.

**Architecture:** Lua modules (`Module:SV/…`) hold all logic; templates are one-line `{{#invoke:}}` wrappers. Project data (property IDs, field lists, Bible versions, UI strings in six languages) lives in JSON in the repo and is compiled by the sync into one Lua data module, `Module:SV/Data`, read with `mw.loadData`. A Python sync script diffs the repo against each wiki through the API and edits only what changed, as a dedicated bot. Lua is unit-tested locally under Lua 5.1 (lupa) with stubs for `mw`, `mw.wikibase` and `mw.language`.

**Tech Stack:** Lua 5.1 (Scribunto), Python 3.12 + `requests` + `pytest` + `lupa` (venv), MediaWiki Action API with BotPasswords, a PHP maintenance script using WikibaseImport services, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-11-14-shared-templates-design.md` (read it first).

## Global Constraints

- Wikis: `en it es fr de pt`; API `https://{lang}.seminaverbi.bibleget.io/w/api.php`. Wikibase repo is `en`; MW 1.44; Scribunto = Lua 5.1 semantics.
- Every page the sync writes has the **same content on all six wikis** (only alias redirects differ per language).
- Canonical titles use English namespaces (`Template:`, `Module:`); the API accepts them on every wiki.
- Names: modules `Module:SV/<Name>`, templates `Template:SV infobox work`, `Template:SV infobox person`, `Template:SV cite`, `Template:SV Bible quote`, `Template:SV discernment`, `Template:SV WIP`, `Template:SV references`. Parameters are English on every wiki.
- Property IDs only in `config/wikibase.json`; never a literal `P…` in Lua.
- Bible defaults: en `NABRE`, it `CEI2008`, es `BLPD`, fr/de/pt `NVBSE`. Supported versions: `NABRE DRB CEI2008 LUZZI BLPD NVBSE VGCL`. Version languages: NABRE/DRB en, CEI2008/LUZZI it, BLPD es, NVBSE/VGCL la.
- The fallback note shows exactly when the rendered version's language ≠ page language (so switching fr/de/pt to a native version later removes it with no other change).
- Bible quote markup must stay compatible with the existing `MediaWiki:BibleGet.js` (classes `bibleQuoteRef` / `bibleQuoteRefBroken`, attributes `data-ref`, `data-version`, `data-inline`).
- Discernment heading: en "Seeds of the Word and points of caution", it "Semi del Verbo e punti di attenzione"; es/fr/de/pt drafts in i18n, flagged for the author's review in the PR.
- Sync edit summary: `sync from seminaverbi-templates@<short sha>`; never overwrite a page whose latest revision is by another user unless `--force <title>`.
- Bot credentials only from environment (`SV_SYNC_USER`, `SV_SYNC_PASSWORD`), never in the repo or printed.
- The bot needs no interface-admin rights: no `MediaWiki:*.js/css` writes; styles go in TemplateStyles pages (`Template:SV infobox/styles.css`).
- Resource guardrails for the enrichment run: record `seminaverbi_en` DB size before/after; abort if it grows by more than 200 MB.
- Existing templates (`Bible quote`, `Citazione bibbia`, `Module:BibleQuote`, all imported Wikipedia templates) are not modified.

## Review Focus

1. A `ref=` or citation value containing `"`, `<` or `&` (e.g. `ref=Jn 3,16"` or a title with quotes) must be escaped in attributes and text, not break the HTML. → tests in Tasks 3 and 5.
2. A page with no connected Wikibase item, or an item missing every field, must still render the infobox (title from parameters) plus the tracking category, never a Lua error. → test in Task 4.
3. A label missing in the page language (common on fr/de/pt) falls back to English, then to the item ID, never to an empty cell. → test in Task 4.
4. A wiki page edited by hand since the last sync must be reported and skipped, not silently overwritten, and the sync must keep going with the other pages and wikis. → test in Task 7.
5. Running the sync twice in a row makes zero edits the second time (no whitespace churn from notices or generated data). → test in Task 7.

---

## File Structure

```
README.md                         how to edit, test, sync; "pages are managed here"
requirements.txt                  requests, pytest, lupa
.github/workflows/ci.yml          pytest on push/PR
config/wikibase.json              semantic property name -> local P id
config/infobox.json               type -> ordered field list
config/bible.json                 versions, version languages, per-wiki defaults
config/aliases.json               lang -> {alias title: canonical title}
i18n/{en,it,es,fr,de,pt}.json     UI strings
modules/SV/Args.lua               argument merge (parent frame + frame), blank -> nil
modules/SV/Util.lua               html escape, i18n lookup with fallback
modules/SV/BibleQuote.lua
modules/SV/Infobox.lua
modules/SV/Cite.lua
modules/SV/Sections.lua           discernment heading, WIP notice, references
templates/*.wikitext              one file per template page; file name = title after "Template:"
templates/SV infobox/styles.css   TemplateStyles
sync/__init__.py, sync/__main__.py
sync/pages.py                     repo -> {title: content} per wiki (incl. generated Module:SV/Data)
sync/wiki.py                      API client
sync/plan.py                      pure diff/guard logic
tests/lua_env.py                  lupa runtime + mw stubs
tests/test_*.py
server/EnrichItems.php            maintenance script (runs on en)
server/properties.txt             Wikidata property IDs to copy
```

---

### Task 1: Repository, CI and Lua test harness

**Files:**
- Create: `README.md`, `requirements.txt`, `.gitignore` (`.venv/`, `__pycache__/`), `.github/workflows/ci.yml`, `tests/lua_env.py`, `tests/test_harness.py`, `modules/SV/Args.lua`, `modules/SV/Util.lua`

**Interfaces:**
- Produces: `LuaEnv(lang="en", entities=None, page_entity=None, sitelinks=None)` in `tests/lua_env.py` with `.require(name) -> lua table` (maps `Module:SV/X` → `modules/SV/X.lua`, `Module:SV/Data` → compiled repo data via `sync.pages.compile_data()`; until Task 2 exists, a stub returning `{}`), `.call(module, fn, *args)`, and `.frame(args: dict, parent_args: dict) -> lua frame`.
  Stubs: `mw.loadData`, `mw.language.getContentLanguage()` (`:getCode()`, `:formatDate(fmt, iso)` → deterministic string `"<fmt>|<iso>"`), `mw.wikibase.getEntityIdForCurrentPage()`, `getEntity(id)` (returns table with `claims` in Wikibase Lua shape), `getLabelByLang(id, lang)`, `getSitelink(id, globalSiteId?)`, `mw.text.trim`, `mw.ustring` = Lua `string` for tests.
- Produces Lua: `require('Module:SV/Args').get(frame) -> table` (parent args overlaid by frame args, trimmed, blank strings dropped); `require('Module:SV/Util').escape(s) -> string` (`& < > "` → entities), `.msg(key, lang) -> string` (i18n lang → en → key).

- [ ] **Step 1:** `git init`, create private GitHub repo `JohnRDOrazio/seminaverbi-templates`, move the existing `docs/` in, first commit of docs only.
- [ ] **Step 2:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`; confirm `python -c "import lupa.lua51"` works (if the wheel lacks `lua51`, use `lupa.luajit21`, also 5.1 semantics, and note it in README).
- [ ] **Step 3: Write failing tests** `tests/test_harness.py`:
  - `test_args_merge`: frame args `{ref=" Jn 3:16 "}`, parent `{ref="x", version=""}` → `{ref="Jn 3:16"}` (frame wins, trimmed, blank dropped).
  - `test_escape`: `escape('a"<b>&')` == `'a&quot;&lt;b&gt;&amp;'`.
  - `test_msg_fallback`: with i18n `{en:{k:"E"}, fr:{}}`, `msg("k","fr")=="E"`, `msg("zz","fr")=="zz"`.
- [ ] **Step 4:** Run `.venv/bin/pytest -q` → FAIL (modules missing).
- [ ] **Step 5:** Implement `tests/lua_env.py`, `Args.lua`, `Util.lua` (Util reads i18n from `mw.loadData('Module:SV/Data').i18n`).
- [ ] **Step 6:** Run `.venv/bin/pytest -q` → PASS. CI workflow runs the same on Python 3.12.
- [ ] **Step 7:** Commit `chore: repo scaffold, Lua test harness, Args and Util`.

### Task 2: Project data and its compiler

**Files:**
- Create: `config/wikibase.json`, `config/infobox.json`, `config/bible.json`, `config/aliases.json`, `i18n/*.json`, `sync/pages.py` (only `compile_data` in this task), `tests/test_data.py`

**Interfaces:**
- Produces: `sync.pages.compile_data(root: Path) -> str` — Lua source `return { wikibase=…, infobox=…, bible=…, i18n={en=…,…} }`, keys sorted, stable output byte-for-byte across runs.
- `config/wikibase.json` (verified local IDs 2026-11-14): `instance_of P2016, title P458, image P725, author P3990, director P4722, screenwriter P4820, cast P552, publication_date P4788, inception P4732, genre P344, performer P683, original_language_work P3001, original_language_film P2549, country_of_origin P3939, composer P7752, lyrics_by P5825, record_label P1561, creator P639, birth_date P4712, death_date P4723, birth_place P818, death_place P915, occupation P63, follows P490, followed_by P501`.
- `config/infobox.json`: `{type: [{key, prop, kind: "item"|"time"|"image"|"monolingual", max?}]}` for `book` (author, original_language_work, publication_date, genre, country_of_origin, follows, followed_by), `film` (director, screenwriter, cast max 6, publication_date, original_language_film, country_of_origin, genre), `tv` (creator, cast max 6, original_language_film, country_of_origin, genre, publication_date), `album` (performer, publication_date, genre, record_label), `song` (performer, composer, lyrics_by, publication_date, genre), `play` (author, original_language_work, publication_date, genre), `artwork` (creator, inception), `person` (birth_date, birth_place, death_date, death_place, occupation). Every type also has `image` first and `title` (monolingual, the original title).
- `config/bible.json`: `versions`, `version_lang`, `defaults` exactly as in Global Constraints.
- `config/aliases.json`: `{lang: {alias: canonical}}`, e.g. it `Template:SV citazione bibbia`→`Template:SV Bible quote`, `Template:SV cita`→`Template:SV cite`, `Template:SV infobox opera`→`Template:SV infobox work`, `Template:SV discernimento`→`Template:SV discernment`; one native alias per template for es/fr/de/pt likewise (builder picks the words); en has none.
- i18n keys (all six files): field labels are optional overrides (`label_<key>`); required keys `infobox_missing_category`, `bible_note` (e.g. fr "texte latin de la Néo-Vulgate ; traduction française pas encore disponible", de "lateinischer Text der Nova Vulgata; deutsche Übersetzung noch nicht verfügbar", pt "texto latino da Nova Vulgata; tradução portuguesa ainda não disponível"), `bible_unsupported`, `discernment_heading`, `references_heading`, `wip_notice`, `managed_notice`, cite labels `cite_accessed`, `cite_archived`, `cite_pages`, `cite_in`.

- [ ] **Step 1: Failing tests** `tests/test_data.py`:
  - `test_compile_is_stable`: two calls return identical strings.
  - `test_every_lang_has_required_keys`: each `i18n/*.json` has every required key above, non-empty.
  - `test_infobox_props_resolve`: every `prop` in `infobox.json` is a key of `wikibase.json`.
  - `test_bible_defaults_cover_all_wikis`: defaults has exactly the six langs and each value is in `versions`.
  - `test_data_loads_in_lua`: `LuaEnv().require('Module:SV/Data').bible.defaults.fr == "NVBSE"`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Write the JSON files and `compile_data` (JSON → Lua literal; strings escaped with `%q`-equivalent; sorted keys). Wire `LuaEnv` to use it.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: project data (properties, field lists, Bible versions, i18n) and compiler`.

### Task 3: Bible quote

**Files:**
- Create: `modules/SV/BibleQuote.lua`, `templates/SV Bible quote.wikitext` (`{{#invoke:SV/BibleQuote|main}}` + `<noinclude>` doc listing `ref`, `version`, `inline`), `tests/test_bible_quote.py`

**Interfaces:**
- Produces: `p.main(frame) -> string`; `p._render(args: {ref, version?, inline?}, lang: string) -> string`.
  Output (valid version, inline default true): `<span class="bibleQuoteRef" data-ref="REF" data-version="V" data-inline="true">REF</span>`; block (`inline=0|false|no`): the existing module's `<div class="bibleQuoteRef" … data-inline="false">` with its 12-div `lds-spinner`. Unsupported version: the existing `bibleQuoteRefBroken` span + `<sup>[!]</sup>`, title text from `msg("bible_unsupported", lang)` with `$1` = version. When `version_lang[V] ~= lang`: append `<small class="sv-bible-note">(` .. msg("bible_note") .. `)</small>`. Missing `ref`: return `<strong class="error">` with msg key `bible_missing_ref` (add to i18n).

- [ ] **Step 1: Failing tests:**
  - `test_default_version_per_wiki`: `_render({ref="Jn 3,16"}, L)` contains `data-version="NABRE"` for en, `CEI2008` it, `BLPD` es, `NVBSE` fr/de/pt.
  - `test_note_only_when_language_differs`: note present for fr/de/pt defaults; absent for en/it/es defaults; present for `version="NVBSE"` on it; absent if fr default were a French version (patch data in the test env).
  - `test_block_markup_matches_bibleget_js`: `inline="0"` → starts with `<div class="bibleQuoteRef"` and has `data-inline="false"`.
  - `test_unsupported_version`: `version="KJV"` → `bibleQuoteRefBroken` and `[!]`.
  - `test_ref_is_escaped` (Review Focus 1): `ref='Jn 3,16"><b>'` → output contains `data-ref="Jn 3,16&quot;&gt;&lt;b&gt;"` and no raw `<b>`.
  - `test_missing_ref_is_error`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: SV Bible quote with per-wiki default version and Latin fallback note`.

### Task 4: Infobox

**Files:**
- Create: `modules/SV/Infobox.lua`, `templates/SV infobox work.wikitext` (`{{#invoke:SV/Infobox|work}}`), `templates/SV infobox person.wikitext` (`{{#invoke:SV/Infobox|person}}`), `templates/SV infobox/styles.css`, `tests/test_infobox.py`

**Interfaces:**
- Produces: `p.work(frame)`, `p.person(frame)`; `p._render(kind: string, args: table, ctx: {lang, entity_id?}) -> string`. `args.type` selects the work type (`book|film|tv|album|song|play|artwork`; default `book`); `args.item` overrides the connected item; `args.name` overrides the caption (default: item label in page lang, else page title via `mw.title.getCurrentTitle().text` — stub it); any field `key` in args overrides the item value (wikitext passed through).
- Output: `<templatestyles src="SV infobox/styles.css"/>` + `<table class="sv-infobox sv-infobox-<type>">`, caption row, image row (`[[File:X|frameless|upright=1.15]]`), then one `<tr><th>LABEL</th><td>VALUE</td></tr>` per non-empty field in config order. Label = `label_<key>` i18n override, else property label in page lang (`getLabelByLang(P, lang)`), else English, else the key.
- Item values: label in page lang → en → ID; link = local sitelink `getSitelink(id)` → `[[Title|label]]`; else `getSitelink(id, lang.."wiki")` → `[[wikipedia:LANG:Title|label]]`; else plain label. Multiple values joined with `, `; `max` truncates. Time values: precision ≥ 11 → `formatDate("j F Y", iso)`, 10 → `"F Y"`, ≤ 9 → year only (negative years as `|year| a.C.`/`BC` not needed — render the number). Monolingual `title`: value whose language ≠ page lang, rendered `''text''`.
- Category: append `[[Category:` .. msg("infobox_missing_category") .. `]]` when there is no item or every Wikibase-backed field is empty.

- [ ] **Step 1: Failing tests** (stub entities modelled on Q4178/Q1185):
  - `test_book_from_item`: item with author → Q-tolkien (label it "J. R. R. Tolkien", sitelink `itwiki` only) on `it` → row label `autore`, value `[[wikipedia:it:J. R. R. Tolkien|J. R. R. Tolkien]]`.
  - `test_local_sitelink_wins`: `followed_by` item with local sitelink `it` → `[[Il Silmarillion|Il Silmarillion]]`.
  - `test_param_overrides_item`: `args.author="[[Tolkien]]"` → that wikitext in the author cell, item value absent.
  - `test_time_precision`: precision 9 `+1954-00-00T00:00:00Z` → `1954`; precision 11 → `j F Y|…`.
  - `test_no_item_still_renders` (Review Focus 2): no connected item, `args.name="X"`, `args.genre="fantasy"` → table with caption X, genre row, and the missing-data category; no error.
  - `test_label_fallback` (Review Focus 3): on `pt`, item label only in en → en label shown; no labels at all → the Q-id.
  - `test_cast_max`: 8 cast values on `film` → 6 shown.
  - `test_person`: birth date and place rows on `person`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (stub `mw.title` in `lua_env.py`). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: SV infobox work/person filled from Wikibase with local-language labels`.

### Task 5: Citation

**Files:**
- Create: `modules/SV/Cite.lua`, `templates/SV cite.wikitext` (`{{#invoke:SV/Cite|main}}`), `tests/test_cite.py`

**Interfaces:**
- Produces: `p.main(frame)`, `p._render(args, lang) -> string`. Params: `type` (`web` default, `book`, `journal`, `video`, `liner`, `interview`), `url title author date publisher work access-date language quote archive-url archive-date isbn pages`.
- Format (fixed, all types; omit absent parts): `AUTHOR (DATE). ` + title (linked `[URL TITLE]` if url, italic for `book`, in quotes `"…"` for `web|journal|interview`) + `. ` + (`journal`/`web`: `''WORK''. `) + `PUBLISHER. ` + `ISBN ISBN. ` + `msg(cite_pages) PAGES. ` + `(LANGUAGE) ` + `msg(cite_archived) [ARCHIVE-URL ARCHIVE-DATE]. ` + `msg(cite_accessed) ACCESS-DATE.` + quote as `«QUOTE»`. Dates in ISO `YYYY-MM-DD` render via `formatDate("j F Y")`; other strings pass through.
- Wrapped in `<cite class="sv-cite">…</cite>`.

- [ ] **Step 1: Failing tests:**
  - `test_web_minimal`: `{url="https://x.org", title="T"}` en → `<cite class="sv-cite">"[https://x.org T]".</cite>`.
  - `test_book_full`: author, date `2002`, title italic, publisher, ISBN, pages → exact expected string (write it from the format above).
  - `test_iso_dates_localized`: `access-date=2026-11-14` → `j F Y|2026-11-14` with `msg(cite_accessed,"it")`.
  - `test_title_escaped` (Review Focus 1): title `A "B" <i>` → `&quot;`/`&lt;` in output; url with `]` is percent-encoded `%5D`.
  - `test_missing_title_is_error`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: SV cite`.

### Task 6: Section helpers

**Files:**
- Create: `modules/SV/Sections.lua`, `templates/SV managed.wikitext`, `templates/SV discernment.wikitext`, `templates/SV WIP.wikitext`, `templates/SV references.wikitext`, `tests/test_sections.py`

**Interfaces:**
- Produces: `p.discernment(frame)` → `== HEADING ==` (from `discernment_heading`) with a newline; `p.wip(frame)` → a notice box `<div class="sv-wip">` with `wip_notice` (`$1` = `args.user`, `$2` = `args.date`) and `[[Category:` .. msg("wip_category") .. `]]` (add key); `p.references(frame)` → `== HEADING ==\n<references />` (optional `group`); `p.managed(frame)` → `<div class="sv-managed">` with `managed_notice` (`$1` = repo URL `https://github.com/JohnRDOrazio/seminaverbi-templates`), used by `Template:SV managed`.
- Templates must be invoked at line start; doc says so.

- [ ] **Step 1: Failing tests:** `test_discernment_heading_it` exact `== Semi del Verbo e punti di attenzione ==`; `test_discernment_heading_en`; `test_wip_fills_user_and_date`; `test_references_group`; `test_managed_links_repo`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: SV discernment, WIP and references helpers`.

### Task 7: Sync script

**Files:**
- Modify: `sync/pages.py`
- Create: `sync/__main__.py`, `sync/wiki.py`, `sync/plan.py`, `tests/test_sync_pages.py`, `tests/test_sync_plan.py`

**Interfaces:**
- `sync.pages.desired_pages(root: Path, lang: str, sha: str) -> dict[str, str]`: `modules/SV/X.lua` → `Module:SV/X` with first line `-- ` + managed notice in English + repo URL; `Module:SV/Data` = `compile_data(root)`; `templates/NAME.wikitext` → `Template:NAME` with `<noinclude>{{SV managed}}</noinclude>` appended unless already present; `templates/SV infobox/styles.css` → `Template:SV infobox/styles.css` (CSS comment notice); `Template:SV managed` comes from `templates/` like the rest (Task 6) and gets no notice appended to itself; aliases for `lang` → `#REDIRECT [[canonical]]`. Content never contains the sha (keeps re-runs idempotent); the sha goes only in the summary.
- `sync.wiki.WikiClient(lang: str, session=None)` with `login(user, password) -> None` (BotPassword via `action=login`), `fetch(titles: list[str]) -> dict[str, PageState | None]` (`PageState(content: str, last_user: str)`, batched by 50), `edit(title, text, summary) -> None` (CSRF token, `bot=1`, raises `SyncError` on API error).
- `sync.plan.plan(desired: dict[str,str], current: dict[str, PageState|None], bot_user: str, force: set[str]) -> list[Action]` where `Action(title, kind: "create"|"update"|"skip-unchanged"|"skip-foreign", diff: str)`. Comparison ignores trailing whitespace only.
- CLI: `python -m sync [--wiki en,it,…|all] [--dry-run] [--force TITLE]…`; prints per wiki `create N, update N, unchanged N, skipped(foreign) N` and unified diffs on `--dry-run`; exit code 1 if any wiki errored or any foreign page was skipped, after processing all wikis.

- [ ] **Step 1: Failing tests:**
  - `test_titles_for_wiki`: `desired_pages(root,"es",…)` contains `Module:SV/Infobox`, `Module:SV/Data`, `Template:SV cite`, `Template:SV infobox/styles.css`, `Template:SV managed` and the es aliases.
  - `test_same_content_every_wiki`: non-alias pages are byte-identical across all six langs.
  - `test_plan_create_update_unchanged`.
  - `test_foreign_edit_skipped` (Review Focus 4): current last_user `"Johnrdorazio"` and content differs → `skip-foreign`; with title in `force` → `update`.
  - `test_idempotent` (Review Focus 5): feeding `desired` back as `current` (last_user = bot) → all `skip-unchanged`.
  - `test_cli_continues_after_wiki_error` (mock `WikiClient`, es raises) → other wikis processed, exit 1.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: sync script with dry-run and foreign-edit guard`; push branch, open PR (repo conventions: Co-Authored-By trailers; PR body ends with the Claude Code line).

### Task 8: Item enrichment script (server)

**Files:**
- Create: `server/EnrichItems.php`, `server/properties.txt` (Wikidata IDs, one per line: P50 P57 P58 P161 P577 P571 P136 P175 P364 P495 P86 P676 P264 P170 P569 P570 P19 P20 P106 P1476 P18 P155 P156 P31), `server/README.md`

**Interfaces:**
- `php maintenance/run.php /var/www/vhosts/bibleget.io/seminaverbi-tools/EnrichItems.php --wiki=seminaverbi_en [--item Q4178]… [--from-articles en,it] [--properties FILE] [--dry-run]`.
- Behaviour: for each local item (given, or the items connected to main-namespace non-redirect pages on the listed wikis, via `page_props.wikibase_item` in each wiki DB), get its Wikidata ID with `ImportedEntityMappingStore::getOriginalId`; skip with a warning if none. Fetch the Wikidata entity with the factory's `ApiEntityLookup`. For each allow-listed Wikidata property that maps to a local property and for which the local item has **no** statement yet: collect referenced items, import them with `EntityImporter::importEntities($refs, false)` (creates items with labels and sitelinks, no statements), copy statements with `StatementCopier`, and save the item once per item with summary `Enrich from Wikidata <QID>: <props>`. `--dry-run` prints `Q4178 (wd Q15228): + P3990 author = Q… J. R. R. Tolkien` lines and writes nothing.
- Uses services the same way `extensions/WikibaseImport/maintenance/importEntities.php` builds them (`EntityImporterFactory`); read that file first.

- [ ] **Step 1:** Write the script; `php -l` it locally if PHP is available, else on the server.
- [ ] **Step 2:** Copy to `/var/www/vhosts/bibleget.io/seminaverbi-tools/` (outside the docroot; owner `bibleget.io_q71zp2mh9vh:psacln`), run `--item Q4178 --dry-run` as the vhost user with `/opt/plesk/php/8.4/bin/php -d memory_limit=512M`. Expected: an author line for Tolkien, publication date, genre; no writes.
- [ ] **Step 3:** Record `seminaverbi_en` size (`information_schema.tables` sum) → run `--item Q4178` for real → check the item on `en` has P3990 and the referenced Tolkien item exists with an `itwiki` sitelink.
- [ ] **Step 4:** Run `--from-articles en,it --dry-run`, review counts, then for real; record DB size after; abort/report if growth > 200 MB.
- [ ] **Step 5:** Commit `feat: server script enriching local items from Wikidata`; add to the same PR.

### Task 9: Bot account, first sync, smoke test

No new code; operational steps on the live wikis. Each step's command and output go in the PR description.

- [ ] **Step 1: Bot account.** On the server as the vhost user: `php maintenance/run.php createAndPromote --wiki=seminaverbi_en --bot "SV templates bot" <random>` (password discarded), confirm CentralAuth attaches it globally (or `--wiki` per wiki if not), then `php maintenance/run.php createBotPassword --wiki=seminaverbi_en --grants basic,highvolume,editpage,createeditmovepage "SV templates bot" sync`. Hand the generated password to the author to store in `~/.config/seminaverbi/sync.env` (`SV_SYNC_USER="SV templates bot@sync"`, `SV_SYNC_PASSWORD=…`, mode 600); Claude never prints or reads it back. Expected: login succeeds on all six wikis (`python -m sync --wiki all --dry-run` gets past login).
- [ ] **Step 2: BibleGet script on small wikis.** `MediaWiki:Global.js` on `en` loads `MediaWiki:BibleGet.js` with a relative URL, so es/fr/de/pt (which lack that page) get no quotes. The author (interface-admin) changes it to `mw.loader.load('https://en.seminaverbi.bibleget.io/w/index.php?title=MediaWiki:BibleGet.js&action=raw&ctype=text/javascript');`. Expected: on es, a test quote fills in.
- [ ] **Step 3: Dry-run** all wikis; expected: only creates (no SV pages exist yet), zero foreign skips.
- [ ] **Step 4: Sync** all wikis; run it again; expected second run `create 0, update 0`.
- [ ] **Step 5: Smoke pages** `User:SV templates bot/SV test` on `en`, `it`, `fr`: an `SV infobox work` with `item=Q4178`, an `SV Bible quote` inline and block, an `SV cite` web and book, `SV discernment`, `SV references`. Check via `action=parse` that there are no script errors (`scribunto-error` absent) and, on fr, the Latin note is present. Screenshot or paste the rendered HTML snippets into the PR.
- [ ] **Step 6:** Update memory `project_seminaverbi_templates.md` (repo URL, bot name, how to sync) and hand over the PR.
