# Music and art domains (works only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two new pipeline domains, `music` and `art`, that propose single works (sacred and secular) on the same queue, ranking page and vote loop as film and literature.

**Architecture:** Two thin modules, `pipeline/domains/music.py` and `pipeline/domains/art.py`, with the phase 2 domain interface, built on `common.py`. Three shared helpers move into or are added to `common.py`: the subclass closure behind `owns()` (the Catholic media source's only dependency), part-into-whole folding by P361, and the cross-domain skip. Art adds a benchmark set for calibration, released one QID at a time.

**Tech Stack:** Python 3.12, `requests`, SQLite (`pipeline/queue.py`), WDQS through `pipeline.sources.Sparql`, TypeSafe Jev through `pipeline.judge`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-music-art-domains-design.md` (with the phase 1 and 2 designs it builds on: `2026-11-20-article-pipeline-film-design.md`, `2026-11-20-article-pipeline-literature-design.md`).

## Global Constraints

- Run tests with the repository's virtualenv: `.venv/bin/python -m pytest -q` from the repository root (in a worktree under `.worktrees/`, use `../../.venv/bin/python`). The full suite must stay green after every task; film and literature tests pass **unchanged**.
- Wikidata only through `pipeline.sources.Sparql` (its User-Agent, one query at a time). No `wdt:P31/wdt:P279*` over the form or art classes inside a candidate query: subclasses come from the closure (Task 1) as a `VALUES` list. Every candidate query goes through `common.bounded` (`LIMIT` 500, most-linked first).
- Floors: `art.sitelink_floor` **15**; `music.sitelink_floor` **10** (sources `form`, `genre`, `facet`); `music.artist_sitelink_floor` **20** (rows whose only source is `artist`). Source `list` (art) passes without a floor. Calibration rows always pass triage.
- Unchanged from phases 1–2: `quick_drop` 0.25, `max_new_per_run` 200, theme kept at 0.50 (at most 8), direct connection passes at 0.80, evidence excerpt at most 25 words, the field asked from the root of the tree.
- The four connection keys stay `direct`, `seeds`, `entertainment`, `none`.
- Jev wording is stored verbatim in each module's `QUESTIONS` (cached answers are keyed by a hash of the text); film's and literature's wording does not change.
- People (P31 Q5), bands and musical groups are reported and skipped by every music and art source; calibration lists them as skipped.
- Out of scope: architecture, comics, people, MusicBrainz, CPDL, IMSLP, museum APIs, Europeana. Iconclass only through Wikidata's P1257.
- Only stage 6 writes to a wiki (as SV editor bot, project namespace on en). No task writes to the wiki, the server or `data/pipeline.sqlite`; tests use `tmp_path` queues.
- No lyric or liturgical text is copied into a dossier note from Wikidata; Wikipedia sections are read as notes like any other.
- Commit messages end with the two attribution lines given by the session (`Co-Authored-By: …`, `Claude-Session: …`). Stage files explicitly; never `git add -A`.

## Review Focus

1. An item that another domain already queued (a hymn text that literature holds as a poem) must be reported and skipped by music, never moved or duplicated. Test: Task 1 `test_other_domain_row_is_reported_and_skipped`.
2. A part with two wholes (a panel listed in two polyptychs, an aria in two works) must stay a candidate of its own with the ambiguity logged, not be folded into either at random. Test: Task 2 `test_part_with_two_wholes_stays_itself`.
3. An art benchmark row must never appear among translation suggestions, never be published before release, and a `release-benchmark` QID that is not a benchmark row must change nothing and say so. Tests: Task 8 `test_benchmark_rows_are_not_translation_suggestions`, `test_release_refuses_a_non_benchmark_qid`.
4. A missing, unreadable or corrupt closure cache must be refetched, not crash a run; a fetch failure must stop the run (a source that fails must not shrink it silently). Tests: Task 1 `test_corrupt_cache_is_refetched`, `test_closure_fetch_failure_raises`.
5. Art folding must stop at a building: a fresco cycle that is part of a chapel stays the candidate. Test: Task 7 `test_fold_stops_at_a_building`.

---

### Task 1: Shared helpers: class closure, `owns`, cross-domain skip

**Files:**
- Modify: `pipeline/domains/common.py`
- Modify: `pipeline/domains/literature.py` (its `_other_domain` becomes a call to the shared helper)
- Test: `tests/test_pipeline_common.py`

**Interfaces:**
- Produces:
  - `common.class_closure(sparql, classes: Iterable[str], cache: Path | None = None) -> set[str]`: the classes and all their subclasses (P279*), one SPARQL query per class, cached as JSON at `cache` (default `data/class_closure.json`, git-ignored like the queue) as `{class_qid: {"date": "YYYY-MM-DD", "subclasses": [...]}}`; an entry younger than 30 days is reused.
  - `common.owns(instance_of: Iterable[str], work_classes: Iterable[str], sparql, cache: Path | None = None) -> bool`: True when any QID of `instance_of` is in the closure of `work_classes`.
  - `common.other_domain(queue, qid: str, domain: str) -> str | None`: the domain of an existing row of `qid` when it is not `domain` (prints `"<domain> candidates: <qid> is already a <other> candidate, skipped"`), else None.

- [ ] **Step 1: Write the failing tests**

```python
def test_closure_is_fetched_once_and_cached(tmp_path):
    sparql = FakeSparql({"wd:Q3305213": [{"sub": "Q3305213"}, {"sub": "Q134194"}]})
    cache = tmp_path / "closure.json"
    assert common.class_closure(sparql, ["Q3305213"], cache) == {"Q3305213", "Q134194"}
    assert common.class_closure(sparql, ["Q3305213"], cache) == {"Q3305213", "Q134194"}
    assert len(sparql.queries) == 1

def test_corrupt_cache_is_refetched(tmp_path):
    cache = tmp_path / "closure.json"; cache.write_text("{not json")
    sparql = FakeSparql({"wd:Q7366": [{"sub": "Q7366"}]})
    assert common.class_closure(sparql, ["Q7366"], cache) == {"Q7366"}

def test_closure_fetch_failure_raises(tmp_path):
    with pytest.raises(RuntimeError):
        common.class_closure(FakeSparql(error=RuntimeError("429")), ["Q7366"], tmp_path / "c.json")

def test_owns_reads_the_closure(tmp_path):
    sparql = FakeSparql({"wd:Q3305213": [{"sub": "Q3305213"}, {"sub": "Q134194"}]})
    assert common.owns(["Q134194"], ["Q3305213"], sparql, tmp_path / "c.json")
    assert not common.owns(["Q5"], ["Q3305213"], sparql, tmp_path / "c.json")

def test_other_domain_row_is_reported_and_skipped(q, capsys):
    q.add("Q1", "literature", "facet", {}, {})
    assert common.other_domain(q, "Q1", "music") == "literature"
    assert "already a literature candidate" in capsys.readouterr().out
    assert common.other_domain(q, "Q1", "literature") is None
    assert common.other_domain(q, "Q9", "music") is None
```

(`FakeSparql` comes from `test_pipeline_film`; add a `q` fixture with `Queue(tmp_path / "q.db")` if the file has none.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_common.py -k "closure or owns or other_domain"`
Expected: FAIL (`AttributeError: module 'pipeline.domains.common' has no attribute 'class_closure'`).

- [ ] **Step 3: Implement the three helpers in `pipeline/domains/common.py`**

The closure query per class: `SELECT ?sub WHERE { ?sub wdt:P279* wd:<cls> . }`, run through `run_query(sparql, "closure", cls, …)` so a failure is logged and re-raised. A cache read error (missing, unreadable, invalid JSON, wrong shape) means "no entry". Replace `literature._other_domain` with `common.other_domain(queue, qid, "literature")` keeping literature's message text identical if its tests assert it (adapt the shared message to literature's existing wording instead of changing literature's tests).

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, literature tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/common.py pipeline/domains/literature.py tests/test_pipeline_common.py
git commit -m "Pipeline: shared class closure, owns() and the cross-domain skip"
```

---

### Task 2: Shared folding of parts into their whole (P361)

**Files:**
- Modify: `pipeline/domains/common.py`
- Test: `tests/test_pipeline_common.py`

**Interfaces:**
- Consumes: `common.class_closure`, `common.other_domain` (Task 1).
- Produces: `common.fold_parts(queue, sparql, rows: list[dict], source: str, domain: str, whole_classes: Iterable[str], stop_classes: Iterable[str] = ()) -> int`: adds the `common.query` rows to the queue under `domain`/`source`; a row whose item has exactly one P361 whole in the closure of `whole_classes` (and not in the closure of `stop_classes`) is replaced by that whole, which carries the source and a stage-0 `parts` entry `{part_qid: {"sitelinks": {...}, "count": int}}`; the whole's stage-0 `count` is the highest of its own and its parts'. A whole already `approved`, `created` or `declined` absorbs nothing (the part is not added either). A part already queued as its own row is marked `folded_into` (dropped by triage). Returns rows added.
- Produces: `common.covered_with_parts(row, repo, wikis) -> tuple[str, dict]`: `stages.covered`, then, for a non-calibration row it passes with nothing covered, a `translation` outcome with `note: "covered through <part title>"` when one of the row's stage-0 `parts` is covered on some wiki (literature's `covered` rule for volumes, generalized; literature keeps its own function).

- [ ] **Step 1: Write the failing tests**

```python
def test_part_folds_into_its_whole(q):
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}], "VALUES ?item { wd:Q1 }": [whole_row("Q1", 40)]})
    added = common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"])
    row = q.row("Q1")
    assert added == 1 and q.row("Q10") is None
    assert row.domain == "music" and "form" in row.sources
    assert set(row.data[0]["parts"]) == {"Q10"} and row.data[0]["count"] == 40

