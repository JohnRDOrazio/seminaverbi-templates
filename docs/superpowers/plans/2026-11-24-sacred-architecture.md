# Sacred Architecture Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pipeline domain `architecture` that proposes sacred buildings of artistic value and receives the press's building mentions.

**Architecture:** A module `pipeline/domains/architecture.py` with the same public functions as `art.py`, registered in `DOMAINS`. The benchmark code now inside `art.py` moves to a shared module, since both domains use it. The quick check is art's significance question, reworded; nothing in stages 1-6 changes.

**Tech Stack:** Python 3.12, pytest, WDQS through `pipeline.sources.Sparql`, Jev through `pipeline.judge`.

**Spec:** `docs/superpowers/specs/2026-11-23-sacred-architecture-design.md`

## Global Constraints

- Work classes: Q16970, Q2977, Q163687, Q108325, Q160742, Q44613, with their subclasses (`common.owns`).
- `sitelink_floor` is 15. Waived for sources `unesco` and `list`, and for `common.press_exempt(row)` (≥ 0.60).
- A building already queued under another domain stays there (`common.other_domain`).
- Art is unchanged in behaviour: `art.STOP_CLASSES`, its candidates and its benchmark stay as they are.
- Rows with a status (`open`, `approved`, `created`, `declined`) never have their outcomes changed.
- Field node `sacred-architecture`. No new dependency. Live WDQS may be read to measure; nothing is written to a wiki, and Jev is not called.
- Needs PR #66 (the press exemption in music and art) merged first.

## Decisions made here (the author checks these)

1. **Christian test.** Church buildings and cathedrals (Q16970, Q2977 and subclasses) are Christian by definition and need no statement: requiring P140 or P825 would lose 25 of the 744 measured on 2026-11-24. Basilica, chapel, abbey and monastery need a Christian P140, or P825 when they have no P140 at all (a dedication alone does not make a monastery Christian, but a stated other religion rules it out). Any candidate of any class whose P140 is stated and not Christian is left out.
2. **Hagia Sophia** (P31 mosque, P140 Sunni Islam) and the like come only through `works`. A listed building needs no class and no religion.
3. **Infobox:** `config/infobox_types.json` has no building layout. Proposals do not need one; a page does. It is a separate change (template, module and six languages), not part of this plan.
4. **Benchmark:** 18 buildings, drafted in Task 5; approved by the author before the first live run.

## Review Focus

