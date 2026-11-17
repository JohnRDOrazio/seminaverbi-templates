# Complete category tree with Jev adjudication — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the seed tree with a complete, framework-anchored field tree plus a grouped Gospel-themes tree, settle disputed placements with Jev, migrate en/it/fr categories to it, and teach the skill the Jev routine.

**Architecture:** `config/categories.json` (now with definitions and grouped themes) stays the single source. `tools/jev.py` wraps three Jev question types (place, classify, themes) with a code-side policy and a decisions log. `tools/migrate_categories.py` moves/re-files existing categories from a reviewed map. `tools.categories --create` then fills every wiki.

**Tech Stack:** Python 3.12, `typesafe-sdk==0.7.5` (model `jev-1.14.0`), `requests`; Lua 5.1 (Scribunto) under lupa; MediaWiki 1.44 API as `SV editor bot`.

**Spec:** `docs/superpowers/specs/2026-11-17-category-tree-jev-design.md`

## Global Constraints

- Six wikis `en it es fr de pt`; Wikibase repo on en; site IDs `<code>wiki`.
- One parent per node; every node has six names and an English `definition`; names unique per language.
- One work, one page, one field: `classify` returns exactly one field node.
- Jev model pinned `jev-1.14.0`; key only from `TYPESAFE_API_KEY`, never printed, logged or committed.
- Policy: Choice applied when top probability ≥ 0.80 and confidence ≥ 0.6, else `author`; theme applied ≥ 0.75, ask author 0.40–0.75, not applied < 0.40. Thresholds are constants in `tools/jev.py` and recorded with each decision.
- Every Jev answer that changes the tree is appended to `config/category_decisions.json`.
- Wiki edits tagged `seminaverbi-mcp`; credentials via `~/.config/seminaverbi/runeditor.py` (never read `editor.env`).
- `tools.categories` stays create-only; only `tools/migrate_categories.py` edits existing pages, and only category pages and category lines of members.
- Full suite `.venv/bin/pytest -q` green after every task.

## Review Focus

1. A member page whose category line uses a different form than the API title (`[[Categoria:Musica|sortkey]]`, `[[category:musica]]`, underscores) — re-filed correctly, sort key kept; an unmatched member is reported, not silently skipped.
2. Theme groups (non-leaf theme nodes) are never applied to an article; only leaf themes.
3. Jev failure (network, 4xx/5xx, missing answer) — `tools/jev.py` raises a clear error, records nothing, exits 1; the migration never applies a Jev-dependent step on failure.
4. Split category whose member Jev cannot classify confidently — the member is left in its old category and listed for the author, not dropped.
5. A move whose target title already exists — no move; old page becomes a redirect; members re-filed to the target.

---

### Task 1: Complete tree data with definitions and grouped themes

**Files:**
- Modify: `config/categories.json`, `sync/categories.py`, `tools/categories.py` (`page_text` uses `definition`), `modules/SV/Categories.lua` (theme groups), `tools/skilldocs.py` (definitions in `categories.md`), `tests/test_categories_data.py`, `tests/test_category_tree.py`, `tests/test_tools_categories.py`
- Regenerate: `skills/seminaverbi-editor/categories.md`

**Interfaces:**
- Produces `categories.json` shape: nodes `{key, names{6}, definition{en required, others optional}, children?}`; `themes` is now a list of **groups** each with `children` (the themes). `description` is removed (renamed `definition`).
- `sync.categories.Node` = `(key, names, parent_key, definition)`; `nodes()` unchanged order (root, fields depth-first, themes-root, theme groups and themes depth-first).
- New `sync.categories.leaf_themes(data) -> list[Node]` and `sync.categories.field_children(data, key | None) -> list[Node]` (children of a field node; `None` = top level).

- [ ] **Step 1: Failing tests** in `tests/test_categories_data.py`:
  - `test_every_node_has_english_definition` (non-empty `definition.en`, ≤ 300 chars).
  - `test_tree_size` — field nodes between 100 and 200.
  - `test_top_level_fields` — top-level field keys == `arts humanities social-sciences natural-sciences engineering-technology medical-health-sciences religion popular-culture` in that order.
  - `test_no_formal_sciences` — no key `formal-sciences`; keys `history`, `philosophy` under `humanities`; `mathematics`, `computer-science` under `natural-sciences`; `medicine` under `medical-health-sciences`; `theology` under `humanities`.
  - `test_renames` — en names `Sculpture`, `Medicine`, `Video games` present; `Sculpting`, `Medicinal science`, `Video gaming` absent.
  - `test_theme_groups` — six groups with keys `theological-virtues cardinal-virtues sin-grace-salvation human-person communion-society christ-church`; every theme leaf has a group parent. `test_seed_themes` is updated: the former `love` theme becomes key `charity` (en `Charity`, it `Carità`) under theological virtues; the other seed keys stay; `family` keeps it `Famiglia`, en `Family`.
  - `test_leaf_themes_excludes_groups`.
  - Update `test_covers_main_page`: every fixture name maps either to a node name or to an entry of the migration map (Task 4 creates the map; until then assert against node names **or** a list `RENAMED = {...}` in the test, replaced by the map in Task 4).
  - `tests/test_category_tree.py`: theme group headings render as collapsible blocks under the themes heading; leaf themes as list items.
  - `tests/test_tools_categories.py`: `page_text` uses the localized definition when present, else the English one.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_categories_data.py tests/test_category_tree.py tests/test_tools_categories.py` — Expected: FAIL.
- [ ] **Step 3: Write the tree.** Fields per the spec: Arts (UNESCO FCS 2009 domains), Humanities / Social sciences / Natural sciences / Engineering and technology / Medical and health sciences (OECD FOS 2007 groups, second-level fields summarized), Religion, Popular culture; ~3 levels; no film genres. Every name in six languages (natural Wikipedia-style category names); English definitions from the frameworks. Themes per spec groups. Update code: `definition` replaces `description`; `Categories.lua` renders theme groups with `render()`; `skilldocs` prints `- Name (key): definition`.
- [ ] **Step 4: Run** `.venv/bin/python -m tools.skilldocs && .venv/bin/pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `feat: complete field tree (UNESCO/OECD) and grouped Gospel themes with definitions`

