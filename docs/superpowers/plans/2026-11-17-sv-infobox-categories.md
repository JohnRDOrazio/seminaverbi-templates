# Item-driven SV infobox and categories from the item — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Articles get their field and Gospel-theme categories from two statements on their Wikibase item, output by `{{SV infobox}}` (layout chosen by the item's "instance of") or `{{SV categories}}`, and today's hand-written tree categories move onto the items.

**Architecture:** Two new item-type properties on the en repository. `Module:SV/Infobox` gains layout detection and a category renderer shared by the infobox templates and `{{SV categories}}`; mappings live in `config/infobox_types.json`, shipped in `Module:SV/Data`. A Python filing tool (`tools/file_items.py`) plans per item (union of themes, one field, Jev re-check where the field does not fit the type), writes statements, then edits each page only after a parse preview proves the page still shows every tree category it showed before.

**Tech Stack:** Lua (Scribunto, tested through `tests/lua_env.py`), Python 3 (`sync/`, `tools/`, pytest), MediaWiki + Wikibase APIs, TypeSafe Jev (`tools/jev.py`).

**Spec:** `docs/superpowers/specs/2026-11-17-sv-infobox-categories-design.md`

## Global Constraints

- SeminaVerbi site IDs are `en`, `it`, `es`, `fr`, `de`, `pt`; `<lang>wiki` is Wikipedia. Never write a `<lang>wiki` sitelink.
- All writes as SV editor bot (`~/.config/seminaverbi/runeditor.py`), bot flag, tag `seminaverbi-mcp`; module/template pages only through the sync (`runsync.py`, SV sync bot). Never print either bot password.
- Every tool writes nothing without `--apply`; the default is a dry run that prints the plan.
- Statements only on items that are sitelinked to a SeminaVerbi article (main namespace) — never on other mirrored Wikidata items. Skip the main page (Q4).
- Themes on an item = union over its pages; field = exactly one; pages disagreeing on the field → stop for that item, report for the author.
- Jev thresholds as in `tools/jev.py` (`applied` / `author`); every Jev decision recorded in `config/category_decisions.json`.
- `Module:SV/Data` stays the single `mw.loadData` module; new config is a JSON file added to `compile_data`.
- The full suite (`.venv/bin/python -m pytest -q`) passes at every commit.
- Tracking/maintenance category names and new messages exist in all six `i18n/*.json` files (and `qqq` if present).

## Review Focus

1. A hand-written tree line with a sort key (`[[Categoria:Musica|Buona novella]]`), lowercase or English namespace on a non-English wiki (`[[category:…]]` on it), or several tree lines — all removed, and the sort key kept through `sortkey=` on the inserted template.
2. A page whose tree category comes only from a template (the hand-modified Wikipedia infoboxes): statement written, no page edit for that category, the page not reported as changed.
3. Running the filing tool twice: the second run adds no statement and edits no page.
4. An item value that is not a tree item, a second SV field value, or a tree item with no category on this wiki: a visible tracking category, never a silent drop or a red category.
5. A page edited between planning and saving: the edit is refused (base revision), reported, the rest continues.

---

### Task 1: WikiClient — properties, item statements, guarded edits, parse preview

**Files:**
- Modify: `sync/wiki.py`
- Test: `tests/test_sync_wiki.py`

**Interfaces:**
- Produces:
  - `WikiClient.new_property(labels: dict[str,str], descriptions: dict[str,str], datatype: str, tags: str|None=None) -> str` (wbeditentity `new=property`; returns the P-ID)
  - `WikiClient.find_property(label_en: str) -> str|None` (wbsearchentities `type=property`, exact en label match)
  - `WikiClient.item_values(item: str, prop: str) -> list[str]` (Q-IDs of the item-valued statements of `prop`, any rank but deprecated)
  - `WikiClient.add_item_claim(item: str, prop: str, value: str, tags: str|None=None, summary: str|None=None) -> None` (wbcreateclaim, `snaktype=value`, value `{"entity-type":"item","numeric-id":N}`)
  - `WikiClient.revision(title: str) -> tuple[int, str] | None` (latest revid and wikitext; None if missing)
  - `WikiClient.edit(..., baserevid: int|None=None)` — new keyword; when given, sent as `baserevid` with `nocreate=1`; an `editconflict` error raises `SyncError` whose message contains `editconflict`
  - `WikiClient.preview_categories(title: str, text: str) -> set[str]` (action=parse with `title`, `text`, `prop=categories`, `pst=1`; returns bare category names, underscores → spaces)
  - `WikiClient.page_categories(title: str) -> set[str]` (prop=categories incl. template-added, bare names)