- A building with two P140 values, one Christian and one not (a converted church): kept, as it has a Christian one (Task 2).
- A row dropped under the floor, then listed in `works`, when the run stops between the two: triaged again by the next run, and not if its drop had another reason (Task 3).
- A benchmark building found by another source before `calibrate` has run: held back (Task 1's shared code, pinned again in Task 5).
- A building that is also a museum or a ruin, with a person or an event among its P31 values: kept when it is of a work class; a person never (Task 2).
- A press mention of a chapel that art's rows already hold: stays in art (Task 4).

---

### Task 1: Share the benchmark code

**Files:**
- Create: `pipeline/domains/benchmark.py`
- Modify: `pipeline/domains/art.py` (`_listed`, `_released`, `_hold_benchmark`, `_settled`, `add_calibration`, `_without`)
- Test: `tests/test_pipeline_benchmark.py`

**Interfaces (produces):**
- `listed(sparql, domain: str, qids: list[str]) -> list[dict]`
- `hold(queue, domain: str, benchmark: list[dict]) -> None`
- `add(queue, sparql, domain: str, qids: list[str], benchmark: list[dict], work_classes: Iterable[str]) -> int`
- `PERSON = "Q5"`, used by both domains.

- [ ] **Step 1:** Write `test_hold_marks_only_rows_of_its_domain`: rows QA (domain `art`) and QB (domain `architecture`), both without status; `hold(q, "architecture", [{"qid": "QA", "kind": "strong"}, {"qid": "QB", "kind": "weak"}])`; assert `q.row("QB").calibration is True`, `q.row("QB").data[0]["benchmark_kind"] == "weak"`, `q.row("QA").calibration is False`.
- [ ] **Step 2:** Write `test_add_reports_under_its_domain(capsys)`: `add` with a QID no class query answers prints `architecture calibration: Q9 skipped (a person, or not an architecture work)` and adds nothing.
- [ ] **Step 3:** Run `pytest tests/test_pipeline_benchmark.py`. Expected: import error.
- [ ] **Step 4:** Move the functions, with the domain as a parameter. `art.py` keeps `add_calibration(queue, sparql, qids, benchmark=None)` and `calibration_qids(...)` as thin calls with the same signatures and messages (for art: `(a person, or not an art work)`).
- [ ] **Step 5:** Run the whole suite. Expected: all pass, with `tests/test_pipeline_art.py` unedited.
- [ ] **Step 6:** Commit `refactor: the benchmark code is shared by the domains that hold one`.

### Task 2: The domain's classes, triage and candidates

**Files:**
- Create: `pipeline/domains/architecture.py`
- Modify: `config/pipeline.json` (key `architecture`), `pipeline/domains/common.py`
- Test: `tests/test_pipeline_architecture.py`

**Interfaces (consumes):** `benchmark.listed`, `benchmark.hold`. **(produces):**
- `WORK_CLASSES: tuple[str, ...]`, `CHRISTIAN_BY_CLASS = ("Q16970", "Q2977")`, `QUESTIONS: dict`
- `owns(instance_of, sparql, cache=None) -> bool`; `triage(row, cfg) -> tuple[str, str]`
- `candidates(queue, sparql, wikipedia, cfg, wikidata=None) -> int`
- `press_stage0(qid, entity, sparql) -> dict` returning `{}`

`config/pipeline.json` gets `"architecture": {"sitelink_floor": 15, "christian_religions": [...], "unesco": ["Q9259", "Q43113623"], "works": [{"qid": "Q48435", "name": "Sagrada Família"}], "benchmark": []}`. `christian_religions` is a list measured on live WDQS: the P140 values found on items of the work classes with ≥ 15 sitelinks, each kept if it is Christianity (Q5043) or a Christian confession, rite or order. Record the query and the date in a comment in `architecture.py`.

- [ ] **Step 1:** Triage tests (stage-0 data as art's): `count` 14, source `building` → `("drop", "14 sitelinks, under the floor of 15")`; 15 → pass; 3 with source `unesco`, `list`, or `press:sir` at 0.6 → pass; 3 with `press:sir` at 0.55 → drop; P31 `["Q5", "Q16970"]` → `("drop", "a person, not a work")`; calibration → `("pass", "calibration")`.
- [ ] **Step 2:** `owns` tests: `["Q163687"]` true and `["Q41176"]` (building) false, with a fake closure.
- [ ] **Step 3:** Query tests, on the text of the queries a recording fake receives: the queries for Q16970 and Q2977 carry no P140 or P825 requirement; those for the other four do; every one excludes an item that has a P140 and none in `christian_religions`; the `unesco` queries carry both P1435 values, no sitelink floor, and the same Christian test; `works` is one `VALUES` query per chunk. Rows with Q5 are reported and skipped.
- [ ] **Step 4:** Queue tests: sources are `building`, `unesco` and `list`; a QID already queued under `art` is skipped and left unchanged; the two-religion building of Review Focus is added.
- [ ] **Step 5:** Run the new file. Expected: FAIL (no module).
- [ ] **Step 6:** Implement. Use `common.bounded` with `floor` for `building`; for `unesco` use `floor=1`. A candidate is added with `queue.add(qid, "architecture", source, labels, sitelinks, False, data)`. `benchmark.hold(queue, "architecture", ...)` runs in a `finally`, as in art. `QUESTIONS` is:

```python
QUESTIONS = {
    "plausible": ("Does the building described in `text` shape how the Christian faith is lived or understood "
                  "(through its liturgy, as a place of pilgrimage, or through a theology expressed in its forms), or "
                  "express the beauty of the faith through the richness and quality of its art and symbolism, beyond "
                  "being a well-known religious building? A building that set how a church is imagined, or whose art "
                  "and symbolism carry a theological reading discussed in its sources, counts; an ordinary parish "
                  "church, however old, does not."),
    "connection": ("`text` is a dossier on one building. How does the building relate to the Gospel? Judge what its "
                   "forms, its art and its use express and how its sources read them, not that it is or was a place "
                   "of worship; do not rest the judgment on Gnostic or esoteric readings."),
    "connections": {
        "direct": "direct Christian or biblical expression in its forms, art, symbolism or use",
        "seeds": "implicit seeds of the Word: a truth the Gospel fulfils, in the building or in a part of it",
        "entertainment": "Christian forms used for decoration, spectacle or tourism, without engaging their meaning",
        "none": "no connection beyond its being, or having been, a religious building",
    },
}
```

- [ ] **Step 7:** Run every query once against live WDQS (≥ 5 s apart). Record in the docstring the rows and seconds of each. None may time out or reach `common.LIMIT` unreported; if one does, split it by class or by P1435 value.
- [ ] **Step 8:** Run the suite. Commit `feat(architecture): classes, triage and candidates`.

### Task 3: Triage again when an exempt source arrives

**Files:**
- Modify: `pipeline/domains/common.py`, `pipeline/domains/architecture.py` (`candidates`)
- Test: `tests/test_pipeline_architecture.py`

**Interfaces (produces):** `common.retriage(queue, domain: str, triage, cfg: dict) -> int`: for every row of `domain` with `status is None`, not calibration, whose stage-2 outcome is `drop` and for which `triage(row, cfg)[0] == "pass"`, `queue.redo(2, qid)`; returns how many.

- [ ] **Step 1:** Tests: a floor drop whose row now has `unesco` is cleared (`2 not in q.row(qid).outcomes`); one with status `open` or `declined` keeps its outcomes; one dropped as a person stays dropped; a row that still fails the floor stays dropped; a run that raised after adding the source but before `retriage` leaves a row the next `candidates` clears.
- [ ] **Step 2:** Run. Expected: FAIL (`retriage` missing).
- [ ] **Step 3:** Implement, and call it in `candidates` after the sources and in its `finally`, so a failed source does not prevent it. `cfg` is the domain's configuration (`common.domain_config`).
- [ ] **Step 4:** Run the suite. Commit `feat(architecture): a floor drop is triaged again when an exempt source comes`.

### Task 4: Register the domain, the press and the docs

**Files:**
- Modify: `pipeline/domains/__init__.py`, `config/press.json` (`waiting_classes`: nothing to add, the domain has a module), `README.md`, the domain lists in `tests/`
- Test: `tests/test_press_link.py`, `tests/test_press_route.py`

- [ ] **Step 1:** Tests: `class_route(["Q2977"], ...) == "architecture"`; `class_route(["Q3305213"], ...)` is still `"art"`; an item with both a painting and a chapel class goes to `"art"` (order of `DOMAINS`); a routed mention of a church becomes a candidate of `architecture` with its press evidence; a chapel already queued under `art` stays there (the mention takes that domain).
- [ ] **Step 2:** Run. Expected: FAIL (`None` for Q2977).
- [ ] **Step 3:** `DOMAINS = {..., "art": art, "architecture": architecture}` (last). Check that the ranking page, the votes page and `--domain architecture` need no other change: `grep -rn '"art"' pipeline tools`, and report what each hit does for the new domain.
- [ ] **Step 4:** README: a paragraph on the domain beside art's, with the decisions above.
- [ ] **Step 5:** Run the suite. Commit `feat(architecture): the domain is registered; the press routes buildings to it`.

### Task 5: Dossier, calibration and the benchmark

**Files:**
- Modify: `pipeline/domains/architecture.py`, `config/pipeline.json`
- Test: `tests/test_pipeline_architecture.py`

**Interfaces (consumes):** `benchmark.add`, `common.calibration_qids`. **(produces):** `dossier(row, cfg, wiki_for, wikidata)`, `dossier_stage(...)`, `wikidata_note(entity, labels_of)`, `add_calibration(queue, sparql, qids, benchmark=None) -> int`, `calibration_qids(wikis, wikidata=None, tree=None)` with roots `{"sacred-architecture"}`.

`HEADINGS`, as art's (lower-case fragments, matched at a word start): en `architecture, history, construction, design, plan, exterior, interior, decoration, art, iconography, symbolism, mosaic, stained glass, sculpture, façade, facade, liturgy, worship, pilgrimage, consecration, dedication, significance, interpretation`; it `architettura, storia, costruzione, progetto, pianta, esterno, interno, decorazione, arte, iconografia, simbolismo, mosaic, vetrat, scultura, facciata, liturgia, culto, pellegrinaggio, consacrazione, dedicazione, significato, interpretazione`; es, fr, de and pt the same ideas in their own words.

- [ ] **Step 1:** Note test: `wikidata_note` names architect (P84), style (P149), religion (P140), dedication (P825), location (P131), the year of P571 and heritage (P1435), in that order, e.g. `Architect: Antoni Gaudí; style: Modernisme; dedicated to: Holy Family; location: Barcelona; date: 1882; heritage: World Heritage Site.` A person with P31 Q5 gives nothing.
- [ ] **Step 2:** Dossier tests: the lead and wanted sections of each language's page, limited by `dossier_languages`; an ordinary row with only the Wikidata note is dropped (`empty dossier`), a calibration one passes; the Scrovegni chapel's page is read for the chapel and never adds its frescos (no parts are folded or read).
- [ ] **Step 3:** Calibration test: the three kinds (`strong`, `weak`, `control`) of the config are added held back, with `benchmark_kind`; a benchmark building found by `candidates` first is held back too; `release-benchmark` releases one.
- [ ] **Step 4:** Run. Expected: FAIL.
- [ ] **Step 5:** Implement, and draft the benchmark: strong: Sagrada Família Q48435, Chartres Q180274, Notre-Dame de Paris Q2981, Hagia Sophia Q12506, Sainte-Chapelle Q193193, Notre-Dame du Haut (Ronchamp) Q638250, San Vitale Q721817, Monreale Q1303856, St Peter's Q12512, Scrovegni Chapel Q963954, and the Church of the Holy Sepulchre Q187702; weak: Matthias Church (Budapest) Q493133, St. Patrick's Cathedral (New York), the Votive Church (Vienna), Grace Cathedral (San Francisco); controls: Eiffel Tower, Sydney Opera House, Guggenheim Museum Bilbao. Look up every QID without one on Wikidata and check every QID's label and description; a listing that does not match is reported, not guessed.
- [ ] **Step 6:** Run the suite, then `python -m pipeline --db <scratch copy> --domain architecture candidates` once on a copy of the database. Report rows per source, the count under the floor and any other-domain skip. Commit `feat(architecture): dossier, calibration and the benchmark draft`.

## After the tasks

The final whole-branch review, then a PR. Before the first live run the author approves the benchmark and runs `calibrate --domain architecture`.