### Task 2: `tools/jev.py`

**Files:**
- Create: `tools/jev.py`, `tests/test_jev.py`, `config/category_decisions.json` (`[]`)
- Modify: `requirements.txt` (`typesafe-sdk==0.7.5`)

**Interfaces:**
- `Decision = NamedTuple(question: str, node: str | None, options: dict[str, str], probabilities: dict[str, float], confidence: float, choice: str, outcome: str)` where `outcome ∈ {"applied", "author"}`.
- `place(client, node: Node, candidates: list[Node], data) -> Decision` — Choice, options = candidate keys → their definitions; state `{node: {name, definition}, candidates: [{key, path, definition}]}`.
- `classify(client, text: str, data) -> Decision` — one field node: level-by-level Choice from the top; descends while the step is confident by policy; returns the deepest confident node (`outcome="author"` if the top level itself is not confident). Beam fallback width 2 only when a step's top two options are within 0.15 of each other: evaluate both children sets and keep the path with the higher product of probabilities.
- `themes(client, text: str, data) -> dict[str, tuple[float, str]]` — one request, one Noul per **leaf** theme: key → (probability, `"applied" | "author" | "no"`).
- `record(decision, path=ROOT/"config/category_decisions.json")` — appends `{id, date, question, node, options, probabilities, confidence, choice, outcome, thresholds}`; `id` = `<date>-<node>-<n>`.
- `make_client()` — `typesafe_sdk.TypeSafeClient()` (reads `TYPESAFE_API_KEY`); `MODEL = "jev-1.14.0"`.
- CLI: `python -m tools.jev place <node-key> <candidate-key>... [--record]`, `python -m tools.jev classify <file>`, `python -m tools.jev themes <file>`; prints choice, probabilities (sorted), confidence, outcome; exit 1 on API error with the error class and message (no key).
- A fake client in tests implements `system_one(state, questions, model)` returning objects with `.choices[id].choice/.probabilities/.confidence` and `.nouls[id].noul`.