- [ ] **Step 1: Write the failing tests** — one per method, using `Recorder`:
  - `test_new_property_posts_entity` — POST params `action=wbeditentity`, `new=property`; `data` JSON has `"datatype": "wikibase-item"` and the labels; returns `"P9001"` from `{"entity": {"id": "P9001"}}`.
  - `test_find_property_exact_label` — search results `[{"id":"P1","label":"SV field x"},{"id":"P2","label":"SV field"}]` → `"P2"`; no exact match → `None`.
  - `test_item_values_skips_deprecated_and_novalue` — claims with ranks normal/preferred/deprecated and a `novalue` snak → only the normal and preferred Q-IDs, in order.
  - `test_add_item_claim_params` — `action=wbcreateclaim`, `entity=Q5`, `property=P9001`, `snaktype=value`, `value='{"entity-type": "item", "numeric-id": 47743}'`, `bot=1`, tags passed.
  - `test_edit_with_baserevid` — params contain `baserevid=123` and `nocreate=1`; an `{"error":{"code":"editconflict"}}` body raises `SyncError` matching `editconflict`.
  - `test_preview_categories` — parse body `{"parse":{"categories":[{"category":"Musica"},{"category":"Arti_dello_spettacolo"}]}}` → `{"Musica","Arti dello spettacolo"}`.
  - `test_page_categories_bare_names` — `{"query":{"pages":[{"categories":[{"title":"Categoria:Musica"}]}]}}` → `{"Musica"}`.

- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest -q tests/test_sync_wiki.py`
  Expected: the 7 new tests FAIL (AttributeError / unexpected keyword).

- [ ] **Step 3: Implement the methods in `sync/wiki.py`** following `new_item`/`set_sitelink`'s style (`_call`, `_dig`, `_token("csrf")`, `bot="1"`).

- [ ] **Step 4: Run the suite** — `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 5: Commit** — `git commit -m "feat(wiki): properties, item statements, guarded edits and parse preview"`

---

### Task 2: The two properties and the tree's item IDs

**Files:**
- Create: `tools/properties.py`
- Modify: `config/wikibase.json` (keys `sv_field`, `sv_theme`), `config/categories.json` (an `"item"` per node, root and themes_root included), `tools/categories.py` (new subcommand `record-items`), `tests/test_categories_data.py`
- Test: `tests/test_properties.py`, `tests/test_tools_categories.py`

**Interfaces:**
- Consumes: Task 1 `find_property`, `new_property`; `WikiClient.item_for(site, title)`.
- Produces:
  - `tools.properties.PROPERTIES` = `{"sv_field": {...labels, descriptions...}, "sv_theme": {...}}` — en labels exactly `"SV field"` and `"SV Gospel theme"`; six languages each.
  - `tools.properties.ensure(repo, apply: bool) -> dict[str,str]` — key → P-ID, reusing a property found by `find_property`, creating otherwise (only when `apply`); writes the IDs into `config/wikibase.json` when `apply`.
  - `python -m tools.categories record-items` — for each node, `item_for("en", "Category:<en name>")`; writes `"item": "Q…"` into that node in `config/categories.json` (key order: `key`, `item`, `names`, …); nodes with no item are reported, exit 1.
  - `tests/test_categories_data.py::test_every_node_has_an_item` — every node (root, fields, themes_root, themes) has `item` matching `^Q\d+$`, all distinct.

- [ ] **Step 1: Failing tests**
  - `test_ensure_reuses_existing` — fake repo whose `find_property("SV field")` → `"P50"`, `("SV Gospel theme")` → None; `ensure(fake, apply=True)` creates only the theme property with `datatype == "wikibase-item"` and returns `{"sv_field": "P50", "sv_theme": <new id>}`.
  - `test_ensure_dry_run_creates_nothing` — `apply=False` → no `new_property` call, returns `{}` for missing ones.
  - `test_record_items_writes_ids` (in `test_tools_categories.py`) — Fake repo mapping en category titles to items; the written JSON has `item` on every node of `DATA`; a node without an item → exit 1 and named in the output.
  - `test_every_node_has_an_item` (fails until Step 5 runs the tool against the live wiki).
