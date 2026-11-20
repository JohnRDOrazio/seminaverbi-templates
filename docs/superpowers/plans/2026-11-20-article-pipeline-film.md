# Article pipeline, phase 1 (shared core, film and television) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A resumable workstation pipeline that finds film and television candidates on Wikidata, judges them with Jev, posts the survivors as `{{SV proposal}}` pages on en, and promotes the ones that reach the vote threshold.

**Architecture:** A `pipeline/` package beside `sync/` and `tools/`: a SQLite queue, shared stages that call a domain module's `candidates()`, `triage()` and `dossier()`, and a CLI. Jev questions reuse `tools/jev.py` and add three new ones; every Jev answer is cached in the queue by question hash. The proposal box is a template plus a Lua module, synced like the other SV templates.

**Tech Stack:** Python 3 (`requests`, `sqlite3`, `typesafe-sdk` 0.7.5), pytest, Lua (Scribunto) tested through `tests/lua_env.py`.

**Spec:** `docs/superpowers/specs/2026-11-20-article-pipeline-film-design.md`

## Global Constraints

- Stages 0–6 run on the workstation; only stage 6 and `votes` write, only to the project namespace (`Semina Verbi:`) on en, as SV editor bot through `~/.config/seminaverbi/runeditor.py`.
- Never print `TYPESAFE_API_KEY` or the bot password; never connect as administrator.
- Jev thresholds: field applied at probability ≥ 0.80 and confidence ≥ 0.60 (`tools.jev.confident`); theme applied ≥ 0.75, asked 0.40–0.75, never kept < 0.40; stage 3 drops < 0.40.
- No IMDb, no TMDb. Popularity floor: Wikidata sitelinks ≥ 15 for facet candidates; none for seed-list candidates.
- SPARQL: `https://query.wikidata.org/sparql`, one query at a time, 60 s timeout, back-off on 429, generic User-Agent (the repository's `USER_AGENT`, no personal details).
- `max_new_per_run` = 200 candidates entering stage 3 per run; vote threshold = 3. Both in `config/pipeline.json`.
- Evidence excerpt ≤ 25 words, with its source URL; dossier text is notes, never posted whole.
- The bot rewrites only the `{{SV proposal|…}}` call at the top of a proposal page; never moves or deletes a proposal page; never changes a `status=declined`; never proposes a declined QID again.
- Calibration rows always pass stages 1 and 2 (stage 1 records their coverage) and are never dropped at stage 3.
- Queue file: `data/pipeline.sqlite`, git-ignored. No local Wikibase import before approval.

## Review Focus

- A candidate whose Wikipedia title is a redirect or has no en sitelink: coverage and title choice must still work (Task 4, Task 9 tests).
- A human edit below the proposal box, or a `status=declined` set by hand, followed by a bot re-run: both survive (Task 9, Task 10 tests).
- An interrupted run (exception mid-stage): the next run resumes without re-asking Jev for answered rows (Task 1, Task 5 tests).
- A proposal title containing characters MediaWiki forbids (`#<>[]|{}`) or a colon prefix: sanitised, and stable across runs (Task 9 test).
- Jev unavailable (`TypeSafeError`): the stage stops cleanly, the row keeps no outcome, nothing is written to the wiki (Task 5 test).

---

### Task 1: The queue and the pipeline config

**Files:**
- Create: `pipeline/__init__.py`, `pipeline/queue.py`, `config/pipeline.json`
- Modify: `.gitignore` (add `data/`)
- Test: `tests/test_pipeline_queue.py`

**Interfaces:**
- Produces: `Queue(path: Path | str)` (`":memory:"` allowed) with
  `add(qid, domain, source, labels: dict, sitelinks: dict, calibration=False) -> bool` (False when the QID exists; then the source is appended to its sources),
  `pending(stage: int, domain: str | None = None, limit: int | None = None) -> list[Row]` (rows whose stage `stage-1` outcome is `pass` — stage 0 counts as passed on insert — and whose `stage` outcome is unset, never `declined` rows),
  `set_outcome(qid, stage: int, outcome: str, data: dict | None = None)` (outcome in `pass`, `drop`, `translation`),
  `redo(stage: int, qid: str | None = None)` (clears that stage and later ones),
  `jev_get(qid, qhash) -> dict | None`, `jev_put(qid, qhash, question: str, answer: dict)`,
  `set_status(qid, status: str, **fields)` (`open`, `approved`, `created`, `declined`), `by_status(status) -> list[Row]`.
  `Row` is a dataclass: `qid, domain, sources: list, labels, sitelinks, calibration: bool, outcomes: dict[int, str], data: dict[int, dict], status: str | None, page: str | None`.
- Produces: `load_config() -> dict` in `pipeline/__init__.py`, reading `config/pipeline.json`:
  `{"vote_threshold": 3, "max_new_per_run": 200, "sitelink_floor": 15, "quick_drop": 0.40, "dossier_languages": 3, "film": {"seeds": {"vatican_page": "Vatican list of films", "awards": ["Q1033465", "Q1573166"]}, "biblical_classes": [], "subjects": []}}` (the builder fills the QID lists in Task 3 and confirms the two award QIDs on Wikidata: Prize of the Ecumenical Jury, SIGNIS Award).

- [ ] **Step 1: Write the failing tests**

```python
def test_add_is_idempotent_and_merges_sources(q):
    assert q.add("Q1", "film", "vatican", {"en": "A"}, {"enwiki": "A"}) is True
    assert q.add("Q1", "film", "facet", {}, {}) is False
    assert q.pending(1)[0].sources == ["vatican", "facet"]

def test_pending_follows_outcomes(q):
    q.add("Q1", "film", "s", {}, {}); q.add("Q2", "film", "s", {}, {})
    q.set_outcome("Q1", 1, "pass"); q.set_outcome("Q2", 1, "drop")
    assert [r.qid for r in q.pending(2)] == ["Q1"]
    assert q.pending(1) == []

def test_redo_clears_the_stage_and_later(q):
    q.add("Q1", "film", "s", {}, {})
    for s in (1, 2, 3): q.set_outcome("Q1", s, "pass")
    q.redo(2, "Q1")
    assert [r.qid for r in q.pending(2)] == ["Q1"] and q.pending(3) == []

def test_jev_cache_survives_reopen(tmp_path):
    q = Queue(tmp_path / "p.sqlite"); q.add("Q1", "film", "s", {}, {})
    q.jev_put("Q1", "h1", "question?", {"p": 0.7})
    assert Queue(tmp_path / "p.sqlite").jev_get("Q1", "h1") == {"p": 0.7}

def test_declined_rows_are_never_pending(q):
    q.add("Q1", "film", "s", {}, {}); q.set_status("Q1", "declined", reason="off topic")
    assert q.pending(1) == []
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_queue.py -v`. Expected: FAIL (`ModuleNotFoundError: pipeline`).
- [ ] **Step 3:** Implement `pipeline/queue.py` (tables `candidates`, `outcomes(qid, stage, outcome, data_json, at)`, `jev(qid, qhash, question, answer_json, at)`; JSON columns for lists and dicts; commit after each write), `load_config`, `config/pipeline.json`, `.gitignore`.
- [ ] **Step 4:** Run the tests again. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): the candidate queue and its config`.

### Task 2: SPARQL and Wikipedia reading

**Files:**
- Create: `pipeline/sources.py`
- Test: `tests/test_pipeline_sources.py`

**Interfaces:**
- Consumes: `sync.wiki.USER_AGENT`, `sync.wiki.WikiClient` (its retry loop) for Wikipedia calls.
- Produces: `Sparql(session=None).select(query: str) -> list[dict[str, str]]` (bindings flattened to `{var: value}`, entity URIs reduced to `Q…`); retries 429/503 with `Retry-After`, at most 3 times, then raises `SyncError`.
- Produces: `Wikipedia(lang, session=None)` with `lead(title) -> str` (plain-text intro, `prop=extracts&exintro&explaintext`, redirects followed), `sections(title) -> list[tuple[str, str]]` (heading, plain text; `action=parse&prop=sections` then per-section `prop=wikitext` stripped with `mwparserfromhell` if present in `requirements.txt`, else `prop=text` stripped of tags), `url(title) -> str`.

- [ ] **Step 1: Write the failing tests**, with a fake session returning canned JSON:
  `test_select_flattens_bindings` (a binding `{"item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q42"}, "n": {"value": "7"}}` → `{"item": "Q42", "n": "7"}`),
  `test_select_retries_on_429_then_succeeds` (one 429 with `Retry-After: 0`, then 200 → one result, two requests),
  `test_lead_follows_redirects` (asserts `redirects=1` in the request params),
  `test_user_agent_has_no_email` (the session header has no `@`).
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_sources.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `pipeline/sources.py`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): SPARQL and Wikipedia readers`.