- [ ] **Step 1: Failing tests:** `test_place_options_are_candidates_with_definitions`, `test_place_policy_applied_vs_author` (0.85/0.7 → applied; 0.85/0.5 → author; 0.7/0.9 → author), `test_model_pinned`, `test_classify_descends_while_confident`, `test_classify_stops_at_parent_when_unsure`, `test_classify_beam_on_near_tie`, `test_themes_only_leaf_themes_one_request`, `test_themes_bands`, `test_record_appends_with_thresholds`, `test_cli_api_error_exit_1_no_key_in_output` (monkeypatched env key value must not appear).
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_jev.py` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** (install `typesafe-sdk==0.7.5` into `.venv`). Question wording: place — "In a classification of all expressions of human culture, which parent category does `node` belong under? Use the definitions."; classify — "This article is about one work or one person. Which category describes the work's own medium, or the person's main domain? Not its subject."; themes — "Does the article genuinely explore `<theme name>` (`<definition>`), not merely mention it?"
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — Expected: all pass. Then one live smoke: `python -m tools.jev place comics arts popular-culture` → prints a choice and probabilities, exit 0.
- [ ] **Step 5: Commit** `feat: tools.jev — place, classify and themes with policy and decisions log`

### Task 3: Adjudicate the disputed placements (live, with the author)

**Files:** Modify `config/categories.json`, `config/category_decisions.json`, `config/adjudications.json` (new: the list asked).

- [ ] **Step 1:** Write `config/adjudications.json`: `[{node, candidates}]` for every disputed placement: comics, animation, video-games (arts subdomains vs popular-culture), christian-roots / cultural-traditions / flags (heritage vs popular-culture vs history), theology (humanities vs religion), sacred-art (arts vs religion), architecture (arts vs engineering-technology), culinary-arts (arts vs popular-culture), geography (social vs natural), psychology (social vs medical), linguistics (humanities vs social), plus any node whose definition in Task 1 named an alternative.
- [ ] **Step 2:** Run `python -m tools.jev place … --record` for each (one request per node). Expected: decisions appended.
- [ ] **Step 3:** Apply `applied` outcomes to `categories.json` (move node under the chosen parent). **Stop and bring every `author` outcome to the author** with Jev's probabilities; record his choice as `author_choice` in the log, apply it.
- [ ] **Step 4:** `python -m tools.skilldocs && .venv/bin/pytest -q` — pass. Commit `feat: adjudicated placements (Jev + author)`.

### Task 4: Migration map and `tools/migrate_categories.py`

**Files:**
- Create: `config/category_migration.json`, `tools/migrate_categories.py`, `tests/test_migrate_categories.py`
- Modify: `sync/wiki.py` (`move`, `members`, `page_text` helpers), `tests/test_sync_wiki.py`, `tests/test_categories_data.py` (`test_covers_main_page` uses the map)

**Interfaces:**
- Map: `{ "<lang>": { "<old category name>": "<node key>" | "split" } }` for en, it, fr.
- `WikiClient.move(old, new, reason, tags, noredirect=False)`, `WikiClient.members(category_title) -> list[(title, ns)]` (`list=categorymembers`, all namespaces, `continue`), `WikiClient.wikitext(title) -> str | None` (via `fetch`).
- `plan_migration(lang, data, mapping, client, jev_client) -> list[Step]`; `Step` kinds: `move(old→new)`, `redirect(old→target)`, `refile(member, old→new)`, `set_text(category page)`, `author(member, reason, decision)`.
- `refile_text(text, old_names, new_name) -> (new_text, changed: bool)` — regex over `[[ *(Category|<localized ns>|<aliases>) *: *Old name *(|sortkey)? *]]`, case-insensitive first letter, `_`≡space; keeps sort key.
- CLI: `python -m tools.migrate_categories --wiki <en|it|fr> [--dry-run]`; dry run prints every step and unified diffs; exits 1 if any `author` step or unmatched member.

- [ ] **Step 1: Failing tests:** `test_map_never_drops_members` (every old category with members maps to a key or `split`), `test_refile_keeps_sortkey`, `test_refile_localized_and_lowercase_and_underscore`, `test_unmatched_member_reported`, `test_move_then_set_text`, `test_target_exists_redirect_and_refile_to_target`, `test_split_uses_classify_confident`, `test_split_unsure_member_left_for_author`, `test_jev_failure_aborts_split_without_edits`, `test_dry_run_no_edits`, `test_no_successor_becomes_redirect_listed`.
- [ ] **Step 2: Run** the file — Expected: FAIL.
- [ ] **Step 3: Implement** the client helpers, the planner and the executor (moves first, then re-files, then category page texts; every edit tagged; localized summary "Category tree migration"/translations).
- [ ] **Step 4: Draft the map** from the live wikis (read-only: category pages with members on en/it/fr) and the tree; the categories without a clear target go to Jev `place`-style adjudication or `split`.
- [ ] **Step 5: Run** `.venv/bin/pytest -q` — pass. Commit `feat: category migration tool and reviewed map`.

### Task 5: Skill — Jev routine and the one-field rule

**Files:** Modify `skills/seminaverbi-editor/SKILL.md`, `skills/seminaverbi-editor/article-standard.md`; `tests/test_skilldocs.py` (`test_skill_mentions_jev_routine`: SKILL.md contains `tools.jev classify`, `tools.jev themes`, `tools.jev place`, the three thresholds, and "one field").

- [ ] **Step 1:** Write the failing test; run — FAIL.
- [ ] **Step 2:** SKILL.md: a "Filing with Jev" section — run `classify` and `themes` on the final article text (via `.venv/bin/python -m tools.jev …` with the text in a temp file); apply `applied`; show `author` items with probabilities and wait; never tag a theme below 0.40, never a theme group; one field category per page; adaptations get their own page linked both ways. Tree changes: `place` + PR to `categories.json` + decision recorded. article-standard.md: the one-work-one-page-one-field rule.
- [ ] **Step 3:** `.venv/bin/pytest -q` — pass. Commit `feat(skill): Jev routine and one-field rule`.

### Task 6: Live migration and fill (with the author)

- [ ] **Step 1:** `runsync.py --wiki all --dry-run` then real: `Module:SV/Data` updated everywhere (tree + definitions); second run 0/0.
- [ ] **Step 2:** `runeditor.py tools.migrate_categories --wiki en --dry-run` (then it, fr) — **stop: author reviews the dry-run output** and any `author` steps.
- [ ] **Step 3:** Real migration on en, it, fr. Then `runeditor.py tools.categories check --wiki <code> --create` on all six (run in background; ~3 min each).
- [ ] **Step 4:** `tools.categories check` on all six → Expected: no missing, no wrong parent, no redirect (except the listed no-successor redirects, which the check reports as `redirect`; list them for the author).
- [ ] **Step 5:** Update memory; push; PR (base `feat/wiki-mcp` until PR #2 merges, then `main`).
