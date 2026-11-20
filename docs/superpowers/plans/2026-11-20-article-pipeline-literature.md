# Article pipeline, literature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `literature` domain for the article pipeline (works only, a series as one page), sharing the generic parts of the film domain.

**Architecture:** The generic half of `pipeline/domains/film.py` moves to `pipeline/domains/common.py`; film keeps its sources, headings and season rule and behaves exactly as before. `pipeline/domains/literature.py` adds seed authors, *Illustrissimi*, the Francis 2024 letter and facets as sources, folds volumes into their series at stage 0, and reads series and volume pages in the dossier. Jev's quick and connection wording comes from the row's domain.

**Tech Stack:** Python 3, SQLite queue, Wikidata SPARQL and API, MediaWiki API, TypeSafe Jev (`typesafe_sdk`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-article-pipeline-literature-design.md` (builds on `docs/superpowers/specs/2026-11-20-article-pipeline-film-design.md`).

## Global Constraints

- Film behaviour unchanged: every existing test passes without edits to its assertions; film's Jev request wording is byte-identical (its cached answers stay valid).
- Works only; a series is one candidate and one proposal; publisher or imprint series never fold.
- Floor `literature.sitelink_floor` = 15 applies to works reached through an author, to facet-only rows and to series; only works a source names directly (sources `illustrissimi`, `francis`) skip it.
- No full texts in the dossier; a Wikisource page is only a proposal source link.
- Wikidata (CC0) and Wikipedia/Wikiquote read as notes; no Open Library, IMDb or TMDb.
- Stages 0–5 only read; nothing in this plan writes to a wiki. `TYPESAFE_API_KEY` is never printed.
- At most `max_new_per_run` (200) rows enter stage 3 per run, per domain, as now.
- Test doubles follow `tests/test_pipeline_film.py` (fake SPARQL, Wikidata, Wikipedia); no network in tests.

## Review Focus

1. A seed author with hundreds of works (Dickens, Goethe): the floor keeps only the notable ones; a test with 3 works of 2, 15 and 40 sitelinks keeps 2 (Task 3).
2. A volume whose P179 is a publisher series (no shared author) stays its own candidate and never folds (Task 5).
3. A series item with no Wikipedia sitelink of its own still gets a quick check and a dossier, through its best volume (Task 5, Task 7).
4. An *Illustrissimi* link that is a disambiguation page, a redirect or a person with no works is printed in the run log, never added (Task 4).
5. A work whose original language is not one of the six (Russian, Japanese) still gets a dossier from the six wikis it has (Task 7).

## File Structure

- Create `pipeline/domains/common.py`: SPARQL building (`query`, `bounded`, `values`), `row_data`, `run_query`, `qids`, `entity_sitelinks`, `sections`, `heading`, `wikidata_qid`, `field_keys`, `calibration_qids`, `facet_patterns`, `domain_config`.
- Modify `pipeline/domains/film.py`: import from `common`; keep the old private names as aliases (tests use them); add `QUESTIONS`.
- Create `pipeline/domains/literature.py`: the literature domain.
- Modify `pipeline/domains/__init__.py`: register `literature`.
- Modify `pipeline/judge.py`: wording from the row's domain.
- Modify `pipeline/stages.py`: `quick` uses a borrowed lead when a row has no Wikipedia sitelink.
- Modify `pipeline/__main__.py`: per-domain config; optional domain `covered`.
- Modify `pipeline/sources.py`: `Wikipedia.section_links`.
- Modify `pipeline/publish.py`: dossier `links` in the sources; translation notes on the ranking page.
- Modify `config/pipeline.json`: the `literature` block.
- Modify `README.md`: the literature domain.
- Tests: `tests/test_pipeline_common.py`, `tests/test_pipeline_literature.py`, plus additions to `tests/test_pipeline_judge.py`, `tests/test_pipeline_stages.py`, `tests/test_pipeline_sources.py`, `tests/test_pipeline_publish.py`, `tests/test_pipeline_cli.py`.

---

### Task 1: Move the generic code into `common.py`

**Files:**
- Create: `pipeline/domains/common.py`
- Modify: `pipeline/domains/film.py`
- Test: `tests/test_pipeline_common.py`

**Interfaces:**
- Produces (all in `pipeline/domains/common.py`):
  - `LANGS`, `LIMIT = 500`, `CHUNK = 50`, `NON_WIKIPEDIA`
  - `query(pattern: str) -> str`, `bounded(pattern: str, cls: str) -> str`, `values(qids) -> str`
  - `row_data(row: dict) -> tuple[dict, dict, dict]` (labels, sitelinks, `{"count", "instance_of"}`)
  - `run_query(sparql, domain: str, label: str, query: str) -> list[dict]` (prints `"<domain> candidates: <label> failed: …"` and re-raises; warns at LIMIT rows, as film's `_run`)
  - `qids(entity: dict, prop: str) -> list[str]`, `entity_sitelinks(entity: dict) -> dict[str, str]`
  - `heading(raw: str) -> str`, `sections(wiki, title: str) -> list[tuple[str, str]]` (missing page → `[]`)
  - `wikidata_qid(repo, wikidata, lang: str, title: str) -> str | None`
  - `field_keys(tree: dict, roots: set[str]) -> set[str]` (the roots and all their descendants)
  - `calibration_qids(wikis: dict, wikidata, tree: dict, roots: set[str]) -> tuple[list[str], list[str]]`
  - `facet_patterns(dcfg: dict) -> list[tuple[str, str]]` (film's five facets, reading `subjects`, `book_class`, `biblical_classes`, `genres` from the domain block `dcfg`)

- [ ] **Step 1: Write the failing tests**

```python
from pipeline.domains import common, film

def test_film_keeps_its_old_names():
    assert film._query is common.query and film._bounded is common.bounded
    assert film._wikidata_qid is common.wikidata_qid and film._qids is common.qids

def test_field_keys_collects_descendants():
    tree = {"fields": [{"key": "arts", "children": [
        {"key": "literature", "children": [{"key": "novels"}, {"key": "poetry"}]},
        {"key": "film", "children": [{"key": "feature-films"}]}]}], "themes": []}
    assert common.field_keys(tree, {"literature"}) == {"literature", "novels", "poetry"}

def test_run_query_names_the_domain(capsys):
    class Boom:
        def select(self, q): raise RuntimeError("x")
    import pytest
    with pytest.raises(RuntimeError):
        common.run_query(Boom(), "literature", "facet", "Q")
    assert "literature candidates: facet failed" in capsys.readouterr().out
```

(Adapt the tree literal to the shape `sync.categories.nodes` reads, as `tests/test_pipeline_film.py::test_calibration_qids_map_through_the_local_item_and_report_unmapped` builds it.)

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest -q tests/test_pipeline_common.py`
Expected: FAIL, `ModuleNotFoundError: pipeline.domains.common`

- [ ] **Step 3: Move the code**

Move the functions listed above out of `film.py` unchanged in behaviour; `film.py` imports them and keeps `_query = common.query`, `_bounded`, `_values`, `_title`, `_row_data`, `_qids`, `_sitelinks`, `_sections`, `_heading`, `_wikidata_qid`, `_facet_patterns = lambda cfg: common.facet_patterns(cfg["film"])` and `_run = lambda sparql, label, query: common.run_query(sparql, "film", label, query)`. `film.calibration_qids(wikis, wikidata=None, tree=None)` keeps its signature and calls `common.calibration_qids(..., roots={"film", "television"})`.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (907 before this task, plus the new ones)

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/common.py pipeline/domains/film.py tests/test_pipeline_common.py
git commit -m "Pipeline: move the generic domain code into domains/common.py"
```

### Task 2: Jev wording per domain

**Files:**
- Modify: `pipeline/judge.py`, `pipeline/domains/film.py`
- Test: `tests/test_pipeline_judge.py`

**Interfaces:**
- Produces: each domain module has `QUESTIONS = {"plausible": str, "connection": str, "connections": dict[str, str]}`; `film.QUESTIONS` holds today's `PLAUSIBLE`, `CONNECTION`, `CONNECTIONS` verbatim. `judge.PLAUSIBLE`, `judge.CONNECTION`, `judge.CONNECTIONS` stay importable (equal to film's). `Judge.plausible` and `Judge.connection` use `DOMAINS[row.domain].QUESTIONS`, falling back to film's for an unknown domain.

- [ ] **Step 1: Pin film's wording, then write the failing tests**

Before editing, record `qhash("system_one", question_text(jev.MODEL, {"connection": <today's Choice>}), "{}")` and the same for `plausible` as literals in the test.

```python
def test_film_wording_is_unchanged():
    assert _connection_hash(domain="film") == FILM_CONNECTION_HASH  # literal recorded above
    assert _plausible_hash(domain="film") == FILM_PLAUSIBLE_HASH

def test_a_row_is_asked_with_its_domain_wording(q):
    fake = FakeJev(...)  # records the instructions it receives
    judge = Judge(fake, q, DATA)
    judge.connection(row_of("literature"), "text")
    assert fake.instructions[-1] == DOMAINS["literature"].QUESTIONS["connection"]
```

The second test needs a stand-in literature module until Task 3: monkeypatch `DOMAINS["literature"]` with a `SimpleNamespace(QUESTIONS=...)`.

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest -q tests/test_pipeline_judge.py`
Expected: the domain test FAILS (film wording used for every row); the hash test passes before and after.

- [ ] **Step 3: Implement** the lookup in `judge.py` (import `DOMAINS` inside the method or at module level; `pipeline.domains.film` must not import `judge`).

- [ ] **Step 4: Run the whole suite** — `.venv/bin/pytest -q`, all pass.

- [ ] **Step 5: Commit** — `git commit -m "Pipeline: Jev's quick and connection wording come from the row's domain"`

### Task 3: The literature module: config, triage, calibration

**Files:**
- Create: `pipeline/domains/literature.py`
- Modify: `pipeline/domains/__init__.py`, `pipeline/domains/common.py`, `pipeline/__main__.py`, `config/pipeline.json`
- Test: `tests/test_pipeline_literature.py`, `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: Task 1's `common`, Task 2's `QUESTIONS` convention.
- Produces:
  - `common.domain_config(cfg: dict, name: str) -> dict`: `cfg` with every key of `cfg[name]` that also exists at the top level overriding it (`sitelink_floor`), without changing `cfg`; `__main__` passes the merged config to every command of that domain (the domain block stays in it, so `candidates` still reads `cfg["literature"]`).
  - `literature.WORK = "Q7725634"`, `literature.EDITION = "Q3331189"`, `literature.DIRECT = {"illustrissimi", "francis"}`
  - `literature.QUESTIONS` (wording below)
  - `literature.triage(row, cfg) -> tuple[str, str]`
  - `literature.calibration_qids(wikis, wikidata=None, tree=None)` (roots `{"literature"}`)
  - `literature.add_calibration(queue, sparql, qids) -> int` (only items that are works or series; prints `literature calibration: <QID> skipped (not a work)` for the rest)
  - Stage-0 data keys on literature rows: `count`, `instance_of`, `series` (bool), `volumes` (`{volume_qid: {"sitelinks": {...}, "count": int}}`), `lead_sitelinks`, `folded_into` (series QID).

`config/pipeline.json` gets `"literature": {"sitelink_floor": 15, "authors": [...], "francis_2024": [], "subjects": [...], "book_class": ..., "biblical_classes": [...], "genres": [...], "series_classes": [...], "illustrissimi_page": "Illustrissimi", "illustrissimi_section": "I personaggi di Illustrissimi"}`. Authors: the spec's list (Dante, Manzoni, Chesterton, Tolkien, C. S. Lewis, Waugh, Greene, Flannery O'Connor, Bernanos, Undset, Endō, Péguy, Claudel, Mauriac, Dostoevsky), each QID checked on Wikidata, as `{"qid": "Q…", "name": "…"}`. `subjects`, `book_class` and `biblical_classes` as film's; `genres` and `series_classes` checked on Wikidata (Christian literature, Christian allegory, hagiography; novel series Q1667921 and the book-series class Wikidata uses for one author's series). `francis_2024` is filled in Task 4.

Literature wording (calibration tests it):

```python
QUESTIONS = {
    "plausible": ("Does the literary work described in `text` plausibly show the influence of the Gospel, or a seed "
                  "of the Word (a moral or spiritual truth that the Gospel brings to fulfilment)? The work need not "
                  "be religious: an implicit seed in one storyline, character, poem or volume is enough."),
    "connection": ("`text` is a dossier on one literary work (for a series, possibly several volumes). How does the "
                   "work relate to the Gospel? A work need not be religious to carry seeds of the Word: a moral or "
                   "spiritual truth that the Gospel brings to fulfilment (mercy, forgiveness, sacrifice, conversion, "
                   "hope...) is enough, even when it is implicit and found in a single storyline, character, poem or "
                   "volume. Judge the text itself, as its scenes show it, not the work's reputation."),
    "connections": {
        "direct": "direct Christian or biblical influence",
        "seeds": "implicit seeds of the Word: a truth the Gospel fulfils, in the whole work or in one storyline, "
                 "character, poem or volume",
        "entertainment": "biblical or Christian imagery used for entertainment",
        "none": "no connection, not even an implicit one",
    },
}
```

- [ ] **Step 1: Write the failing tests**

```python
def test_registry_has_literature():
    assert DOMAINS["literature"] is literature

def test_editions_are_dropped():
    assert literature.triage(lrow(["author"], 40, ["Q3331189"]), CFG)[0] == "drop"

def test_floor_applies_to_author_works_and_facets():
    assert literature.triage(lrow(["author"], 2), CFG) == ("drop", "2 sitelinks, under the floor of 15")
    assert literature.triage(lrow(["facet"], 14), CFG)[0] == "drop"
    assert literature.triage(lrow(["illustrissimi:author"], 40), CFG)[0] == "pass"

def test_works_a_source_names_directly_skip_the_floor():
    assert literature.triage(lrow(["illustrissimi", "facet"], 1), CFG)[0] == "pass"
    assert literature.triage(lrow(["francis"], 0), CFG)[0] == "pass"

def test_a_folded_volume_is_dropped():
    assert literature.triage(lrow(["author"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")

def test_domain_config_overrides_only_shared_keys():
    cfg = {"sitelink_floor": 15, "literature": {"sitelink_floor": 8, "authors": []}}
    merged = common.domain_config(cfg, "literature")
    assert merged["sitelink_floor"] == 8 and "authors" not in merged
    assert merged["literature"]["authors"] == [] and cfg["sitelink_floor"] == 15

def test_calibration_skips_people(q, capsys):
    # fake SPARQL returns only the work for [Q_work, Q_person]
    assert literature.add_calibration(q, FakeSparql([item("Q1", types="Q7725634")]), ["Q1", "Q5"]) == 1
    assert "Q5 skipped (not a work)" in capsys.readouterr().out
```

(`lrow(sources, count, instance_of=("Q7725634",), folded_into=None)` builds a `Row` like `row` in `tests/test_pipeline_film.py`; `CFG = {"sitelink_floor": 15}` after `domain_config`.) In `tests/test_pipeline_cli.py`, add: `--domain literature` is accepted, and stage 2 for a literature row is called with `sitelink_floor` from the `literature` block.

- [ ] **Step 2: Run to see them fail** — `.venv/bin/pytest -q tests/test_pipeline_literature.py tests/test_pipeline_cli.py`, FAIL on the missing module.

- [ ] **Step 3: Implement** `literature.py` (triage, calibration, QUESTIONS, stub `candidates`, `dossier`, `dossier_stage` raising `NotImplementedError` until Tasks 4–7), the registry entry, `common.domain_config`, its use in `__main__._stage_functions`, and the config block.

- [ ] **Step 4: Run the whole suite** — all pass.

- [ ] **Step 5: Commit** — `git commit -m "Pipeline: literature domain: config, triage and calibration"`

### Task 4: Literature candidates: authors, *Illustrissimi*, Francis 2024, facets

**Files:**
- Modify: `pipeline/domains/literature.py`, `pipeline/sources.py`, `config/pipeline.json`
- Test: `tests/test_pipeline_literature.py`, `tests/test_pipeline_sources.py`

**Interfaces:**
- Produces:
  - `Wikipedia.section_links(title: str, heading: str) -> list[str]`: main-namespace link titles of the first section whose heading equals `heading` (redirects followed, page order); `[]` when there is no such section.
  - `literature.candidates(queue, sparql, wikipedia, cfg, wikidata=None) -> int`. `wikipedia` is an en reader (as film); the *Illustrissimi* list is read with an it reader the function makes itself (`pipeline.sources.Wikipedia("it")`) unless `cfg` passes `_it_wikipedia` (tests).
  - Sources: `author` (works of `literature.authors`, by P50); `illustrissimi:author` / `francis:author` (works of an addressee or named author, by P50); `illustrissimi` / `francis` (a work named directly: the link is a work, or a fictional character's work by P1441); `facet`.
  - All queries bounded to works and series: for each class in `[WORK] + series_classes`, `common.bounded(pattern, cls)`; series hits get `data["series"] = True`.

- [ ] **Step 0 (research, before code): the Francis list.** Read the *Letter of the Holy Father Francis on the role of literature in formation* (17 July 2024) on vatican.va. Record in `config/pipeline.json` `literature.francis_2024` each author and each work it names as `{"qid": "Q…", "name": "…", "kind": "author" | "work"}`, the QID checked on Wikidata; leave out a name with no clear item and list it in the commit message.

- [ ] **Step 1: Write the failing tests**

```python
def test_section_links_reads_one_section(): ...  # fake API: section 1 "I personaggi di Illustrissimi" -> its links only

def test_illustrissimi_addressees_lead_to_works(q, capsys):
    # it page links: Charles Dickens (Q5, author of Q_A 40 sitelinks), Pinocchio (character, P1441 -> Q_P),
    # Guglielmo Marconi (Q5, no works), Davide (disambiguation: no Wikidata item resolved)
    literature.candidates(q, sparql, en, CFG_FULL, wikidata)
    assert q.row("Q_A").sources == ["illustrissimi:author"]
    assert q.row("Q_P").sources == ["illustrissimi"]
    out = capsys.readouterr().out
    assert "illustrissimi: no literary work for 'Guglielmo Marconi'" in out
    assert "illustrissimi: no Wikidata item for 'Davide'" in out

def test_seed_author_works_are_tagged_author(q): ...
def test_francis_entries_by_kind(q): ...  # an author -> francis:author, a work -> francis
def test_facets_run_for_works_and_series(q): ...  # every facet query is bounded to WORK and to each series class
```

The fake SPARQL answers by matching a marker in the query (as `FakeSparql` with `marker` in `tests/test_pipeline_film.py`).

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement.** One SPARQL per (source, class) for P50 works of a VALUES list of authors, and one for "the item itself or its P1441 work" of a VALUES list of direct entries; an *Illustrissimi* addressee is reported when neither query returns a row for it (`illustrissimi: no literary work for '<title>'`).

- [ ] **Step 4: Run the whole suite** — all pass.

- [ ] **Step 5: Commit** — `git commit -m "Pipeline: literature candidates from seed authors, Illustrissimi, the Francis letter and facets"`

### Task 5: Fold volumes into their series

**Files:**
- Modify: `pipeline/domains/literature.py`, `pipeline/stages.py`
- Test: `tests/test_pipeline_literature.py`, `tests/test_pipeline_stages.py`

**Interfaces:**
- Consumes: Task 4's candidate rows (before insertion).
- Produces:
  - `literature.series_of(sparql, qids: list[str], series_classes: list[str]) -> dict[str, list[str]]`: volume QID → series QIDs whose P31 is (a subclass of) a series class and that share an author (P50) with the volume.
  - `literature.fold(queue, sparql, rows: list[dict], source: str, cfg) -> int`: replaces `_take` for literature; adds the series row (labels, sitelinks, count from `common.query` on the series QID) with `volumes` extended and `count = max(own, volumes')`; when the series has no Wikipedia sitelink, `lead_sitelinks` = the sitelinks of its volume with the highest count; a volume with zero or several series is added as itself and printed (`literature candidates: <QID> in 2 series, kept on its own`); a volume already in the queue gets `folded_into`; a series whose status is approved, created or declined gets nothing added.
  - `stages.quick`: when `row.sitelinks` has no Wikipedia sitelink, use `row.data[0]["lead_sitelinks"]` if present.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_volume_becomes_its_series(q):
    # Q_V (volume, 30 sitelinks) -> series Q_S (same author, 12 sitelinks)
    fold(...)
    assert q.row("Q_V") is None
    s = q.row("Q_S")
    assert s.data[0]["volumes"]["Q_V"]["count"] == 30 and s.data[0]["count"] == 30

def test_a_later_volume_extends_the_series(q): ...  # second fold adds Q_V2 next to Q_V

def test_a_publisher_series_does_not_fold(q):
    # series_of returns nothing when the series shares no author: the volume is added as itself
    ...

def test_a_volume_in_two_series_stays_itself_and_is_reported(q, capsys): ...

def test_an_existing_volume_row_is_marked_folded(q):
    q.add("Q_V", "literature", "author", {"en": "V"}, {"enwiki": "V"})
    fold(...)
    assert q.row("Q_V").data[0]["folded_into"] == "Q_S"

def test_a_declined_series_absorbs_nothing(q): ...

def test_a_series_without_wikipedia_borrows_its_best_volume(q):
    assert q.row("Q_S").data[0]["lead_sitelinks"] == {"enwiki": "Best volume"}

# tests/test_pipeline_stages.py
def test_quick_reads_a_borrowed_lead():
    row = Row(..., sitelinks={}, data={0: {"lead_sitelinks": {"enwiki": "Best volume"}}})
    assert quick(row, judge_p(0.9), wikipedia_with({"Best volume": "lead"}))[0] == "pass"
```

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement** (call `fold` from every source in `candidates`).
- [ ] **Step 4: Run the whole suite** — all pass.
- [ ] **Step 5: Commit** — `git commit -m "Pipeline: fold literature volumes into their series"`

### Task 6: A series covered through a volume

**Files:**
- Modify: `pipeline/domains/literature.py`, `pipeline/__main__.py`, `pipeline/publish.py`
- Test: `tests/test_pipeline_literature.py`, `tests/test_pipeline_publish.py`

**Interfaces:**
- Produces:
  - optional domain function `covered(row, repo, wikis) -> tuple[str, dict]`; `__main__` stage 1 uses `getattr(domain, "covered", None)` before `stages.covered`.
  - `literature.covered`: `stages.covered` on the series; when that passes and a volume (a `Row`-like with the volume's sitelinks) is covered on some wiki, returns `("translation", {"have": [...], "missing": [...], "note": "covered through <volume title>"})`.
  - `publish.ranking`: a translation suggestion with `note` ends with ` (<note>)`.

- [ ] **Step 1: Write the failing tests** — `test_a_series_covered_through_a_volume_is_a_translation_suggestion`, `test_ranking_shows_the_translation_note` (asserts the line ends `(covered through The Hobbit)`).
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole suite** — all pass.
- [ ] **Step 5: Commit** — `git commit -m "Pipeline: a literature series covered through a volume is a translation suggestion"`

### Task 7: The literature dossier

**Files:**
- Modify: `pipeline/domains/literature.py`, `pipeline/publish.py`, `README.md`
- Test: `tests/test_pipeline_literature.py`, `tests/test_pipeline_publish.py`

**Interfaces:**
- Consumes: `common.sections`, `common.qids`, `common.entity_sitelinks`; stage-0 `volumes`, `lead_sitelinks`.
- Produces:
  - `literature.HEADINGS` per language (lower-case word starts): en `plot, synopsis, summary, content, structure, theme, analysis, interpretation, religio, christian, reception`; it `trama, sinossi, contenuto, struttura, tema, temi, analisi, interpretazione, religios, critica`; es `argumento, sinopsis, resumen, contenido, estructura, temas, análisis, interpretación, religios`; fr `intrigue, résumé, synopsis, contenu, structure, thème, analyse, interprétation, religi`; de `handlung, inhalt, aufbau, thema, themen, deutung, interpretation, analyse, religi`; pt `enredo, sinopse, resumo, conteúdo, estrutura, temas, análise, interpretação, religios`.
  - `literature.dossier(row, cfg, wiki_for, wikidata) -> dict` with film's shape `{"notes": [...], "languages": {...}}` plus `"links": [url]`: languages `[original (P407 among the six), en, it, es, fr, de, pt]` filtered to the row's Wikipedia sitelinks (or `lead_sitelinks`), at most `dossier_languages`; per language the lead and wanted sections of the work's page; for a series, each volume's page (from `volumes`, else P527) with its lead and wanted sections, headed `"<page> / <heading>"` (`"<page>"` for the lead); Wikiquote (en, it) sections naming an author (P50); `links` = the work's Wikisource page URLs for those languages (`<lang>wikisource` sitelinks).
  - `literature.dossier_stage(row, cfg, wiki_for, wikidata) -> tuple[str, dict]`: as film's (empty dossier dropped unless calibration).
  - `publish`: the proposal's `sources` also lists `row.data[4]["links"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_dossier_starts_with_the_original_language(): ...  # P407 Q652 (it) -> it notes first
def test_a_non_six_original_language_falls_back_to_the_six(): ...  # P407 Russian, sitelinks de, pt -> de, pt
def test_series_reads_each_volume_page(): ...  # headings "Vol 1 / Plot", "Vol 2" (lead)
def test_a_series_without_its_own_page_reads_its_volumes(): ...  # sitelinks {}, lead_sitelinks set
def test_wikiquote_keeps_sections_naming_the_author(): ...
def test_wikisource_is_a_link_not_a_note():
    d = literature.dossier(...)
    assert d["links"] == ["https://en.wikisource.org/wiki/The_Man_Who_Was_Thursday"]
    assert all("wikisource" not in n["url"] for n in d["notes"])

# tests/test_pipeline_publish.py
def test_dossier_links_are_proposal_sources(): ...
```

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement**, then add a "Literature" paragraph to `README.md` under "The article pipeline": the sources, series folding, the floor rule, `--domain literature`, and that `calibrate --domain literature` comes first.
- [ ] **Step 4: Run the whole suite** — all pass.
- [ ] **Step 5: Commit** — `git commit -m "Pipeline: literature dossier, with series volumes and Wikisource links"`

---

## After the plan (operations, not tasks)

`python -m pipeline calibrate --domain literature`, the report to the author before anything is published, then `run --domain literature` and the count shown to the author before `publish --domain literature`.