### Task 3: Film candidates and triage (stages 0 and 2)

**Files:**
- Create: `pipeline/domains/__init__.py` (`DOMAINS = {"film": film}`), `pipeline/domains/film.py`
- Modify: `config/pipeline.json` (`film.subjects`: the QIDs P921/P144 may point to — Jesus Q302, Bible Q1845, Christianity Q5043, plus the builder's list of the Bible's books via `instance of` book of the Bible Q29154430 resolved at run time; `film.biblical_classes`; `film.genres`: Christian film, biblical film, religious film QIDs)
- Test: `tests/test_pipeline_film.py`

**Interfaces:**
- Consumes: `Sparql`, `Wikipedia("en")`, `Queue.add`, `load_config`.
- Produces: `candidates(queue, sparql, wikipedia, cfg) -> int` (rows added); sources tagged `vatican`, `award:<QID>`, `facet`, `calibration`. Calibration QIDs come from `calibration_qids(wikis: dict[str, WikiClient]) -> list[str]`: members of the film and television tree categories (`sync.categories`, keys `film`, `television` and their descendants) on all six wikis, mapped through each page's local item to its Wikidata QID (the item's `enwiki` sitelink resolved on Wikidata); unmapped pages returned in a second list for the report.
- Produces: `triage(row, cfg) -> tuple[str, str]` (outcome, reason): `drop` when facet-only and sitelinks < `sitelink_floor`, or when the item is an episode, trailer or clip (instance-of check against a constant set in `film.py`); else `pass`. Seed-list and calibration rows have no floor.
- Each SPARQL query returns QID, labels in the six languages, sitelinks (`schema:about`) for the six Wikipedias, and the sitelink count; television uses Q5398426 subclasses, film Q11424 subclasses (`wdt:P31/wdt:P279*`).