- [ ] **Step 2: Run** — `.venv/bin/python -m pytest -q tests/test_properties.py tests/test_tools_categories.py tests/test_categories_data.py` → new tests FAIL.
- [ ] **Step 3: Implement** `tools/properties.py` (CLI `python -m tools.properties [--apply]`, credentials as `tools.categories`) and `record-items`.
- [ ] **Step 4: Run the unit tests** → pass except `test_every_node_has_an_item`.
- [ ] **Step 5: Run live** — `.venv/bin/python ~/.config/seminaverbi/runeditor.py tools.properties` (dry run, read the output), then `… tools.properties --apply`; then `.venv/bin/python -m tools.categories record-items`.
  Expected: two P-IDs in `config/wikibase.json`; every node has an item; `test_every_node_has_an_item` passes; full suite passes.
- [ ] **Step 6: Commit** — `git commit -m "feat: SV field / SV Gospel theme properties; tree nodes carry their item IDs"`

---

### Task 3: Category output from the item (`{{SV categories}}`, and from every SV infobox)

**Files:**
- Modify: `modules/SV/Infobox.lua`, `i18n/*.json`, `config/templates.json`
- Create: `templates/SV categories.wikitext`
- Test: `tests/test_infobox.py`

**Interfaces:**
- Consumes: `data.wikibase.sv_field`, `data.wikibase.sv_theme`, `data.categories` node `item` IDs (Task 2).
- Produces:
  - The set of tree item IDs is built once per call from `data.categories` (root, fields, themes_root, themes; their `item`); field values are checked against the field nodes, theme values against the leaf themes.
  - `Infobox._categories(entity, ctx, sortkey) -> string` — `[[Category:<local title without namespace>|<sortkey>]]` per value (no `|` part when `sortkey` is nil), field first then themes in statement order; uses `mw.wikibase.getSitelink(valueId)` (this wiki).
  - Tracking (messages, all six languages): `category_not_tree` (value not a tree item), `category_two_fields` (more than one SV field: the first is used), `category_not_on_wiki` (tree item with no local sitelink). Each emitted once as a category.
  - `p.categories(frame)` — args `item`, `sortkey`; no item → empty string, no tracking category.
  - `_render` appends `_categories(...)` (with `args.sortkey`) for `work`, `person` and the new `auto` kind.
  - Template `SV categories`: `<includeonly>{{#invoke:SV/Infobox|categories}}</includeonly>` plus the usual `<noinclude>` doc; `config/templates.json` entry documenting `item`, `sortkey`.

- [ ] **Step 1: Failing tests** in `tests/test_infobox.py` (extend `ENTITIES` with field Q47743 → sitelink `it` = `Categoria:Musica`, theme Q47888 → `Categoria:Fede`, a non-tree Q999, and DATA tree items via a `data=` override of `LuaEnv` or the real compiled data):
  - `test_field_and_theme_categories` — output contains `[[Category:Musica]]` and `[[Category:Fede]]`, field before theme.
  - `test_sortkey_param` — `sortkey="Buona novella"` → `[[Category:Musica|Buona novella]]`.
  - `test_not_tree_value_tracked` — value Q999 → the `category_not_tree` category, no `[[Category:` for Q999's label.
  - `test_two_fields_first_used_and_tracked`.
  - `test_tree_item_without_local_category_tracked` — tree item without an `it` sitelink → `category_not_on_wiki`, no red link.
  - `test_sv_categories_without_item_is_empty` — `p.categories` with no page entity → `""`.
  - existing `test_book_from_item`'s `"Category:" not in out` stays true (LOTR has no SV statements).
- [ ] **Step 2: Run** — `.venv/bin/python -m pytest -q tests/test_infobox.py` → new tests FAIL.
- [ ] **Step 3: Implement**; add the messages to `i18n/{en,it,es,fr,de,pt}.json`; template and `templates.json` entry; regenerate skill docs: `.venv/bin/python -m tools.skilldocs`.
- [ ] **Step 4: Run the suite** → all pass (incl. `test_skilldocs`, `test_data`).
- [ ] **Step 5: Commit** — `git commit -m "feat(infobox): categories from the item's SV field and SV Gospel theme statements"`

