# Connect every article, then `{{SV infobox}}` + `{{SV bio}}` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect every on-topic SeminaVerbi article to a Wikibase item (imported from Wikidata), then replace each page's Wikipedia infobox with a bare `{{SV infobox}}` and it `{{Bio}}` with a new item-driven `{{SV bio}}`, moving onto the item every fact the old templates showed.

**Architecture:** Server side, `server/EnrichItems.php` gains an `--import` mode (items and properties from Wikidata, with the WikibaseImport mapping row). Client side, two one-shot Python tools in the style of `tools/file_items.py`: `tools/connect_items.py` (find the Wikidata item, import, sitelink, file) and `tools/switch_infobox.py` (map the old template's parameters to SV rows, write missing facts, swap the template under the guarded-save rules). Lua: `Module:SV/Bio` renders the opening sentence from the item in six languages.

**Tech Stack:** Python 3 (requests, pytest, lupa, **mwparserfromhell** new), Lua 5.1 / Scribunto, PHP (MediaWiki 1.44 maintenance script), Wikibase.

**Spec:** `docs/superpowers/specs/2026-11-18-connect-and-sv-infobox-design.md`

## Global Constraints

- Items live on en; SeminaVerbi site IDs are `en`…`pt`, never `<lang>wiki` (that is Wikipedia).
- All wiki writes as SV editor bot through `~/.config/seminaverbi/runeditor.py`, tag `seminaverbi-mcp`, summary from `i18n/<lang>.json`.
- Page saves are guarded exactly as in `tools/file_items.py`: base revision, a preview that keeps every tree category, a re-read just before saving, and verification of the saved revision (`revision_categories(newrevid)`).
- Server steps run as the vhost user via `maintenance/run --wiki en`; the `seminaverbi_en` database size is checked before and after an import batch; stop if it grows by more than 200 MB.
- Requests to Wikidata send only titles and QIDs, with the repository's generic User-Agent (`sync.wiki.USER_AGENT`).
- Unresolvable values are reported, never guessed; a page switches only when every mapped row with a value is covered on the item, or the author accepted the listed losses for that page.
- Excluded from connecting (deletion list): it JavaScript, Interprete JavaScript, Cookie, Cross-site request forgery, Bevanda alcolica, OpenStreetMap, Musica pop, Anchor, Progetto:Fumetti/Citare fumetti/Esempi; en Draft:Sandbox. Main pages go to Q4.
- Tools are idempotent: a second run plans nothing.
- Every author checkpoint is a stop: dry-run output shown to the author before `--apply`.

## Review Focus

1. A Wikipedia infobox nested in another template, or with a parameter whose value is itself a template (`|released={{Film date|2016|2|19}}`, `|image=[[File:X.jpg|200px]]`): parsed, converted or reported, never mangled into the page.
2. A song and an album, or a novel and its film, with the same title: the kind filter picks the item of the page's own kind, or sends it to the author — never the other one.
3. An item value resolvable only through Wikipedia (a linked person with no SeminaVerbi page): resolved through the Wikidata sitelink and imported, never by label match.
4. `{{SV bio}}` for a person with no birth date, no occupation in the wiki's language, unknown gender, or still living: no empty parentheses, no wrong agreement, no wrong tense.
5. A page that already has `{{SV infobox}}` or `{{SV categories}}`, or two infoboxes: no second SV infobox, `{{SV categories}}` removed only when an SV infobox is present.

---

### Task 1: `EnrichItems.php --import` and new properties

**Files:**
- Modify: `server/EnrichItems.php`, `server/properties.txt`, `server/README.md`

**Interfaces:**
- Produces: `maintenance/run --wiki en <tools-dir>/EnrichItems.php --import Q123 [--import P21]… [--dry-run]`. For each ID: if mapped, print `IMPORT <wd> = <local> (mapped)`; else create the lean copy (items: as `ensureLocal`; properties: datatype, labels, descriptions in the SV languages) and print `IMPORT <wd> = <local> (created)`; on failure `IMPORT <wd> FAILED <reason>`. Imported items are then enriched like `--item` (same code path). Dry run prints `IMPORT <wd> = (would create)`.
- Produces: `properties.txt` gains `P21` (sex or gender), `P27` (country of citizenship), `P2521` (female form of label), `P1549` (demonym); the items Q6581097 (male) and Q6581072 (female) are imported too (Step 4).
- Produces: `--item` also accepts items reached through `--follow P106,P27` (enrich the occupation and country items the given items point to, so P2521 / P1549 exist locally).

- [ ] **Step 1:** Refactor `ensureLocal` into `importEntity( EntityId $wd ): ?string` handling `ItemId` and `NumericPropertyId` (property: `Property::newFromType( $remote datatype )`, via `remote()` generalised to fetch either type). Keep `ensureLocal` as the item-only caller.
- [ ] **Step 2:** Add options `import` (repeatable) and `follow` (comma list); `localItems()` appends imported items and, with `follow`, the local targets of those properties on the selected items.
- [ ] **Step 3:** Lint: `php -l server/EnrichItems.php` → `No syntax errors detected`.
- [ ] **Step 4:** Deploy (README's `install` lines), then on the server as the vhost user: record `seminaverbi_en` size; `--import P21 --import P27 --import P2521 --import P1549 --import Q6581097 --import Q6581072 --dry-run`. Expected: six `IMPORT … (would create)` or `(mapped)` lines. Then without `--dry-run`. Expected: six `IMPORT … = …` lines; size growth < 1 MB.
- [ ] **Step 5:** Commit: `git commit -m "feat(server): EnrichItems --import for items and properties, --follow"`.

### Task 2: Config for the new properties and the import map

**Files:**
- Modify: `config/wikibase.json`, `config/infobox.json` (person layout unchanged; no new rows), `requirements.txt` (+ `mwparserfromhell`)
- Create: `config/infobox_import.json`
- Test: `tests/test_data.py`

**Interfaces:**
- Produces: `wikibase.json` keys `sex_or_gender`, `country_of_citizenship`, `female_form`, `demonym`, `male`, `female` (local IDs from Task 1).
- Produces: `infobox_import.json` = `{"templates": {"<lang>": {"<template name, first letter case-insensitive>": {"layout": "<SV layout>", "params": {"<param>": "<SV row key>"}}}}, "helpers": ["Film date", "Start date", …], "bio": {"it": "Bio"}}`, covering every infobox kind in the spec's survey (en 19, it 11); `Bandiera`, `Album ratings`, `Album reviews` listed as helpers to drop.
- [ ] **Step 1: Failing test** `test_infobox_import_resolves`: every `layout` is a key of `infobox.json`; every mapped row key exists in that layout; every template kind in the survey list (inline in the test) has an entry. Run `.venv/bin/python -m pytest tests/test_data.py -q` → FAIL.
- [ ] **Step 2:** Write `infobox_import.json` (parameter names from the live templates' TemplateData / source on each wiki: `get-page Template:<name>`), add the four `wikibase.json` keys.
- [ ] **Step 3:** Run tests → PASS. Commit `"config: import map for the Wikipedia infoboxes; bio properties"`.

### Task 3: `{{SV bio}}`

**Files:**
- Create: `modules/SV/Bio.lua`, `templates/SV bio.wikitext`, `tests/test_bio.py`
- Modify: `i18n/*.json`, `config/templates.json`, `skills/seminaverbi-editor/templates.md` (generated)

**Interfaces:**
- Consumes: `wikibase.json` keys (Task 2): `birth_date`, `birth_place`, `death_date`, `death_place`, `occupation`, `sex_or_gender`, `country_of_citizenship`, `female_form`, `demonym`. Reuses Infobox's date formatting and `itemLink` — move `formatTime`, `label`, `itemLink` into `Module:SV/Util` (or export from Infobox) rather than duplicating.
- Produces: `{{SV bio|name=|note=|rest=|ref=}}` → `p._sentence(entity, lang, args)` and `p.main(frame)`. Output: one sentence fragment, no categories, no newline.
- i18n keys: `bio_is_living`, `bio_is_dead` (it "è"/"è stato" with feminine "è stata"; en "is"/"was"; es "es"/"fue"; fr "est"/"était"; de "ist"/"war"; pt "é"/"foi"), `bio_article_m`, `bio_article_f` (it "un"/"una", es "un"/"una", fr "un"/"une", pt "um"/"uma", de "ein"/"eine", en "a"), `bio_paren_born`, `bio_paren_span`, `bio_list_and`.
- Gender: the local IDs of Wikidata's Q6581097 (male) and Q6581072 (female) are config values `wikibase.json` `male` / `female` (imported in Task 1). Any other value, or none, means no agreement: the plain occupation label and no article.
- [ ] **Step 1: Failing tests** in `tests/test_bio.py` (using `LuaEnv`), each asserting the exact string:
  - it, male, living: `Nek, pseudonimo di Filippo Neviani (Sassuolo, 6 gennaio 1972), è un cantautore italiano`. Name from the label `Nek`, `note=pseudonimo di Filippo Neviani`, birth place Sassuolo, birth date 1972-01-06 (day precision), occupation `cantautore` (female form `cantautrice`), citizenship Italy (demonym `italiano`, feminine `italiana`).
  - it, female: `… è una cantautrice italiana`.
  - it, dead male: `(Londra, 8 gennaio 1947 – New York, 10 gennaio 2016), è stato un cantautore britannico`.
  - en: `Nek (born 6 January 1972 in Sassuolo) is an Italian singer-songwriter` (en article "an" before a vowel sound: `a`/`an` by first letter vowel).
  - es, fr, de, pt one case each (living male).
  - unknown gender: no article, plain occupation label; missing birth data: no parentheses; no occupation: sentence ends after the parenthesis with the copula omitted.
  - `rest=` appended after the occupation; `ref=` appended verbatim; no `[[Category:` in any output.
  Run `.venv/bin/python -m pytest tests/test_bio.py -q` → FAIL (module not found).
- [ ] **Step 2:** Implement `Module:SV/Bio` and `templates/SV bio.wikitext` (`{{#invoke:SV/Bio|main}}`), i18n keys in six languages, `config/templates.json` entry; regenerate `python -m tools.skilldocs`.
- [ ] **Step 3:** Run the full suite → PASS. Commit `"feat(lua): {{SV bio}}, the opening sentence from the item in six languages"`.

### Task 4: Wikidata client and `tools/connect_items.py`

**Files:**
- Create: `sync/wikidata.py`, `tools/connect_items.py`, `tests/test_wikidata.py`, `tests/test_connect_items.py`
- Modify: `sync/wiki.py` (`set_sitelink` exists; add `add_claim(item, prop, datavalue: dict, tags, summary)` for any datatype)

**Interfaces:**
- Produces: `sync.wikidata.WikidataClient(session=None)`: `by_sitelink(site, titles) -> {title: QID}` (batches of 50, follows Wikipedia redirects via `redirects=yes`), `entity(qid) -> dict` (claims, labels, sitelinks; cached), `search(text, lang) -> [QID]`.
- Produces: `tools.connect_items.plan(articles, repo, wikidata, existing) -> list[Connect]` with `Connect(lang, title, action, wd, local, reason)`; `action` ∈ `"skip-excluded" | "main-page" | "join" (same subject already connected on another wiki) | "import" | "local" (mapped already) | "create" (no Wikidata item) | "author" (several/none confident)`.
- Kind filter: the page's old infobox (via `infobox_import.json` layout) → allowed instance-of Wikidata IDs (`config/infobox_types.json` reversed, plus Wikidata's `Q482994` album, `Q7366` song, `Q11424` film…, kept in `infobox_import.json` as `"kinds": {"<layout>": [QIDs]}`); a candidate whose P31 is not in the page's kind is dropped.
- CLI: `runeditor.py tools.connect_items [--wiki en,it] [--apply] [--server-cmd "ssh catholicdigitalcommons.org sudo -u <vhost> …"]`. Dry run prints the plan; `--apply` runs the server import for `import` rows (parses `IMPORT wd = local` lines), sets sitelinks (`en`/`it`; Q4 for es/fr/de/pt main pages), runs `tools.file_items.run(only_item=local)` per new item, and prints the deletion list.
- [ ] **Step 1: Failing tests** (`test_connect_items.py`, fakes for repo/wikidata/server):
  - `test_excluded_pages_are_listed_not_connected`
  - `test_main_pages_join_q4`
  - `test_translation_joins_the_existing_item` (it David Bowie, en already Q46886 with the same Wikidata ID → `join`)
  - `test_wikipedia_title_gives_the_item`
  - `test_kind_filter_drops_the_album_for_a_song_page`
  - `test_two_candidates_go_to_the_author`
  - `test_mapped_wikidata_item_is_used_not_imported`
  - `test_no_wikidata_item_means_create`
  - `test_second_run_plans_nothing`
  - `test_import_output_parsed` (`IMPORT Q7339 = Q48001 (created)` → local `Q48001`; a `FAILED` line → the row reported, run continues)
  And `test_wikidata.py`: `by_sitelink` batches and follows redirects; User-Agent is `USER_AGENT`.
  Run → FAIL.
- [ ] **Step 2:** Implement. The Wikidata ID of an existing local item comes from its Wikipedia sitelinks (the mirror copies them) looked up on Wikidata, or from the import output.
- [ ] **Step 3:** Suite → PASS. Commit `"feat(tools): connect_items — find, import, sitelink and file the unconnected articles"`.

### Task 5: `tools/switch_infobox.py`

**Files:**
- Create: `tools/switch_infobox.py`, `tests/test_switch_infobox.py`
- Modify: `tools/file_items.py` (extract `guarded_save(client, title, text, revid, keep, summary, out) -> "saved" | "unsafe" | "failed"` and reuse it in both tools; existing tests keep passing)

**Interfaces:**
- Consumes: `infobox_import.json`, `infobox.json`, `wikibase.json`, `WikidataClient`, `WikiClient.add_claim`, `guarded_save`.
- Produces: `convert(row_kind, raw_wikitext, lang, resolver) -> Value | Unresolved` where `Value` is a Wikibase datavalue dict; `plan_page(page, item_claims, mapping) -> PagePlan(title, layout, writes: [(prop, datavalue, shown)], unresolved: [(row, raw)], new_text, bio)`.
- Conversion rules (spec Step 2.3): item rows from wikilinks via local sitelink → else Wikipedia title on Wikidata → import (never by label); dates only from `{{Film date|…}}`, `{{Start date|…}}`, ISO, "17 November 1999"/"17 novembre 1999", bare year (precision 11/10/9); images only if the file exists in a repository the wiki sees (`prop=imageinfo`), and when the commons-media claim is refused the file stays as `image=` on the call.
- Bio (it): `{{Bio|…}}` → `{{SV bio|note=…|rest=…}}` keeping only the free-text parameters (`NoteNascita`→ref, `PostCognome`/`Epoca`… per the map in `infobox_import.json["bio"]`), after its facts (birth/death place and date, `Attività`→occupation, `Nazionalità`→citizenship) are converted like infobox rows. Gate: the old and new sentences, both rendered with `action=parse`, state the same facts (place names, the date strings, the occupation and nationality words); otherwise the page is blocked with both sentences.
- Swap: the infobox call (helpers inside it go with it) → `{{SV infobox}}` (+ `type=` only if `infobox_types` would not give the layout, + `image=` per the rule above); `{{SV categories}}` removed when the page now has `{{SV infobox}}`.
- CLI: `runeditor.py tools.switch_infobox [--wiki en,it] [--page TITLE] [--accept-loss TITLE]… [--apply]`. Dry run prints per page: writes, unresolved rows, the rendered future infobox (`action=parse` of the new text with converted values passed as row parameters) and, for Bio pages, both sentences. `--apply` writes the statements, then the guarded save.
- [ ] **Step 1: Failing tests** (fakes as in `test_file_items.py`):
  - `test_infobox_film_maps_to_film_layout_and_rows`
  - `test_wikilink_resolves_through_local_sitelink` / `…_through_wikipedia_and_imports` / `test_plain_name_is_unresolved`
  - `test_dates`: `{{Film date|2016|2|19}}` → day precision; `1999` → year; `circa 1999` → unresolved
  - `test_image_on_commons_written` / `test_image_refused_stays_on_the_call`
  - `test_row_already_on_the_item_is_not_written`
  - `test_unresolved_row_blocks_the_page` / `test_accept_loss_lets_it_switch`
  - `test_nested_parameter_templates_do_not_break_parsing` (Review Focus 1)
  - `test_page_with_sv_infobox_is_left_alone`; `test_sv_categories_removed_when_sv_infobox_added` (RF 5)
  - `test_bio_becomes_sv_bio_with_free_text` / `test_bio_sentence_mismatch_blocks`
  - `test_save_is_guarded` (preview loses a tree category → unsafe, not saved)
  - `test_second_run_plans_nothing`
  Run → FAIL.
- [ ] **Step 2:** Extract `guarded_save` from `file_items.run` (run `tests/test_file_items.py` → PASS), then implement `switch_infobox` with mwparserfromhell.
- [ ] **Step 3:** Suite → PASS. Commit `"feat(tools): switch_infobox — facts to the item, Wikipedia infobox and Bio to SV templates"`.

### Task 6: Deploy and connect (live)

- [ ] **Step 1:** Sync modules and templates: `runsync.py --wiki all --dry-run` → only `Module:SV/Bio`, `Module:SV/Util` (if changed), `Module:SV/Data`, `Template:SV bio` change; then without `--dry-run`; second dry run → 0 changes.
- [ ] **Step 2:** `runeditor.py tools.connect_items` (dry run). **Author checkpoint:** show the plan (joins, imports, creates, author rows, deletion list); the author rules on `author` rows (recorded as `--wd TITLE=QID` / `--create TITLE`).
- [ ] **Step 3:** Record DB size; `--apply`; record DB size (< 200 MB growth). Second dry run → plans nothing. `tools.file_items` dry run → nothing left to file; `tools.categories check` unchanged on every wiki.

### Task 7: Enrich and switch (live)

- [ ] **Step 1:** Server: `EnrichItems.php --from-articles en,it --follow P106,P27 --dry-run`, then for real (DB size guard).
- [ ] **Step 2:** `runeditor.py tools.switch_infobox` (dry run). **Author checkpoint:** per page writes, unresolved rows, rendered infobox, Bio sentences; the author names any `--accept-loss` pages.
- [ ] **Step 3:** `--apply`. Second dry run → plans nothing. Category snapshot before/after identical for every switched page (as in the previous plan's Task 7).

### Task 8: Skill and docs

- [ ] **Step 1: Failing test** `test_skilldocs.py::test_skill_uses_sv_bio_and_bare_infobox` — SKILL.md mentions `{{SV bio}}`, "bare `{{SV infobox}}`", `wikibase-search-entities` then Wikidata import for a new subject, and the `image=` exception.
- [ ] **Step 2:** Edit SKILL.md (new article: connect first; facts on the item; `{{SV bio}}` for people on every wiki; never a Wikipedia infobox); regenerate skill docs; README for `server/`.
- [ ] **Step 3:** Suite → PASS. Commit `"docs(skill): bare SV infobox and SV bio; connect before writing"`.