- [ ] **Step 1: Write the failing tests** with a fake `Sparql` and fake `Wikipedia`:
  `test_vatican_links_resolve_to_qids_and_unresolved_are_reported`,
  `test_award_and_facet_sources_merge_on_one_row`,
  `test_triage_floor_applies_to_facet_rows_only` (facet row with 14 sitelinks → `drop`; seed row with 2 → `pass`),
  `test_triage_drops_episodes`,
  `test_calibration_rows_are_flagged`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_film.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Unresolved Vatican titles are logged to stdout, never guessed. Arts & Faith: search for a machine-readable copy; if none, add nothing and note "Arts & Faith dropped (no machine-readable list)" in the module docstring and the README section of Task 11.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): film and television candidates and triage`.

### Task 4: Already covered? (stage 1)

**Files:**
- Create: `pipeline/stages.py`
- Test: `tests/test_pipeline_stages.py`

**Interfaces:**
- Consumes: `WikiClient.items_for(site, titles)`, `WikiClient.item_sitelinks(item)`, a title search on each wiki (`WikiClient` `list=search`, add `search_titles(text) -> list[str]` to `sync/wiki.py` if no equivalent exists).
- Produces: `covered(row, repo: WikiClient, wikis: dict[str, WikiClient]) -> tuple[str, dict]`: `("drop", {"have": [...]})` when the local item has SV sitelinks on every one of the six wikis; `("translation", {"have": [...], "missing": [...]})` when on some; `("pass", {})` when on none and no exact-title page exists on any wiki. The local item is found by trying the row's Wikipedia sitelinks in the order en, it, es, fr, de, pt (`items_for("enwiki", [title])` …).
- Produces: `run_stage(queue, stage: int, fn, limit=None) -> dict[str, int]` (outcome counts), the loop every stage uses: for each pending row, `fn(row) -> (outcome, data)`, then `set_outcome`; an exception stops the loop, leaving that row without an outcome, and is re-raised.

- [ ] **Step 1: Write the failing tests:** `test_covered_on_all_six_drops`, `test_covered_on_some_is_a_translation`, `test_found_through_itwiki_when_no_enwiki` (Review Focus), `test_exact_title_page_without_item_counts_as_covered`, `test_run_stage_stops_on_exception_and_keeps_row_pending`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_stages.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `covered` and `run_stage`; calibration rows are set `pass` without lookups.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): stage 1, skip subjects SeminaVerbi already covers`.