---

### Task 4: `{{SV infobox}}` — layout from "instance of"

**Files:**
- Create: `config/infobox_types.json`, `templates/SV infobox.wikitext`
- Modify: `config/infobox.json` (add layouts `work` and `group`), `sync/pages.py` (`compile_data` includes `infobox_types`), `modules/SV/Infobox.lua`, `config/templates.json`, `i18n/*.json`, `tests/test_data.py`
- Test: `tests/test_infobox.py`, `tests/test_data.py`

**Interfaces:**
- Produces:
  - `config/infobox_types.json`:
    ```json
    {"types": {"Q13367": "film", "Q6022": "tv", "Q13121": "person", "Q2404": "group",
               "Q323": "book", "Q2742": "book", "Q46661": "book", "Q47731": "book",
               "Q2031": "play", "Q2407": "album", "Q47692": "work", "Q1025": "work"},
     "fit": {"book": ["literature"], "film": ["film", "animation"], "tv": ["television", "animation"],
             "album": ["music"], "song": ["music"], "group": ["music"],
             "play": ["theatre", "musical-theatre", "drama", "opera"],
             "artwork": ["visual-arts", "crafts", "sacred-art"]}}
    ```
    `fit[layout]` lists tree keys whose subtree a field of that layout should lie in; layouts absent from `fit` (`person`, `work`) are never checked.
  - Layout `work`: image, title, creator, publication_date, genre, country_of_origin. Layout `group`: image, inception, genre, record_label, country_of_origin (all props already in `wikibase.json`).
  - `Infobox.layoutFor(entity, data) -> layout, tracked` — the first `instance_of` value (best rank) present in `types`; else `person` if any value is Q13121, else `work` with `tracked = true` (tracking message `infobox_unknown_type`, six languages).
  - `p.auto(frame)` — kind `auto`: `args.type` wins, else `layoutFor`; same rows/override/caption behaviour as `work`; appends categories (Task 3).
  - Template `SV infobox`: `{{#invoke:SV/Infobox|auto}}`; `templates.json` entry (`type`, `item`, `name`, `sortkey`, any field key).
  - `tests/test_data.py::test_infobox_types_resolve` — every `types` value is a layout in `infobox.json`; every `fit` key a layout; every `fit` tree key exists in `categories.json`.

- [ ] **Step 1: Failing tests**
  - `test_auto_picks_film_from_instance_of` — entity with P2016 = Q13367 → `sv-infobox-film`.
  - `test_auto_type_param_wins` — `type="tv"` on the same entity → `sv-infobox-tv`.
  - `test_auto_first_mapped_instance_wins` — P2016 = [Q776 (unmapped), Q1025] → `sv-infobox-work`, no unknown-type tracking.
  - `test_auto_unknown_type_tracked` — P2016 = Q999 → `sv-infobox-work` + the `infobox_unknown_type` category.
  - `test_auto_human_is_person`.
  - `test_infobox_types_resolve`.
- [ ] **Step 2: Run** — `.venv/bin/python -m pytest -q tests/test_infobox.py tests/test_data.py` → FAIL.
- [ ] **Step 3: Implement**; regenerate skill docs.
- [ ] **Step 4: Run the suite** → all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(infobox): {{SV infobox}} picks its layout from the item's instance of"`

---

### Task 5: Filing plan (pure)

**Files:**
- Create: `tools/file_items.py` (planning half)
- Test: `tests/test_file_items.py`