def test_part_with_two_wholes_stays_itself(q, capsys):
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}, {"item": "Q10", "whole": "Q2"}]})
    common.fold_parts(q, sparql, [part_row("Q10", 25)], "depicts", "art", ["Q15711026"])
    assert q.row("Q10") is not None and "2 wholes" in capsys.readouterr().out

def test_closed_whole_absorbs_nothing(q):
    q.add("Q1", "music", "form", {}, {}); q.set_status("Q1", "declined", reason="x")
    sparql = FakeSparql({"wdt:P361": [{"item": "Q10", "whole": "Q1"}]})
    assert common.fold_parts(q, sparql, [part_row("Q10", 25)], "form", "music", ["Q105543609"]) == 0
    assert q.row("Q10") is None
```

`part_row`/`whole_row` are small helpers in the test file returning `common.query`-shaped dicts (`item`, `n`, `types`, `l_en`, `s_en`). The closure is stubbed by passing a FakeSparql answer for the closure query, or by monkeypatching `common.class_closure` to return the class itself.

```python
def test_whole_is_a_translation_suggestion_when_a_part_is_covered():
    # FakeRepo/FakeWiki from test_pipeline_film: the part Q10 has a local item with an `it` sitelink
    outcome, data = common.covered_with_parts(row_with_parts({"Q10": {"sitelinks": {"itwiki": "Kyrie"}, "count": 5}}), repo, wikis)
    assert outcome == "translation" and data["have"] == ["it"] and data["note"].startswith("covered through")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_common.py -k "fold or covered"`
Expected: FAIL (`no attribute 'fold_parts'`).

- [ ] **Step 3: Implement `fold_parts` in `pipeline/domains/common.py`**

One query per chunk of `CHUNK` part QIDs: `VALUES ?item { … } ?item wdt:P361 ?whole . VALUES ?cls { <closure> } ?whole wdt:P31 ?cls .` (minus `stop_classes` closure); wholes' details through `common.query` with `VALUES ?item { … }`. Mirror literature's `fold`/`_fold_into` semantics (literature itself keeps its own series code: no refactor of literature here).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/common.py tests/test_pipeline_common.py
git commit -m "Pipeline: fold parts into their whole by P361, and the parts' coverage (shared by music and art)"
```