### Task 5: The new Jev questions, cached (stage 3 and the parts of stage 5)

**Files:**
- Create: `pipeline/judge.py`
- Test: `tests/test_pipeline_judge.py` (reuse `FakeJev` and `choice` from `tests/test_jev.py` by import)

**Interfaces:**
- Consumes: `tools.jev.MODEL`, `tools.jev.confident`, `tools.jev.classify`, `tools.jev.themes`, `Queue.jev_get/jev_put`.
- Produces: `qhash(name: str, instructions: str, state_text: str) -> str` (sha256 hex of the three, joined by `\0`).
- Produces: `Judge(client, queue)` with
  `plausible(row, synopsis: str) -> float` (Noul: does the work plausibly show the Gospel's influence or a seed of the Word?),
  `connection(row, dossier_text: str) -> dict` (`{"choice": key, "probabilities": {...}, "confidence": c}`, Choice keys exactly `direct`, `seeds`, `entertainment`, `none`, with the four descriptions of the spec),
  `field(row, dossier_text) -> dict` and `themes(row, dossier_text) -> dict` (wrapping `tools.jev.classify` / `themes`, result as JSON-safe dicts),
  `evidence(row, theme_key: str, paragraphs: list[str]) -> int` (Choice over paragraph indexes `"p0"…`, returns the index).
  Every method looks up `qhash` first and returns the cached answer without calling the client.

- [ ] **Step 1: Write the failing tests:** `test_cached_answer_is_not_asked_again` (two calls, one client call), `test_changed_wording_is_a_new_hash`, `test_connection_choice_has_the_four_keys`, `test_evidence_returns_the_chosen_paragraph_index`, `test_typesafe_error_leaves_no_cache_entry` (Review Focus; `FakeJev` raising `TypeSafeError`).
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_judge.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `pipeline/judge.py`. Stage 3's stage function in `stages.py`: `quick(row, judge, wikipedia_for) -> (outcome, data)`: synopsis = the `lead` in the first language of en, it, es, fr, de, pt the row has a sitelink for; `drop` below `quick_drop`, except calibration rows (always `pass`, probability recorded).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): Jev questions for the pipeline, cached by question hash`.

### Task 6: The film dossier (stage 4)

**Files:**
- Modify: `pipeline/domains/film.py`
- Test: `tests/test_pipeline_film.py`

**Interfaces:**
- Consumes: `Wikipedia(lang).sections/url`, Wikiquote through `Wikipedia` pointed at `{lang}.wikiquote.org` (constructor parameter `project="wikipedia"`).
- Produces: `dossier(row, cfg, wiki_for) -> dict`: `{"notes": [{"lang", "url", "heading", "text"}...], "languages": {lang: count}}`. Languages: the work's original language (P364, mapped to a code among the six, else skipped) then en, then it, at most `dossier_languages`. Sections kept when their heading matches, case-insensitively, a per-language list in `film.py` (plot, synopsis, themes, analysis, reception, religious, trama, temi, critica, argumento, temas, intrigue, thèmes, Handlung, Themen, enredo, temas…). For a television series, one note per season section when present. Wikiquote: the work's page, quotations attributed to its creators only.

- [ ] **Step 1: Write the failing tests:** `test_dossier_keeps_plot_and_reception_sections_only`, `test_dossier_languages_start_with_original_language`, `test_series_gets_one_note_per_season`, `test_every_note_keeps_its_url`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_film.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement; the stage function stores the dossier as stage-4 data.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): the film and television dossier`.

### Task 7: Adjudication (stage 5)

**Files:**
- Modify: `pipeline/stages.py`
- Test: `tests/test_pipeline_stages.py`

**Interfaces:**
- Consumes: `Judge`, the stage-4 dossier, `sync.categories.load`.
- Produces: `adjudicate(row, judge, data) -> (outcome, result)` with `result = {"connection": {...}, "field": {...}, "themes": [{"key", "p", "status", "evidence": {"url", "excerpt"}}], "outline": [str], "first_wiki": lang}`. Themes kept: `status` `applied` or `author` (≥ 0.40); for each, `judge.evidence` over the dossier's notes split into paragraphs; `excerpt` = the chosen paragraph's first 25 words plus `…` when cut. Outline = the article standard's sections in order (`Synopsis`, the Gospel section, `Discernment`, `References`) with one `Theme: <en name>` entry per kept theme inserted after the Gospel section. `first_wiki` = the language with most notes, ties broken en, it, es, fr, de, pt. `connection.choice == "none"` → `drop` (calibration rows: `pass` with the result kept).

- [ ] **Step 1: Write the failing tests:** `test_none_connection_drops`, `test_themes_below_040_are_never_kept`, `test_excerpt_is_at_most_25_words`, `test_outline_has_one_entry_per_kept_theme`, `test_first_wiki_is_the_richest_language`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_stages.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): stage 5, connection, field, themes and evidence`.