**Interfaces:**
- Consumes: `sync.categories.load/nodes`, `config/infobox_types.json`, `tools.migrate_categories._line_regex` (reuse, do not copy), Task 2 node items.
- Produces:
  - `class PageFacts(NamedTuple): lang, title, revid, text, categories: set[str]` (bare names, incl. template-added)
  - `class ItemPlan(NamedTuple): item, field: str|None, themes: list[str], add: list[tuple[str,str]]` (prop key `sv_field`/`sv_theme`, Q-ID), `edits: dict[str, tuple[str, str, int]]` (lang → (title, new text, base revid)), `stop: str|None` (reason; nothing written for this item), `recheck: bool` (field does not fit the type)
  - `tree_keys(lang, names: set[str], data) -> tuple[set[str], set[str]]` — (field keys, theme keys) for bare category names on that wiki.
  - `fits(field_key, layout, data, types) -> bool` — True when the layout has no `fit` entry or `field_key` is in / below one of its keys.
  - `rewrite(text, lang, remove: list[str], namespaces, has_module: bool) -> str` — deletes the hand-written lines of `remove` (bare names) using `_line_regex`; if any removed line had a sort key, the first one is passed as `sortkey=`; inserts `{{SV categories}}` (or `{{SV categories|sortkey=…}}`) where the first removed line was, unless `has_module` (the page already calls `SV infobox*` / `SV categories`), in which case a sort key goes on that call only if it has none.
  - `plan_item(item, pages: list[PageFacts], existing: dict[str, list[str]], layout: str, data, types, namespaces: dict[str, list[str]], node_item: dict[str,str]) -> ItemPlan` — union of themes; one field (disagreement → `stop="field disagreement: en=…, it=…"`); `recheck = not fits(...)`; `add` = wanted minus `existing`; `edits` only for pages with a hand-written tree line.

