# Catholic Media Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shared `press` source for the article pipeline that reads eleven Catholic outlets, links the works and
people their culture articles discuss to Wikidata, feeds them to the film and literature queues (holding music,
art and artists as `waiting:<domain>`), puts their passages in dossiers, and lists press on existing entries for
editors.

**Architecture:** A package `pipeline/press/` with one module per responsibility (store, polite fetch, discovery,
extraction, Jev questions, linking, routing, enrichment page), its own tables in the existing SQLite queue, and a
`press` subcommand family in `pipeline/__main__.py`. Domain stages change only at three seams: each domain exposes
`WORK_CLASSES` and `owns()`, triage exempts kept press mentions from the sitelink floor, and the dossier stage prepends press notes.

**Tech Stack:** Python 3 standard library (`html.parser`, `xml.etree.ElementTree`, `urllib.robotparser`, `sqlite3`),
`requests==2.34.2`, `typesafe-sdk==0.7.5` (Jev), pytest 9.2. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-23-catholic-media-source-design.md`

**Prerequisite:** Task 1 of the music/art plan (`docs/superpowers/plans/2026-11-23-music-art-domains.md`, PR #54:
`common.class_closure` and `common.owns(instance_of: Iterable[str], work_classes: Iterable[str], sparql, cache: Path | None = None) -> bool`) lands first as its own PR; this plan starts from it and does not build it.

## Global Constraints

- Outlets (config, editable): L'Osservatore Romano, Vatican News, Avvenire, Roma Sette, EWTN, La Civiltà Cattolica, Famiglia Cristiana, Aleteia, National Catholic Register, KTO, SIR (agensir.it).
- Kept of an article: link, title, date, outlet, language, and short quoted passages. Never the full text; never used for training.
- Quotations: at most 25 words each, at most three per mention, always with outlet, title, date and link.
- Jev reads only the paragraphs around each mention, at most ~150 words per question (the culture filter reads the title and the first ~150 words). Never whole articles.
- User agent: `SeminaVerbi-press/1.0 (+https://en.seminaverbi.bibleget.io/wiki/Semina_Verbi:Press_bot)`, a config value. No personal name or email. Never a browser agent, never a name listed in an outlet's robots.txt.
- HTTP: one request at a time per outlet, at least `delay_seconds` apart (default 10), obeying a robots.txt `Crawl-delay` when larger; robots.txt re-read each run; a 401/403/429 stops that outlet for the run and is reported, never retried around. Conditional requests (`If-Modified-Since`/ETag) for feeds and sitemaps.
- Cut-offs to start: `culture_drop` 0.40, disambiguation probability 0.80 with confidence 0.60, `mention_keep` 0.60.
- A press-only work is exempt from the domain's sitelink floor when its mention was kept at ≥ 0.60.
- `max_articles_per_run` 200, round-robin across outlets.
- Only publishing writes to the wiki, only to en's project namespace, as SV editor bot (through `runeditor.py`).
- Enrichment page: `Semina Verbi:Press for existing articles` on en; entries closed with `{{SV press done|url}}`.
- `TYPESAFE_API_KEY` from the environment, never printed; Jev answers cached in the `jev` table under `press:<sha1 of url>`.
- Test fixtures are synthetic feeds, sitemaps and article pages written for the tests, never copies of real articles.
- Prerequisite before the first live run (not before building): TypeSafe's written confirmation that it does not train on API inputs, recorded in `docs/content-tasks.md` with its date. The timer is not enabled before it.
- Prerequisite: `common.owns(instance_of: Iterable[str], work_classes: Iterable[str], sparql, cache: Path | None = None) -> bool` and its closure cache (`data/class_closure.json`) exist, from the music/art plan's Task 1, merged before Task 6 here.
- Music, art and artists have no domain module yet: their mentions route to `waiting:music`, `waiting:art`, `waiting:artists`.

## Review Focus

- An outlet entry that returns malformed XML, an HTML error page or a timeout: that outlet is reported and skipped for the run, the others continue (Task 3 test `test_discover_skips_a_broken_entry`).
- The same article under several URLs (tracking query, fragment, trailing slash, `rel=canonical`): one row, judged once (Task 3 `test_discover_canonicalises_urls`, Task 4 `test_extract_prefers_canonical`).
- A title that is also a common word or names several works ("Silence"): dropped unless Jev picks one at ≥ 0.80/0.60; never the first search hit by default (Task 6 `test_ambiguous_title_is_dropped_below_the_cut`).
- robots.txt unreachable (5xx, timeout): the outlet is skipped for the run and reported, never read as "allow all" (Task 2 `test_unreachable_robots_skips_the_outlet`).
- An editor adds `{{SV press done|…}}` or text to the enrichment page between runs: done markers survive and close their entries, and the bot rewrites only its own block (Task 9 `test_enrichment_keeps_done_markers`).

---

### Task 1: Press tables

**Files:**
- Create: `pipeline/press/__init__.py`, `pipeline/press/store.py`
- Test: `tests/test_press_store.py`

**Interfaces:**
- Produces: `PressStore(db: sqlite3.Connection)` (creates its tables on the queue's connection; `PressStore.of(queue)`
  shorthand); `add_article(url, outlet, lang, title=None, published=None) -> bool` (False when known);
  `articles(stage: str, outlet: str | None = None, limit: int | None = None) -> list[Article]` where `stage` is
  `"found"`, `"filtered"`, `"linked"`; `set_article(url, stage, outcome, data: dict)`;
  `add_mention(Mention) -> None` (upsert on `(url, qid)`); `mentions(qid=None, route=None) -> list[Mention]` (None
  means no filter); `unrouted() -> list[Mention]` (route IS NULL);
  `set_route(url, qid, route)`; `feed_cache(url) -> tuple[str | None, str | None]` and
  `set_feed_cache(url, etag, last_modified)`.
  `Article` dataclass: `url, outlet, lang, title, published, stage, outcome, data`.
  `Mention` dataclass: `url, qid, label, kind ("work"|"person"), domain, p: float, passages: list[str],
  route: str | None` (`"candidate"`, `"enrichment"`, `"waiting"`, or None until routed).

- [ ] **Step 1: Write the failing tests**

```python
def test_tables_live_in_the_queue_db(tmp_path):
    q = Queue(tmp_path / "q.sqlite"); s = PressStore.of(q)
    assert s.add_article("https://x/a", "sir", "it") is True
    assert s.add_article("https://x/a", "sir", "it") is False
    names = {r[0] for r in q.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"press_articles", "press_mentions", "press_feeds"} <= names

def test_no_body_column():
    s = PressStore(sqlite3.connect(":memory:"))
    cols = {c[1] for c in s.db.execute("PRAGMA table_info(press_articles)")}
    assert not cols & {"text", "body", "html", "content"}

def test_mention_upsert_and_route():
    s = PressStore(sqlite3.connect(":memory:")); s.add_article("u", "sir", "it")
    m = Mention("u", "Q1", "Silence", "work", "film", 0.7, ["a b c"], "candidate")
    s.add_mention(m); s.add_mention(replace(m, p=0.9))
    assert [x.p for x in s.mentions(qid="Q1")] == [0.9]
    s.set_route("u", "Q1", "enrichment"); assert s.mentions(route="enrichment")[0].qid == "Q1"
    assert s.unrouted() == []
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_store.py -q` — Expected: FAIL (`ModuleNotFoundError: pipeline.press`).
- [ ] **Step 3: Implement `pipeline/press/store.py`.** Schema: `press_articles(url PK, outlet, lang, title, published, found_at REAL, stage TEXT DEFAULT 'found', outcome, data_json)`; `press_mentions(url, qid, label, kind, domain, p REAL, passages_json, route, PRIMARY KEY(url, qid))`; `press_feeds(url PK, etag, last_modified)`. Passages are stored only after truncation by Task 4's `passage()`; the store refuses (ValueError) a passage over 25 words.
- [ ] **Step 4: Run** the tests — Expected: PASS; then `.venv/bin/python -m pytest -q` all green.
- [ ] **Step 5: Commit** `git add pipeline/press/__init__.py pipeline/press/store.py tests/test_press_store.py && git commit -m "press: tables for articles, mentions and feed caches"`

### Task 2: Polite fetch client and config

**Files:**
- Create: `pipeline/press/fetch.py`, `config/press.json`
- Test: `tests/test_press_fetch.py`

**Interfaces:**
- Consumes: `PressStore.feed_cache/set_feed_cache` (Task 1).
- Produces: `load_press_config() -> dict` (reads `config/press.json`); `Refused(Exception)` with `.outlet, .reason`;
  `PoliteClient(session, user_agent: str, delay: float, store: PressStore | None = None, clock=time.monotonic,
  sleep=time.sleep)`; `client.get(outlet: str, url: str, conditional: bool = False) -> Response | None` (None on 304);
  `client.refused: dict[str, str]` (outlet → reason, for the run report).
- `config/press.json` keys: `user_agent` (the exact string in Global Constraints), `delay_seconds` 10,
  `max_articles_per_run` 200, `culture_drop` 0.40, `pick_p` 0.80, `pick_confidence` 0.60, `mention_keep` 0.60,
  `press_exempt` 0.60, `waiting_classes` (used only while a domain has no module: `{"music": ["Q105543609", "Q7366", "Q482994", "Q484692"],
  "art": ["Q3305213", "Q860861", "Q132137", "Q15711026", "Q46686", "Q3714446", "Q21061279", "Q219423"],
  "artists": ["Q5"]}`, the music/art plan's `WORK_CLASSES`),
  `outlets` (list, filled in Task 10; here one placeholder-free entry per outlet with `id`, `name`, `base`, `langs`,
  `entries: []`, `culture_paths: []`, `since: null`).

- [ ] **Step 1: Write the failing tests** (FakeSession as in `tests/test_pipeline_sources.py`; a fake clock)

```python
def test_sends_our_agent_and_waits_between_requests(): ...
    # two GETs to the same host: User-Agent == cfg["user_agent"]; sleep called with ≥ 10 s before the second
def test_crawl_delay_wins_when_larger(): ...      # robots "Crawl-delay: 30" -> waits 30
def test_disallowed_path_is_not_fetched(): ...    # robots "Disallow: /private" -> get(".../private/x") is None, no request
def test_403_refuses_the_outlet_for_the_run():
    # first article 403 -> raises Refused("vaticannews", "403"); a later get for that outlet raises at once, no request
def test_unreachable_robots_skips_the_outlet():   # robots.txt 503 or timeout -> Refused(outlet, "robots unreachable")
def test_conditional_get_uses_the_cache(): ...    # stored ETag sent as If-None-Match; 304 -> None
def test_config_agent_has_no_email():
    assert "@" not in load_press_config()["user_agent"]
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_fetch.py -q` — Expected: FAIL (import error).
- [ ] **Step 3: Implement** `PoliteClient` with one `urllib.robotparser.RobotFileParser` per host read through the same session (robots 401/403 → disallow all; 5xx or timeout → `Refused`); per-host last-request time; `Refused` on 401/403/429 (recorded in `refused`, never retried).
- [ ] **Step 4: Run** the tests — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/fetch.py config/press.json tests/test_press_fetch.py && git commit -m "press: polite fetch client and config"`

### Task 3: Discovery

**Files:**
- Create: `pipeline/press/outlets.py`
- Test: `tests/test_press_outlets.py`, fixtures `tests/fixtures/press/{feed.rss,feed.atom,sitemap-index.xml,urlset.xml,news-sitemap.xml}` (synthetic)

**Interfaces:**
- Consumes: `PoliteClient.get` (Task 2), `PressStore.add_article` (Task 1).
- Produces: `canonical(url: str) -> str` (drops fragment, `utm_*`/`fbclid`/`gclid` query keys, trailing slash);
  `entry_urls(xml_text: str) -> list[tuple[str, str | None]]` (url, date ISO or None; RSS, Atom, sitemap index
  recursion is the caller's, urlset, news sitemap); `discover(store, client, outlets: list[dict], limit: int) ->
  dict[str, int]` (new articles per outlet; round-robin until `limit`; a `<sitemapindex>` is followed to its child
  sitemaps matching the outlet's `langs`).

- [ ] **Step 1: Write the failing tests**

```python
def test_rss_atom_and_sitemaps_parse(): ...        # each fixture -> expected (url, date) pairs
def test_only_culture_paths_and_since(): ...       # culture_paths ["/cultura/"], since "2026-01-01" filters both ways
def test_round_robin_respects_the_limit():          # two outlets × 5 urls, limit 4 -> 2 + 2
def test_discover_canonicalises_urls():
    assert canonical("https://x.va/a/?utm_source=f&id=3#top") == "https://x.va/a?id=3"
def test_discover_skips_a_broken_entry(capsys):     # one outlet returns "<html>oops", the other's articles still added
def test_refused_outlet_is_reported_not_retried():  # Refused from client -> counted under refused, no further gets
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_outlets.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** with `xml.etree.ElementTree` (namespaced tags matched by local name); a parse error or non-XML body prints `press: <outlet> <url>: not a feed or sitemap` to stderr and skips that entry.
- [ ] **Step 4: Run** — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/outlets.py tests/test_press_outlets.py tests/fixtures/press && git commit -m "press: discovery from feeds and sitemaps"`

### Task 4: Article extraction, windows and passages

**Files:**
- Create: `pipeline/press/extract.py`
- Test: `tests/test_press_extract.py`, fixtures `tests/fixtures/press/article-*.html` (synthetic, in it/en/fr)

**Interfaces:**
- Produces: `extract(html: str, url: str, default_lang: str) -> Page` with `Page(title, published, lang, canonical,
  paragraphs: list[str], spans: list[Span])`; `Span(text, paragraph: int, kind: "italic"|"quoted"|"wikilink",
  wiki: tuple[str, str] | None)` (`wiki` = (lang, title) for a link to `<lang>.wikipedia.org/wiki/<title>`);
  `window(paragraphs, index, words=150) -> str` (the paragraph with the mention plus neighbours, cut to `words`);
  `passage(paragraph: str, needle: str, words=25) -> str` (≤ 25 words around `needle`, `…` at cut ends);
  `opening(page, words=150) -> str` (title + first words, for the culture filter).
- Title from `og:title` then `<title>`; date from `article:published_time`, `<time datetime>`, JSON-LD
  `datePublished`; lang from `<html lang>` (first two letters) else `default_lang`; canonical from
  `<link rel="canonical">`. Paragraphs: `<p>` text inside `<article>` when present, else all `<p>`; nav, footer,
  aside, script and style skipped. Quoted spans: «…», “…”, "…" of 1–8 words starting with a capital.

- [ ] **Step 1: Write the failing tests**

```python
def test_title_date_lang_and_paragraphs(): ...
def test_extract_prefers_canonical(): ...           # canonical link wins over the fetched url
def test_italic_quoted_and_wikipedia_spans(): ...   # <em>Il Signore degli Anelli</em>, «Silence», a wikilink -> 3 spans
def test_window_never_exceeds_150_words(): ...
def test_passage_is_at_most_25_words_and_keeps_the_needle():
    p = passage(" ".join(["w"] * 100 + ["Silence"] + ["w"] * 100), "Silence")
    assert len(p.replace("…", "").split()) <= 25 and "Silence" in p
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_extract.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** with `html.parser.HTMLParser` (no new dependency).
- [ ] **Step 4: Run** — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/extract.py tests/test_press_extract.py tests/fixtures/press && git commit -m "press: article extraction, windows and passages"`

### Task 5: Jev questions for articles

**Files:**
- Create: `pipeline/press/judge.py`
- Test: `tests/test_press_judge.py`

**Interfaces:**
- Consumes: `pipeline.judge.CachingClient`, `tools.jev.MODEL`.
- Produces: `article_key(url) -> str` (`"press:" + sha1(url).hexdigest()`); `PressJudge(client, queue)` with
  `culture(url, text) -> float`, `pick(url, passage, label, options: dict[str, str]) -> tuple[str | None, float,
  float]` (qid or None for "none", probability, confidence), `substantive(url, label, passage) -> float`. Every text
  sent is ≤ 150 words (assert in code; callers pass `window()`/`opening()` output).
- Wording (verbatim: cache keys hash it):
  - CULTURE: "`text` is the title and opening of an article in a Catholic newspaper or magazine. Is the article mainly about a work of culture (a film, television series, book, poem, piece of music, album, painting, sculpture, building or play) or about an artist, rather than news, liturgy, politics or Church affairs?"
  - PICK: "`text` is a passage of an article that names `mention`. Which of these Wikidata items does the passage mean?" with one criterion per QID named `"<label> — <description>"` and `"none": {"name": "None of these"}`.
  - SUBSTANTIVE: "`text` is a passage of an article that names `mention`. Does the article discuss this work or person substantively (what it says, means or shows), rather than naming it in passing?"

- [ ] **Step 1: Write the failing tests** (a fake TypeSafe client counting calls)

```python
def test_answers_are_cached_per_article(): ...      # same url+text twice -> one client call; jev row qid == article_key(url)
def test_pick_none_returns_none(): ...
def test_text_over_150_words_is_refused():
    with pytest.raises(ValueError): PressJudge(fake, q).culture("u", " ".join(["w"] * 151))
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_judge.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** on `CachingClient(client, queue, article_key(url))` with `Noul`/`Choice` as in `pipeline/judge.py`.
- [ ] **Step 4: Run** — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/judge.py tests/test_press_judge.py && git commit -m "press: Jev questions for articles"`

### Task 6: Linking mentions to Wikidata, and domain classes

**Files:**
- Create: `pipeline/press/link.py`
- Modify: `pipeline/domains/film.py`, `pipeline/domains/literature.py` (add `WORK_CLASSES` and `owns`)
- Test: `tests/test_press_link.py`

**Interfaces:**
- Consumes: `Page`, `Span`, `window`, `passage` (Task 4); `PressJudge` (Task 5); `PressStore.add_mention` (Task 1);
  `WikidataClient.search(text, lang) -> list[str]`, `.by_sitelink(site, titles) -> dict`, `.entity(qid)`;
  from the prerequisite: `common.owns(instance_of: Iterable[str], work_classes: Iterable[str], sparql, cache: Path | None = None) -> bool`; `music.owns` / `art.owns`
  (`owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool`) when those modules exist.
- Produces: `film.WORK_CLASSES: tuple[str, ...] = ("Q11424", "Q5398426")` and `literature.WORK_CLASSES: tuple[str, ...] =
  ("Q7725634", "Q47461344", "Q1667921", "Q277759")` (literary work, written work, and the config's `series_classes`);
  `film.owns` and `literature.owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool` =
  `common.owns(instance_of, WORK_CLASSES, sparql, cache)`;
  `class_route(instance_of: list[str], sparql, cfg, cache: Path | None = None) -> str | None` (the first module in
  `DOMAINS` order whose `owns()` is True → its name; else the first `waiting_classes` entry, for a domain absent from
  `DOMAINS`, with `common.owns(instance_of, classes, sparql, cache)` True → `"waiting:<name>"`; else None);
  `instance_of` is the hit's P31 from `wikidata.entity(qid)`; `link_article(page, outlet, store,
  judge, wikidata, sparql, cfg) -> list[Mention]` (route `None` until Task 7).
- Rules: a `wikilink` span resolves through `by_sitelink`; other spans through `search` in the page's lang, then en.
  Hits whose class routes nowhere are discarded. One routed hit: taken. Several: `pick`, taken only at
  `p ≥ pick_p and confidence ≥ pick_confidence`. Then `substantive(window)`; kept at `≥ mention_keep`, with up to
  three `passage()`s from the paragraphs naming the label. `kind` = `"person"` for a `Q5` hit, else `"work"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_wikilink_resolves_without_search(): ...
def test_one_typed_hit_is_taken_without_jev_pick(): ...
def test_ambiguous_title_is_dropped_below_the_cut():  # two film hits, pick p=0.7 -> no mention
def test_passing_mention_is_dropped():                 # substantive 0.5 < 0.60 -> no mention
def test_routes_music_art_and_people_to_waiting():
    # DOMAINS = film, literature only; closure fake: Q7366 ⊂ music, Q3305213 ⊂ art, Q5 ⊂ artists
    assert [class_route(p31, fake_sparql, cfg, tmp_path / "c.json") for p31 in (["Q7366"], ["Q3305213"], ["Q5"])] == \
        ["waiting:music", "waiting:art", "waiting:artists"]
def test_a_present_module_takes_its_items(): ...      # monkeypatch DOMAINS with a fake music module whose owns() is True -> "music"
def test_film_wins_over_a_broader_class(): ...         # P31 both film and painting -> "film" (DOMAINS order)
def test_at_most_three_passages_each_25_words(): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_link.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement**; no SPARQL of its own: class membership comes only from `owns()` and its cached closure.
- [ ] **Step 4: Run** — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/link.py pipeline/domains/film.py pipeline/domains/literature.py tests/test_press_link.py && git commit -m "press: link mentions to Wikidata and route by class"`

### Task 7: Routing into the queues, and the floor exemption

**Files:**
- Create: `pipeline/press/route.py`
- Modify: `pipeline/domains/common.py` (add `press_exempt`), `pipeline/domains/film.py:127-` and
  `pipeline/domains/literature.py:463-` (use it in `triage`)
- Test: `tests/test_press_route.py`, additions to `tests/test_pipeline_film.py`, `tests/test_pipeline_literature.py`

**Interfaces:**
- Consumes: `Mention`, `PressStore` (Task 1); `stages.covered(row, repo, wikis)`; `Queue.add`; `WikidataClient.entity`.
- Produces: `route(store, queue, repo, wikis, wikidata) -> dict[str, int]` (counts per route; for each of
  `store.unrouted()`: a provisional `Row` from the entity's labels and Wikipedia sitelinks → `covered()`: `"drop"` or
  `"translation"` (covered somewhere) → `"enrichment"`; else a domain in `DOMAINS` → `queue.add(qid, domain,
  f"press:{outlet}", labels, sitelinks, data={"press": {outlet: p}})` → `"candidate"`; `waiting:*` → `"waiting"`);
  `release_waiting(store, queue, domain: str, wikidata) -> int` (adds the `waiting:<domain>` mentions as candidates;
  the hook for the music, art and artist domains, called by `cmd_candidates`); `common.press_exempt(row,
  threshold=0.60) -> bool` (True when `max(row.data[0]["press"].values()) ≥ threshold`).

- [ ] **Step 1: Write the failing tests**

```python
def test_covered_item_goes_to_enrichment_not_the_queue(): ...
def test_new_film_becomes_a_candidate_with_a_press_source():
    # queue.row("Q9").sources == ["press:sir"]; data[0]["press"] == {"sir": 0.8}
def test_existing_candidate_gains_the_source(): ...
def test_music_waits_until_release(): ...           # route -> "waiting"; release_waiting(..., "music") adds it
def test_triage_exempts_a_kept_press_mention():     # film and literature: press p 0.6, 3 sitelinks -> pass
def test_triage_floor_applies_below_the_exemption(): # p 0.55 (mention_keep lowered in config) -> floor applies
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_route.py tests/test_pipeline_film.py tests/test_pipeline_literature.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** In film's `triage`, the floor applies when the sources are only `facet` and `press:*` and `not press_exempt(row)`; in literature's, `press_exempt(row)` joins `DIRECT` as a reason to skip the floor.
- [ ] **Step 4: Run** — Expected: PASS; full suite green (existing triage tests unchanged).
- [ ] **Step 5: Commit** `git add pipeline/press/route.py pipeline/domains/common.py pipeline/domains/film.py pipeline/domains/literature.py tests/test_press_route.py tests/test_pipeline_film.py tests/test_pipeline_literature.py && git commit -m "press: route mentions to queues, enrichment or waiting; floor exemption"`

### Task 8: Press notes in the dossier and on proposals

**Files:**
- Modify: `pipeline/__main__.py:_stage_functions` (the stage-4 callable)
- Create: `pipeline/press/notes.py`
- Test: `tests/test_press_notes.py`, addition to `tests/test_pipeline_publish.py`

**Interfaces:**
- Consumes: `PressStore.mentions(qid=...)`, `Article` (Task 1).
- Produces: `press_notes(store, qid, outlets: dict[str, str]) -> list[dict]` (one note per mention:
  `{"lang", "url", "heading": "<outlet name> (<published>): <title>", "text": " … ".join(passages)}`, newest first);
  the stage-4 callable returns the domain's `(outcome, data)` with `data["notes"] = press_notes(...) + data["notes"]`.
- Ruling carried from the spec's storage rule: the dossier note holds only the stored passages (≤ 3 × 25 words); the
  article is not fetched again at stage 4.

- [ ] **Step 1: Write the failing tests**

```python
def test_press_notes_come_first_in_the_dossier(): ...
def test_note_text_is_only_the_stored_passages(): ...
def test_proposal_sources_list_the_press_url_first():   # via publish._params -> sources starts with the article url
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_notes.py tests/test_pipeline_publish.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/notes.py pipeline/__main__.py tests/test_press_notes.py tests/test_pipeline_publish.py && git commit -m "press: notes in the dossier and sources on proposals"`

### Task 9: The enrichment page and `{{SV press done}}`

**Files:**
- Create: `pipeline/press/enrich.py`, `templates/SV press done.wikitext`
- Modify: `config/templates.json` (register the template)
- Test: `tests/test_press_enrich.py`

**Interfaces:**
- Consumes: `PressStore.mentions(route="enrichment")`, `publish._write(en, title, build)`.
- Produces: `ENRICH_TITLE = "Semina Verbi:Press for existing articles"`; `done_urls(text: str | None) -> set[str]`;
  `enrichment(store, outlets, old_text) -> str`; `publish_enrichment(store, en, outlets) -> bool`.
- Page: an intro line ("Articles of the Catholic press on works SeminaVerbi already covers. Work an article into the
  entry, then add `{{SV press done|<url>}}` below to close it."), then a bot block between `<!-- press:begin -->`
  and `<!-- press:end -->` (per item, newest first: the item's label as `[[:<lang>:<title>]]` links for each wiki that
  has it, then one line per press article: outlet, date, `[url title]`, language, the passages in quotes), then
  whatever follows the end marker, kept as written. Entries whose URL is in `done_urls(old_text)` are omitted.
- Template: `{{SV press done|url}}` renders `✓ <url>` in small text and adds no category; registered with summary
  "Closes an entry of the press enrichment list." and param `1: the article URL`.

- [ ] **Step 1: Write the failing tests**

```python
def test_enrichment_lists_newest_first_with_passages(): ...
def test_enrichment_keeps_done_markers():
    old = "intro\n<!-- press:begin -->\nx\n<!-- press:end -->\n{{SV press done|https://x/a}}\nnote by editor\n"
    text = enrichment(store_with("https://x/a", "https://x/b"), outlets, old)
    assert "https://x/a" not in text.split("<!-- press:end -->")[0]
    assert text.endswith("{{SV press done|https://x/a}}\nnote by editor\n")
def test_unchanged_page_is_not_rewritten(): ...      # FakeEn from test_pipeline_publish: no edit when equal
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_enrich.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement**; also run `.venv/bin/python -m tools.skilldocs` so the template docs regenerate, and include its output files in the commit.
- [ ] **Step 4: Run** — Expected: PASS; full suite green.
- [ ] **Step 5: Commit** `git add pipeline/press/enrich.py "templates/SV press done.wikitext" config/templates.json <skilldocs outputs> tests/test_press_enrich.py && git commit -m "press: enrichment page and {{SV press done}}"`

### Task 10: CLI, outlets config, timer, docs and the Press bot page text

**Files:**
- Create: `pipeline/press/stages.py`, `deploy/systemd/sv-pipeline-press.service`, `deploy/systemd/sv-pipeline-press.timer`, `docs/press-bot-page.wiki`
- Modify: `pipeline/__main__.py` (subcommand `press`), `config/press.json` (outlet entries), `README.md`, `docs/content-tasks.md`
- Test: `tests/test_press_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `python -m pipeline press discover|filter|link|route|enrich|run [--dry-run]`; `run` = discover → filter
  (culture `< culture_drop` → outcome `drop`) → link → route → enrich; `--dry-run` runs discover, filter and link,
  prints each mention's would-be route with its probabilities and the share hitting existing articles, and calls
  neither `route` nor the wiki. `enrich` and `run` without `--dry-run` log in through `_login_editor()`; filter and
  link need `TYPESAFE_API_KEY`. The report lists new articles, drops, mentions per route, and `client.refused`.
- Timer: `OnCalendar=*-*-* 04:30:00`, `Persistent=true`; service like `sv-pipeline-votes.service` with
  `EnvironmentFile=%h/.config/seminaverbi/pipeline.env` (holds `TYPESAFE_API_KEY`) and
  `ExecStart=… runeditor.py pipeline press run`.
- `config/press.json` outlets: `entries` from the 2026-11-23 recon (Vatican News and L'Osservatore Romano:
  `sitemap.<lang>.xml` for it, en, fr, de, es, pt; SIR, Roma Sette, Famiglia Cristiana, KTO: their RSS URLs; Aleteia:
  each language subdomain's `sitemap.xml`; Avvenire, the Register, La Civiltà Cattolica, EWTN: the builder follows
  their redirects or finds their feeds by fetching only feed and sitemap index files, and leaves `entries: []` with a
  `"note"` when none is reachable); `culture_paths` read off those index files' URLs.

- [ ] **Step 1: Write the failing tests**

```python
def test_dry_run_writes_nothing_to_queue_or_wiki(): ...   # candidates table unchanged; FakeEditor has no edits
def test_run_reports_refused_outlets(capsys): ...
def test_press_needs_the_jev_key_only_for_filter_and_link(): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_press_cli.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement**; write `docs/press-bot-page.wiki` (who reads: the SeminaVerbi pipeline of the BibleGet project, operated by the Catholic Digital Commons Foundation, a non-profit; why; how often (daily, at most 200 articles, one request per outlet every 10 s or the outlet's Crawl-delay); what is kept (link, title, date, outlet, language, quotations of at most 25 words; never the full text, never used for training); how long (as long as the entry or proposal cites it); how an outlet asks to be left out (the page's talk page, or a robots.txt rule for `SeminaVerbi-press`, which the bot obeys)). The page is **not** published by any code; it goes on the wiki only with the author's approval in session. In `README.md`, a "Catholic press" section (commands, timer, prerequisite). In `docs/content-tasks.md`, the open item: "TypeSafe's written confirmation that it does not train on API inputs: needed before enabling `sv-pipeline-press.timer`."
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` — Expected: all green. Then the manual check `.venv/bin/python -m pipeline press discover --dry-run` lists culture URLs per outlet and the refused ones (network; no Jev, no wiki).
- [ ] **Step 5: Commit** `git add pipeline/press/stages.py pipeline/__main__.py config/press.json deploy/systemd/sv-pipeline-press.service deploy/systemd/sv-pipeline-press.timer docs/press-bot-page.wiki README.md docs/content-tasks.md tests/test_press_cli.py && git commit -m "press: CLI, outlets, timer and docs"`