### Task 8: `{{SV proposal}}`

**Files:**
- Create: `templates/SV proposal.wikitext` (`<includeonly>{{#invoke:SV/Proposal|main}}</includeonly>` plus the noinclude doc, as the other templates), `modules/SV/Proposal.lua`
- Modify: `config/templates.json` (entry `"SV proposal"` with its params), `i18n/*.json` (keys below, six languages), the generated skill docs (`python -m tools.skilldocs`)
- Test: `tests/test_proposal.py` (through `LuaEnv`)

**Interfaces:**
- Parameters (exactly): `qid`, `label_en` … `label_pt`, `domain`, `connection`, `connection_p`, `field`, `field_p`, `theme1` … `themeN` (theme keys), `theme1_p`, `theme1_status`, `theme1_evidence`, `theme1_source`, `outline` (sections separated by ` ; `), `sources` (URLs separated by spaces), `first_wiki`, `status` (`open` default, `approved`, `created`, `declined`), `reason`, `article`.
- Renders: a box (`sv-proposal`, `notheme`) with the label in the page language (fallback en), a Wikidata link, the status, the connection type's localised name and probability, the field and theme names from `Module:SV/Data` in the page language, each theme's probability, excerpt and source link, the outline, the sources and the first wiki; then `<vote />` (through `frame:extensionTag('vote', '')`) only when `status` is `open`; for `declined`, the reason instead of the button; for `created`, a link to `article` on `first_wiki` (interwiki prefix of that wiki). Category `SV proposals (<status>)` in the en i18n string `proposal_category`.
- i18n keys: `proposal_title`, `proposal_status_open|approved|created|declined`, `proposal_connection_direct|seeds|entertainment|none`, `proposal_field`, `proposal_themes`, `proposal_outline`, `proposal_sources`, `proposal_first_wiki`, `proposal_reason`, `proposal_category`.

- [ ] **Step 1: Write the failing tests:** `test_open_proposal_has_the_vote_button`, `test_declined_shows_reason_and_no_button`, `test_theme_names_come_from_the_data_in_page_language`, `test_label_falls_back_to_en`, `test_unknown_status_is_treated_as_open`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_proposal.py tests/test_skilldocs.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; regenerate skill docs; add the template's styles inline in the template's TemplateStyles sheet only if needed (no `var()`, night mode per the README's category-tree section).
- [ ] **Step 4:** Run `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat(templates): SV proposal`.

### Task 9: Proposal pages and the ranking page (stage 6)

**Files:**
- Create: `pipeline/publish.py`
- Test: `tests/test_pipeline_publish.py`

**Interfaces:**
- Consumes: stage-5 data, `Queue.set_status/by_status`, `WikiClient.wikitext/edit/parse` on en.
- Produces: `page_title(row) -> str`: `Semina Verbi:Article proposals/` + the en sitelink title, else the label in `first_wiki`, else the en label; characters `#<>[]|{}` removed, a leading `:` removed; stored in the queue on first use and reused afterwards (stable across runs).
- Produces: `proposal_call(row, result) -> str` (the `{{SV proposal|…}}` call, one parameter per line, values with `|` or `}}` escaped as `{{!}}` / `&#125;&#125;`).
- Produces: `replace_call(old_text: str | None, call: str) -> str`: a new page is `call + "\n"`; otherwise only the first top-level `{{SV proposal …}}` is replaced (balanced-brace scan), everything else kept byte for byte; if the old call has `status=declined`, `status` and `reason` are carried over unchanged.
- Produces: `publish(queue, en: WikiClient, cfg) -> int` (pages written; skips the edit when the text is unchanged; summary `Article proposal (pipeline)`) and `ranking(queue) -> str` (sections: per domain the open proposals by votes desc, then Approved, Created, Declined, Translation suggestions), written to `Semina Verbi:Article proposals`.

- [ ] **Step 1: Write the failing tests:** `test_text_below_the_box_survives_republish` (Review Focus), `test_declined_status_is_carried_over`, `test_title_is_sanitised_and_stable` (Review Focus: `"Star Wars: Episode I – The Phantom Menace"` keeps its colon inside, `"A|B"` → `"AB"`, second call returns the stored title), `test_title_uses_first_wiki_label_without_enwiki`, `test_unchanged_page_is_not_edited`, `test_ranking_orders_by_votes`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_publish.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): stage 6, proposal pages and the ranking page`.

### Task 10: Votes, declines and the approved list

**Files:**
- Modify: `pipeline/publish.py`
- Test: `tests/test_pipeline_publish.py`

**Interfaces:**
- Consumes: `WikiClient.parse`, `replace_call`, `ranking`.
- Produces: `count_votes(en, titles: list[str]) -> dict[str, int]`: one `action=parse` of a text with one `{{NUMBEROFVOTESPAGE:<title>}}` per line, wrapped in markers, so many pages cost one call (batches of 50).
- Produces: `sync_votes(queue, en, cfg) -> dict` (`{"approved": [...], "declined": [...]}`): read each open proposal page; a `status=declined` set by hand → `queue.set_status(qid, "declined", reason=…)`; else count ≥ `vote_threshold` → set `status=approved` in the call (through `replace_call`) and in the queue; store each count; then regenerate the ranking page.
- Produces: `approved_report(queue) -> str` (one block per approved row: title, QID, first wiki, connection, field, themes with evidence, outline, sources).

- [ ] **Step 1: Write the failing tests:** `test_threshold_promotes_to_approved`, `test_below_threshold_stays_open`, `test_hand_declined_is_read_back_and_never_reproposed` (then `publish` skips that QID), `test_votes_are_counted_in_one_parse_per_50_pages`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_publish.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(pipeline): the vote loop and declined proposals`.