- [ ] **Step 1: Failing tests**
  - `test_union_of_themes_one_field` — en {film, conscience, family}, it {film, conscience, family, forgiveness} → field `film`, themes union, `add` has 1 field + 3 themes.
  - `test_field_disagreement_stops` — en film, it television → `stop` set, `add == []`, `edits == {}`.
  - `test_existing_statements_not_added_again` — `existing={"sv_field":["Q_film"],"sv_theme":["Q_conscience"]}` → those absent from `add` (Review Focus 3).
  - `test_template_only_category_no_edit` — category in `categories` but no hand-written line in `text` → statement in `add`, lang not in `edits` (Review Focus 2).
  - `test_rewrite_sortkey_lowercase_and_english_namespace` — it text with `[[categoria:Musica|Buona novella]]` and `[[Category:Fede]]` → both gone, `{{SV categories|sortkey=Buona novella}}` where the first was (Review Focus 1).
  - `test_rewrite_page_with_sv_infobox_gets_no_second_call`.
  - `test_fits` — (`television`, `tv`) True; (`film`, `tv`) False; (`film`, `person`) True; (`novels`, `book`) True.
  - `test_main_page_and_unconnected_skipped` — handled by the caller: `plan_item` is never given the main page (asserted in Task 6's test).
- [ ] **Step 2: Run** — `.venv/bin/python -m pytest -q tests/test_file_items.py` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the suite** → pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(tools): filing plan — field and themes from the pages to the item"`

---

### Task 6: Filing execution, Jev re-check and CLI

**Files:**
- Modify: `tools/file_items.py`
- Test: `tests/test_file_items.py`

**Interfaces:**
- Consumes: Task 1 methods; Task 5 `plan_item`; `tools.jev.classify`, `tools.jev.record`; Task 4 layout rules (Python twin: `layout_for(instance_of: list[str], types) -> str` — same rule as the Lua `layoutFor`).
- Produces:
  - `collect(repo, clients, data) -> list[tuple[str, list[PageFacts], dict, str]]` — every article (ns 0, not a redirect, not Q4) on the six wikis connected to an item, grouped by item; plus a list of unconnected articles (reported, untouched).
  - `recheck_field(jev_client, item_plan, pages, layout_label, data) -> Decision` — `jev.classify` on the first page in en, it, es, fr, de, pt order, text prefixed `"Type: <instance-of label>.\n\n"`; `record()` always; `applied` replaces the field, `author` sets `stop="field for the author: …"`.
  - `execute(repo, clients, plans, apply: bool) -> Report` — per item, in order: (1) `add_item_claim` for each `add`; (2) for each edit: `preview_categories(title, new_text)` must contain every tree category the page showed before (the replaced field excepted when Jev changed it) — else skip the edit and report `unsafe`; (3) `edit(..., baserevid=…)`; an `editconflict` is reported and the run continues (Review Focus 5). Without `apply`: prints the plan, writes nothing.
  - CLI `python -m tools.file_items [--wiki en,it,…] [--apply] [--item Q…]`; credentials `SV_EDITOR_USER`/`SV_EDITOR_PASSWORD`; summary per wiki in its language (`file_summary` message added to `i18n/*.json`); exit 1 if anything stopped, was unsafe or failed.

- [ ] **Step 1: Failing tests** (fakes for repo/clients/Jev, as in `test_migrate_categories.py`):
  - `test_dry_run_writes_nothing`.
  - `test_statements_before_edits` — call order: all `add_item_claim` for an item precede its `edit`.
  - `test_unsafe_preview_skips_edit` — preview missing a category → no edit, reported `unsafe`, exit 1.
  - `test_editconflict_reported_and_run_continues`.
  - `test_recheck_applied_replaces_field_and_records` — TV-series item filed `film`; fake Jev classify → `television` applied → `add` has `television`, `record` called once.
  - `test_recheck_author_stops_item`.
  - `test_main_page_never_planned`.
  - `test_second_run_is_a_no_op` — after an applied run the fakes hold the statements and new texts; a second run plans no `add` and no `edits`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the suite** → pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(tools): file_items — statements, Jev re-check, guarded page edits"`

---

### Task 7: Deploy and file (live)

**Files:** none new (ledger notes only); `config/category_decisions.json` gains the Jev re-checks.

- [ ] **Step 1: Sync modules/templates/data to all six wikis**
  Run: `.venv/bin/python ~/.config/seminaverbi/runsync.py --wiki all --dry-run`, read it, then without `--dry-run`.
  Expected: updates to `Module:SV/Infobox`, `Module:SV/Data`, new `Template:SV infobox`, `Template:SV categories`; a second dry run reports create 0 / update 0.
- [ ] **Step 2: File one item and check re-rendering.** Dry-run, then apply, `… runeditor.py tools.file_items --item Q47169` (*Aggiungi un posto a tavola*, en + it, field Theatre on both). Then add one theme statement to Q47169 and, without purging, poll both pages' `page_categories` for up to 10 minutes; remove the statement again and poll the same way.
  Expected: both changes appear on both pages. If not: record it in the ledger, add a purge of the item's pages after statement edits in `execute` (with its own test), and report the client change-dispatch configuration to the author as a separate ops issue before Step 3.
- [ ] **Step 3: Dry run on all wikis** — `… runeditor.py tools.file_items` → save to the workspace; show the author the summary: items, statements per item, pages to edit, stops (field disagreements, Jev `author` cases), unconnected articles.
- [ ] **Step 4: Apply** after the author has seen Step 3 — `… tools.file_items --apply`; then a second dry run.
  Expected: second run plans nothing (Review Focus 3); `tools.categories check --wiki X` unchanged for every wiki; no article left without a tree category it had (compare `page_categories` before/after, saved in the workspace).
- [ ] **Step 5: Commit** `config/category_decisions.json` — `git commit -m "chore: Jev field re-checks from the item filing"`

---

### Task 8: Skill and docs

**Files:**
- Modify: `skills/seminaverbi-editor/SKILL.md`, `config/templates.json` (already from Tasks 3–4), `skills/seminaverbi-editor/templates.md` (generated), `tests/test_skilldocs.py`

**Interfaces:**
- Produces: SKILL.md rules —
  - Field and themes are **statements on the item** (`SV field` once, `SV Gospel theme` per theme), written with `wikibase-add-statement`, never `[[Category:…]]` lines for tree categories.
  - Every article has `{{SV infobox}}` or `{{SV categories}}`.
  - A new article first searches the mirror (`wikibase-search-entities`) for its subject's item and adds the SeminaVerbi sitelink; creates an item only when none exists.
  - The Jev routine's outputs go to the item; `author` outcomes still wait for the author.
  - The "statements only when the author asks" rule now excludes `SV field` / `SV Gospel theme`.
  - `test_skilldocs.py::test_skill_files_on_the_item` — SKILL.md mentions `SV field`, `SV Gospel theme`, `wikibase-add-statement` and `{{SV categories}}`.
- [ ] **Step 1: Failing test** `test_skill_files_on_the_item` → run → FAIL.
- [ ] **Step 2: Edit SKILL.md**; `.venv/bin/python -m tools.skilldocs`.
- [ ] **Step 3: Run the suite** → pass.
- [ ] **Step 4: Commit** — `git commit -m "docs(skill): file articles on the item; SV infobox / SV categories"`