---

### Task 3: Music module: classes, `owns`, wording, triage, config, registry

**Files:**
- Create: `pipeline/domains/music.py`
- Modify: `pipeline/domains/__init__.py` (`DOMAINS["music"] = music`)
- Modify: `config/pipeline.json` (a `music` block)
- Test: `tests/test_pipeline_music.py`

**Interfaces:**
- Consumes: `common.owns`, `common.class_closure` (Task 1).
- Produces:
  - `music.WORK_CLASSES: tuple[str, ...] = ("Q105543609", "Q7366", "Q482994", "Q484692")` (musical work/composition, song, album, hymn; the builder appends the single-release class after checking it on Wikidata).
  - `music.owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool` = `common.owns(instance_of, WORK_CLASSES, sparql, cache)`. **This is the interface the Catholic media source consumes.**
  - `music.QUESTIONS` with keys `plausible`, `connection`, `connections` (wording below).
  - `music.triage(row, cfg: dict) -> tuple[str, str]`.
  - `music.covered = common.covered_with_parts` (Task 2), so stage 1 applies the parts rule (`__main__` already prefers a domain's own `covered`).
  - `music.FOLD_WHOLES = ("Q105543609",)` and `music.FOLD_STOP = ("Q482994",)`: a part folds only into a composition, never into an album (a song stays a song).
- Config block (exact keys; QIDs as measured in the spec, the builder adds the remaining ones after checking each on Wikidata, each with a `"name"` comment where the list holds objects):

```json
"music": {
  "sitelink_floor": 10,
  "artist_sitelink_floor": 20,
  "forms": ["Q217295", "Q6941842", "Q85477", "Q781815", "Q174873", "Q11499279", "Q188285", "Q484692",
            "Q856713", "Q200809", "Q210080", "Q2136369", "Q239759", "Q138775540", "Q240709", "Q503354", "Q123397869"],
  "genres": ["Q180268", "Q1379958", "Q6452410", "Q753520"],
  "subjects": ["Q302", "Q345", "Q1845", "Q5043"],
  "book_class": "Q29154430",
  "biblical_classes": ["Q12405827"],
  "artists": [{"qid": "…", "name": "Bob Dylan"}, "… the 12 calibration artists and 10 composers named in the spec, QIDs checked"]
}
```

- Wording (verbatim; the art module has its own):
  - `plausible`: "Does the musical work described in `text` plausibly show the influence of the Gospel, or a seed of the Word (a moral or spiritual truth that the Gospel brings to fulfilment)? The work need not be religious: an implicit seed in one song, verse or movement is enough."
  - `connection`: "`text` is a dossier on one musical work (a song, an album, or a composition with its movements). How does the work relate to the Gospel? A work need not be religious to carry seeds of the Word: a moral or spiritual truth that the Gospel brings to fulfilment (mercy, forgiveness, sacrifice, conversion, hope...) is enough, even when it is implicit and found in a single song, verse or movement. Judge the work, its words and its setting, not the artist's reputation."
  - `connections`: literature's four texts with "storyline, character, poem or volume" replaced by "song, verse or movement".

- [ ] **Step 1: Write the failing tests**

```python
def mrow(sources, count, instance_of=("Q7366",), calibration=False, **data):
    return Row("Q1", "music", sources, {}, {}, calibration, data={0: {"count": count, "instance_of": list(instance_of), **data}})

CFG = {"sitelink_floor": 10, "artist_sitelink_floor": 20}

def test_registry_has_music():
    assert DOMAINS["music"] is music

def test_floor_for_forms_and_a_higher_one_for_artist_only_rows():
    assert music.triage(mrow(["form"], 10), CFG)[0] == "pass"
    assert music.triage(mrow(["form"], 9), CFG) == ("drop", "9 sitelinks, under the floor of 10")
    assert music.triage(mrow(["artist"], 19), CFG) == ("drop", "19 sitelinks, under the floor of 20")
    assert music.triage(mrow(["artist", "genre"], 12), CFG)[0] == "pass"

def test_people_and_folded_parts_are_dropped():
    assert music.triage(mrow(["artist"], 90, ["Q5"]), CFG) == ("drop", "a person, not a work")
    assert music.triage(mrow(["form"], 40, folded_into="Q9"), CFG) == ("drop", "folded into Q9")

def test_calibration_always_passes():
    assert music.triage(mrow(["calibration"], 1, calibration=True), CFG) == ("pass", "calibration")

def test_owns_uses_the_music_classes(tmp_path):
    sparql = FakeSparql({"wd:Q7366": [{"sub": "Q7366"}]})
    assert music.owns(["Q7366"], sparql, tmp_path / "c.json")

def test_wording_has_the_four_connections():
    assert set(music.QUESTIONS["connections"]) == {"direct", "seeds", "entertainment", "none"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_music.py`
Expected: FAIL (`ImportError: cannot import name 'music'`).

- [ ] **Step 3: Implement `music.py` (classes, `owns`, `QUESTIONS`, `triage`), register it, add the config block**

Triage order: calibration → person or group (P31 Q5 or a musical-group class: "a person, not a work") → a recording, performance or edition of a score (the builder lists the P31 QIDs found while testing; reason "a recording or edition, not a work of its own") → `folded_into` → floor (artist floor when `set(row.sources) == {"artist"}`).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass; `python -m pipeline --domain music --help` lists the domain.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/music.py pipeline/domains/__init__.py config/pipeline.json tests/test_pipeline_music.py
git commit -m "Pipeline: music domain (classes, owns, wording, triage, config)"
```

---

### Task 4: Music candidates (stage 0)

**Files:**
- Modify: `pipeline/domains/music.py`
- Test: `tests/test_pipeline_music.py`

**Interfaces:**
- Consumes: `common.class_closure`, `common.fold_parts`, `common.other_domain`, `common.bounded`, `common.facet_patterns`.
- Produces: `music.candidates(queue, sparql, wikipedia, cfg: dict, wikidata=None) -> int`, sources `form`, `genre`, `facet`, `artist`.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_source_tags_its_rows(q):
    sparql = FakeSparql({"wdt:P7937": [item("Q1", 30)], "wdt:P136 ?genre": [item("Q2", 30)],
                         "wdt:P175|wdt:P86|wdt:P676": [item("Q3", 30)]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q1").sources == ["form"] and q.row("Q2").sources == ["genre"] and q.row("Q3").sources == ["artist"]

def test_no_subclass_path_in_candidate_queries(q):
    sparql = FakeSparql()
    music.candidates(q, sparql, None, load_config())
    assert not any("wdt:P279*" in x for x in sparql.queries if "?sub" not in x)

def test_a_song_does_not_fold_into_its_album(q):
    # Q10 (song) has P361 Q20 (an album, P31 Q482994): Q10 is queued as itself, Q20 is not queued
    ...
    assert q.row("Q10") is not None and q.row("Q20") is None

def test_people_are_reported_not_queued(q, capsys):
    sparql = FakeSparql({"wdt:P7937": [item("Q5x", 90, types="Q5")]})
    music.candidates(q, sparql, None, load_config())
    assert q.row("Q5x") is None and "person" in capsys.readouterr().out
```

(`item` from `test_pipeline_film`; extend it with a `types` argument if it lacks one.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_music.py -k "source or subclass or people or album"`
Expected: FAIL (`no attribute 'candidates'`).

- [ ] **Step 3: Implement `music.candidates`**

Patterns: `form`: `VALUES ?f { <forms closure> } ?item wdt:P7937|wdt:P136|wdt:P31 ?f .`; `genre`: `VALUES ?g { <genres> } ?item wdt:P136 ?g .`; `facet`: `common.facet_patterns(cfg["music"])` without its `genres` entry; `artist`: `VALUES ?a { <chunk of artist QIDs> } ?item wdt:P175|wdt:P86|wdt:P676 ?a .`. Each pattern is bounded to each class of `WORK_CLASSES` (`common.bounded`), its rows go through `common.fold_parts(…, "music", FOLD_WHOLES, FOLD_STOP)`, and any row whose item is a person or group, or `common.other_domain` reports, is skipped and printed.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/music.py tests/test_pipeline_music.py
git commit -m "Pipeline: music candidates (forms, Christian genres, subjects, seed artists)"
```

---

### Task 5: Music dossier and calibration

**Files:**
- Modify: `pipeline/domains/music.py`
- Test: `tests/test_pipeline_music.py`

**Interfaces:**
- Consumes: `common.sections`, `common.calibration_qids`, the film/literature dossier pattern.
- Produces:
  - `music.HEADINGS: dict[str, tuple[str, ...]]` (six languages; en: `"background", "composition", "writing", "recording", "lyrics", "text", "structure", "music", "theme", "meaning", "reception", "liturgical"`; the builder fills it/es/fr/de/pt equivalents).
  - `music.dossier(row, cfg, wiki_for, wikidata) -> dict` and `music.dossier_stage(row, cfg, wiki_for, wikidata) -> tuple[str, dict]` (film's return shape: `{"notes": [...], "languages": {...}}`).
  - `music.wikidata_note(entity: dict, labels_of) -> dict | None`: one note `{"lang": "en", "url": "https://www.wikidata.org/wiki/<QID>", "heading": "Wikidata", "text": …}` naming composer (P86), lyricist (P676), performer (P175), date (P577 or P571), and the text it is based on (P144) or is about (P921), saying "a book of the Bible" when that item is an instance of Q29154430.
  - `music.calibration_qids(wikis, wikidata=None, tree=None)` over roots `{"music", "musical-theatre", "opera"}`; `music.add_calibration(queue, sparql, qids) -> int`, skipping people and groups with `"music calibration: <qid> skipped (a person or group)"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_dossier_reads_the_wanted_sections_and_the_parts(...):
    # FakeSections with "Background", "Lyrics", "Track listing" on the work page, and one part page:
    # notes hold Background and Lyrics, not Track listing; the part page's sections are headed "<part> / <heading>"

def test_wikidata_note_names_the_psalm_it_sets():
    note = music.wikidata_note(entity("Q1", P86=["Q100"], P144=["Q200"]), labels_of={"Q100": "Allegri", "Q200": "Psalm 51"}.get)
    assert "Allegri" in note["text"] and "Psalm 51" in note["text"]

def test_calibration_skips_people(q, capsys):
    sparql = FakeSparql({"VALUES ?item": [item("Q1", 20)]})   # Q2 (Bach) answers to no work class
    music.add_calibration(q, sparql, ["Q1", "Q2"])
    assert q.row("Q1").calibration and q.row("Q2") is None
    assert "Q2 skipped" in capsys.readouterr().out
```

(The first test follows `test_pipeline_literature`'s volume-page test with `FakeSections` and `wiki_for_pages`; write it in that shape with these headings.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_music.py -k "dossier or wikidata_note or calibration"`
Expected: FAIL.

- [ ] **Step 3: Implement dossier, `wikidata_note`, calibration**

Languages: P407 (language of work) when it maps to one of the six (film's `ORIGINAL_LANGUAGES`), then film's order of preference, at most `dossier_languages`. Parts: the row's stage-0 `parts`, else the whole's P527. Wikiquote as film.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/music.py tests/test_pipeline_music.py
git commit -m "Pipeline: music dossier and calibration"
```

---

### Task 6: Art module: classes, `owns`, significance wording, triage, config, registry

**Files:**
- Create: `pipeline/domains/art.py`
- Modify: `pipeline/domains/__init__.py` (`DOMAINS["art"] = art`)
- Modify: `config/pipeline.json` (an `art` block)
- Test: `tests/test_pipeline_art.py`

**Interfaces:**
- Consumes: `common.owns` (Task 1).
- Produces:
  - `art.WORK_CLASSES: tuple[str, ...] = ("Q3305213", "Q860861", "Q132137", "Q15711026", "Q46686", "Q3714446", "Q21061279", "Q219423")` (painting, sculpture, icon, altarpiece, reredos, retable, stained-glass window, mural); the builder appends mosaic, tapestry and the liturgical goldsmith's works after checking them.
  - `art.STOP_CLASSES: tuple[str, ...]` = building (Q41176) and church building (Q16970): folding never goes into them.
  - `art.owns(instance_of, sparql, cache=None) -> bool` = `common.owns(instance_of, WORK_CLASSES, sparql, cache)`. **Consumed by the Catholic media source.**
  - `art.QUESTIONS` (wording below), `art.triage(row, cfg) -> tuple[str, str]`.
  - `art.covered = common.covered_with_parts` (Task 2).
- Config block:

```json
"art": {
  "sitelink_floor": 15,
  "depicts": ["Q302", "Q345"],
  "biblical_classes": ["Q12405827"],
  "scene_classes": ["… Nativity, Crucifixion, Annunciation, Last Supper classes, checked on Wikidata"],
  "iconclass_prefixes": ["7", "11"],
  "genres": ["Q2864737", "Q645717"],
  "works": [],
  "benchmark": [{"qid": "…", "name": "…", "kind": "strong | implicit | control"}]
}
```

- Wording (verbatim):
  - `plausible` (the significance question; key unchanged so `pipeline.judge` and stage 3 need no change): "Does the artwork described in `text` shape how the scene it depicts, or the Christian faith, is imagined or understood, or carry a theological reading discussed in its sources, beyond depicting a religious subject? A famous work that set how a scene is pictured, or a secular work with an implicit seed of the Word, counts; one devotional image among many of the same subject does not."
  - `connection`: "`text` is a dossier on one artwork (possibly an altarpiece or cycle with its panels or scenes). How does the work relate to the Gospel? A work need not be religious to carry seeds of the Word: a moral or spiritual truth that the Gospel brings to fulfilment (mercy, forgiveness, sacrifice, conversion, hope...) is enough, even when it is implicit. Judge what the work shows and how its sources read it; do not rest the judgment on Gnostic or esoteric readings."
  - `connections`: `direct` "direct Christian or biblical influence"; `seeds` "implicit seeds of the Word: a truth the Gospel fulfils, in the whole work or in one panel or scene"; `entertainment` "Christian or biblical imagery used for decoration, satire or shock, without engaging its meaning"; `none` "no connection, not even an implicit one".

- [ ] **Step 1: Write the failing tests**

```python
def arow(sources, count, instance_of=("Q3305213",), calibration=False, **data): ...  # as mrow

def test_registry_has_art():
    assert DOMAINS["art"] is art

def test_art_floor_and_the_list_source():
    assert art.triage(arow(["depicts"], 14), {"sitelink_floor": 15}) == ("drop", "14 sitelinks, under the floor of 15")
    assert art.triage(arow(["list"], 2), {"sitelink_floor": 15})[0] == "pass"

def test_copies_and_people_are_dropped():
    assert art.triage(arow(["depicts"], 40, ["Q5"]), {"sitelink_floor": 15}) == ("drop", "a person, not a work")

def test_significance_is_the_quick_question():
    assert "beyond depicting a religious subject" in art.QUESTIONS["plausible"]
    assert "decoration, satire or shock" in art.QUESTIONS["connections"]["entertainment"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_art.py`
Expected: FAIL (`cannot import name 'art'`).

- [ ] **Step 3: Implement `art.py` (classes, `owns`, `QUESTIONS`, `triage`), register it, add the config block**

Triage: calibration → person → `folded_into` → a copy, print or study class (the builder lists the P31 QIDs found while testing, with the reason "a copy or study, not a work of its own") → floor unless the source is `list`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/art.py pipeline/domains/__init__.py config/pipeline.json tests/test_pipeline_art.py
git commit -m "Pipeline: art domain (classes, owns, significance wording, triage, config)"
```

---

### Task 7: Art candidates (stage 0)

**Files:**
- Modify: `pipeline/domains/art.py`
- Test: `tests/test_pipeline_art.py`

**Interfaces:**
- Consumes: `common.class_closure`, `common.fold_parts(…, stop_classes=art.STOP_CLASSES)`, `common.other_domain`, `common.bounded`.
- Produces: `art.candidates(queue, sparql, wikipedia, cfg, wikidata=None) -> int`, sources `depicts`, `iconclass`, `genre`, `list`.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_art_source_tags_its_rows(q):
    sparql = FakeSparql({"wdt:P180": [item("Q1", 30)], "wdt:P1257": [item("Q2", 30)], "wdt:P136 ?genre": [item("Q3", 30)]})
    art.candidates(q, sparql, None, load_config())
    assert {q.row(x).sources[0] for x in ("Q1", "Q2", "Q3")} == {"depicts", "iconclass", "genre"}

def test_fold_stops_at_a_building(q):
    # the fresco cycle Q1 is part of (P361) the chapel Q2, an instance of church building (Q16970):
    # Q1 stays the candidate, Q2 is never queued
    ...
    assert q.row("Q1") is not None and q.row("Q2") is None

def test_listed_works_are_queued_by_qid(q, monkeypatch):
    cfg = load_config(); cfg["art"]["works"] = [{"qid": "Q7", "name": "x"}]
    sparql = FakeSparql({"wd:Q7": [item("Q7", 3)]})
    art.candidates(q, sparql, None, cfg)
    assert q.row("Q7").sources == ["list"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_art.py -k "source or building or listed"`
Expected: FAIL.

- [ ] **Step 3: Implement `art.candidates`**

Patterns: `depicts`: `VALUES ?d { <depicts> } ?item wdt:P180 ?d .`, plus `?item wdt:P180 ?d . ?d wdt:P411 [] .` (a saint), plus biblical figures and scene classes; `iconclass`: `?item wdt:P1257 ?ic . FILTER(STRSTARTS(STR(?ic), "7") || STRSTARTS(STR(?ic), "11"))` from `iconclass_prefixes`; `genre`: `VALUES ?genre { <genres> } ?item wdt:P136 ?genre .`; `list`: its own query, `VALUES ?item { <works> }` with the query LIMIT kept but no class filter and not run through `common.bounded` (a listed work need not be in `WORK_CLASSES`, but is reported if it is a person). The other three sources go through `common.bounded` per class; all four are then folded by `common.fold_parts(…, "art", WORK_CLASSES, STOP_CLASSES)`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/art.py tests/test_pipeline_art.py
git commit -m "Pipeline: art candidates (depicts, Iconclass, genre, curated list)"
```

---

### Task 8: Art dossier, calibration and the benchmark

**Files:**
- Modify: `pipeline/domains/art.py`
- Modify: `pipeline/publish.py` (`ranking`: benchmark rows are not translation suggestions)
- Modify: `pipeline/__main__.py` (`release-benchmark` command)
- Modify: `config/pipeline.json` (`art.benchmark`: the drafted list)
- Test: `tests/test_pipeline_art.py`, `tests/test_pipeline_publish.py`, `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: Task 6/7 art module; the music dossier shape (Task 5) for `art.HEADINGS`, `art.dossier`, `art.dossier_stage`, `art.wikidata_note` (depicts P180, creator P170, date P571, collection P195, location P276, based on P144).
- Produces:
  - `art.calibration_qids(wikis, wikidata=None, tree=None)` over roots `{"visual-arts", "crafts"}` minus the `comics` subtree; `art.add_calibration(queue, sparql, qids, benchmark: list[dict] | None = None) -> int` adds the existing art works and every `art.benchmark` QID, the latter with stage-0 data `{"benchmark": True, "benchmark_kind": <kind>}`.
  - `publish.ranking`: a calibration row with `data[0].get("benchmark")` is never listed under translation suggestions.
  - CLI: `python -m pipeline --domain art release-benchmark QID [QID …]`: for each QID that is a benchmark row, clears `calibration` and the `benchmark` flag (prints `released <QID>`); any other QID prints `<QID> is not a benchmark row, nothing changed` and the command exits non-zero. A queue method `Queue.release_calibration(qid: str) -> bool` does the update.
  - Art dossier languages: en, it, then the others (no original-language rule).

- [ ] **Step 1: Write the failing tests**

```python
def test_benchmark_rows_are_flagged(q):
    sparql = FakeSparql({"VALUES ?item": [item("Q1", 50), item("Q2", 50)]})
    cfg_benchmark = [{"qid": "Q2", "name": "Rublev Trinity", "kind": "strong"}]
    art.add_calibration(q, sparql, ["Q1"], benchmark=cfg_benchmark)
    assert q.row("Q2").calibration and q.row("Q2").data[0]["benchmark"]

def test_benchmark_rows_are_not_translation_suggestions(q):   # in test_pipeline_publish.py
    q.add("Q2", "art", "calibration", {"en": "Trinity"}, {}, calibration=True, data={"benchmark": True})
    q.set_outcome("Q2", 1, "translation", {"have": ["it"], "missing": ["en", "es", "fr", "de", "pt"]})   # covered in a language: would be a suggestion
    assert "Trinity" not in ranking(q)   # the translation suggestions omit the benchmark title

def test_release_turns_one_benchmark_row_into_a_candidate(q):   # in test_pipeline_cli.py
    ...  # run the CLI on Q2; then q.row("Q2").calibration is False and publish() writes its page

def test_release_refuses_a_non_benchmark_qid(q, capsys):
    ...  # Q1 is an ordinary calibration row: exit code != 0, "Q1 is not a benchmark row, nothing changed", row unchanged
```

(`add_calibration` takes the benchmark list as a keyword argument defaulting to `load_config()["art"]["benchmark"]`, so the CLI's existing `domain.add_calibration(queue, sparql, qids)` call still works.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_art.py tests/test_pipeline_publish.py tests/test_pipeline_cli.py -k benchmark`
Expected: FAIL.

- [ ] **Step 3: Implement dossier, calibration, benchmark flag, ranking rule, `release-benchmark`, `Queue.release_calibration`**

Draft `art.benchmark` with about 20 works, each QID checked on Wikidata: strong (Rublev's *Trinity*, the Isenheim Altarpiece, Leonardo's *Last Supper*, Michelangelo's *Pietà*, Caravaggio's *The Calling of Saint Matthew*, Rembrandt's *The Return of the Prodigal Son*, Millet's *The Angelus*, Giotto's Scrovegni Chapel frescos, Fra Angelico's *Annunciation* (San Marco), Michelangelo's Sistine Chapel ceiling, Velázquez's *Christ Crucified*), implicit (works with a seed of the Word but no religious subject, e.g. Millet's *The Gleaners*, Van Gogh's *The Potato Eaters*, Rembrandt's *The Night Watch* only if its sources support it: the builder picks four or five), and two or three controls (Serrano's *Piss Christ*, a work using Christian imagery for shock; a secular work with no connection, e.g. Mondrian's *Composition with Red, Blue and Yellow*). The author approves the list before the first live art run (open item; not a blocker for this task).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/domains/art.py pipeline/publish.py pipeline/__main__.py pipeline/queue.py config/pipeline.json tests/test_pipeline_art.py tests/test_pipeline_publish.py tests/test_pipeline_cli.py
git commit -m "Pipeline: art dossier, calibration and the benchmark (released one QID at a time)"
```

---

### Task 9: End to end through the CLI, and the README

**Files:**
- Modify: `README.md` (the pipeline section: the two domains, the floors, `release-benchmark`, the benchmark approval step)
- Test: `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no new code beyond fixes the tests reveal.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("domain", ["music", "art"])
def test_run_takes_a_candidate_to_a_proposal(domain, tmp_path, ...):
    # as the existing film/literature run test: fake Sparql, wikis, Wikipedia and judge;
    # `run` then `publish` writes one proposal page and the ranking page has a "=== Music ===" / "=== Art ===" section

def test_ranking_links_the_new_domains(q):   # in test_pipeline_publish.py
    ...  # one open music row and one open art row: the "Go to:" line has [[#Art|Art]] (1) and [[#Music|Music]] (1)
```

- [ ] **Step 2: Run them to verify they fail (or pass already, which is a finding to record in the commit message)**

Run: `.venv/bin/python -m pytest -q tests/test_pipeline_cli.py tests/test_pipeline_publish.py -k "run_takes or new_domains"`
Expected: FAIL where a wiring gap exists (e.g. the CLI's `--domain` choices).

- [ ] **Step 3: Fix the wiring the tests reveal; update `README.md`**

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_pipeline_cli.py tests/test_pipeline_publish.py pipeline/__main__.py
git commit -m "Pipeline: music and art end to end; README"
```

---

## Open items (not blockers for building)

- The author approves `art.benchmark` before the first live art run (`--domain art calibrate`).
- The author reads each domain's calibration report before its first live `publish`.
- Architecture: its own domain later (decided 2026-11-23).
- Stage 7, not this plan: when the first art article is written, map the imported local items for painting, sculpture, icon, altarpiece and stained-glass window to the `artwork` layout in `config/infobox_types.json` (spec, "Infobox types").