### Task 11: The CLI, the calibration report, the timer, the docs

**Files:**
- Create: `pipeline/__main__.py`, `pipeline/calibration.py`, `deploy/systemd/sv-pipeline-votes.service`, `deploy/systemd/sv-pipeline-votes.timer`
- Modify: `README.md` (section "The article pipeline"), `docs/content-tasks.md` only if Arts & Faith was dropped
- Test: `tests/test_pipeline_cli.py`

**Interfaces:**
- Commands: `python -m pipeline candidates --domain film`, `covered`, `triage`, `quick`, `dossier`, `adjudicate`, `publish`, `run` (stages 0–5 in order, capped by `max_new_per_run` entering stage 3, then `publish` only with `--publish`), `calibrate` (stages 3–5 on calibration rows, then the report), `votes`, `approved`, `redo STAGE [--qid Q]`. Common options: `--db PATH` (default `data/pipeline.sqlite`), `--domain`.
- `calibration.report(queue, wikis) -> str`: a Markdown table per calibration article: title, Jev connection (with p), Jev field vs. the item's `SV field`, Jev themes ≥ 0.40 vs. the item's `SV Gospel theme` statements, and the unmapped-pages list from Task 3.
- The timer: every 3 hours, `ExecStart=%h/development/seminaverbi-templates/.venv/bin/python %h/.config/seminaverbi/runeditor.py pipeline votes`, `WorkingDirectory` the repository; the README says how to install it (`systemctl --user link …`, `enable --now`).

- [ ] **Step 1: Write the failing tests:** `test_run_caps_new_rows_entering_stage_3`, `test_run_does_not_publish_without_flag`, `test_calibrate_reports_field_disagreement`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pipeline_cli.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; write the README section (stages, commands, the queue file, the timer, licences: Wikidata CC0, Wikipedia read as notes, no IMDb/TMDb).
- [ ] **Step 4:** Run `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat(pipeline): the command line, calibration report and vote timer`.

---

## After the branch merges (operations, with the author)

1. Sync the templates (`runsync.py --wiki all`) so `{{SV proposal}}` exists.
2. `python -m pipeline candidates --domain film`, then `calibrate`; show the author the report before anything is posted. Wording or cut-off changes go through a PR, then `redo`.
3. `run --publish` on the backlog in batches of `max_new_per_run`; install the timer.
4. Portal pages on each wiki (editor session), `voter` to the first approved users (the author), tick the boxes on #20 and #21.
