# Article pipeline: Catholic media as a source (design)

Builds on the pipeline of `2026-11-20-article-pipeline-film-design.md` (stages 0–6, the queue, Jev through
`pipeline/judge.py`, the ranking page) and on literature's design. Music and art (a parallel design) and a shared
source for artists (later) plug into what this one produces; neither is built here.

## Intent

Today the pipeline reads only Wikidata, Wikipedia and Wikiquote, so a proposal rests on what Wikipedia says about a
work. The Catholic press reviews films, books, music and art from the faith's own point of view, which is exactly
the reading a Seeds of the Word article needs. This source reads eleven Catholic outlets, finds the articles that
discuss a work (or an artist), and uses them in three ways:

1. **Discovery:** a work an outlet discusses becomes a candidate, or an existing candidate gains a source.
2. **Support:** the article goes into the work's dossier; Jev judges with it, and the proposal page cites it.
3. **Enrichment:** an article about a work SeminaVerbi already covers is listed for the editors, so the existing
   entry can be enriched with what the press said.

Success: proposals whose evidence includes what L'Osservatore Romano or Avvenire wrote about the work, and an
editors' list of press articles on the works SeminaVerbi already has.

## Decided by the author (2026-11-23)

- **Outlets:** L'Osservatore Romano, Vatican News, Avvenire, Roma Sette, EWTN, La Civiltà Cattolica, Famiglia
  Cristiana, Aleteia, National Catholic Register, KTO, SIR (agensir.it). The list lives in config, so outlets are
  added or removed there.
- **Role:** find candidates *and* support them (articles in the dossier, cited on proposal pages), plus the
  enrichment list for existing entries.
- **Vatican outlets and EWTN** are read under their `Content-Signal: search=yes, ai-train=no, use=reference`: as
  reference use, under the pipeline's own honest user agent. Their robots.txt blocks named AI crawlers and allows
  `*`; the pipeline never uses one of those names, never poses as a browser, and obeys every `Disallow` path.
- **What is kept** of an article: link, title, date, outlet, language, and short quoted passages. Never the full
  text; never used for training.
- **Linking:** each work or artist is matched to its Wikidata item; an ambiguous match is dropped, not guessed.
- **Domains:** film and literature now; music and art when their domain lands; artists later (a hook here).
- **Scope:** new articles from now on, plus a backfill of whatever each outlet's sitemap or feed still lists.
- **Architecture:** one shared source for every domain (option B of the conversation), not a module per domain.

## Goals

- A `press` stage family, shared by all domains, that discovers articles, keeps only culture, links mentions to
  Wikidata, and routes each mention to a domain's queue, to the enrichment list, or to a waiting list.
- Press passages in the stage-4 dossier of any candidate they mention, and cited among a proposal's `sources`.
- A generated enrichment page on en.
- A user timer that runs discovery and linking daily, bounded in requests and in Jev spend.

## Non-goals

- Music, art and artist domains (their own designs; this one records their mentions until they exist).
- Reading paywalled or logged-in content, comments, PDFs of print editions, or video and audio.
- Summarising articles, or writing article text from them (stage 7 stays an editor session).
- Outlets beyond the configured list; social media.

## Anti-goals (a failure even if it works)

- Storing or republishing an outlet's article text beyond short attributed quotations.
- Any request that hides who is reading: a browser user agent, a rotated identity, ignoring robots.txt or a 403.
- A flood of proposals for works an outlet only mentioned in passing (a film named in a homily report).
- Wrong links: an article about Tolkien's *The Hobbit* attached to the film, or to another work of the same title.
- Paying Jev again for an article already judged.

## Constraints

- Everything runs on the workstation, like the other stages; only publishing writes to the wiki, and only to the
  project namespace on en, as **SV editor bot**.
- HTTP: one request at a time per outlet, at least `delay_seconds` apart (default 10), obeying a robots.txt
  `Crawl-delay` when larger; robots.txt re-read each run; a 401/403/429 stops that outlet for the run and is
  reported, never retried around. Conditional requests (`If-Modified-Since`/ETag) for feeds and sitemaps.
- User agent: a config value naming the project and a generic contact URL, e.g.
  `SeminaVerbi-press/1.0 (+https://en.seminaverbi.bibleget.io/wiki/Semina_Verbi:Press_bot)`. No personal name or email (decision 3 below).
- Quotations: at most 25 words each (the pipeline's `EXCERPT_WORDS`), always with outlet, title, date and link.
- `TYPESAFE_API_KEY` from the environment, never printed. Jev answers are cached as everywhere else.
- Test fixtures are synthetic feeds, sitemaps and article pages written for the tests, never copies of real articles.

## Approaches considered

- **A. Press as one more source inside each domain's `candidates()`:** each domain reads the outlets itself. Rejected:
  eleven outlets fetched and parsed once per domain, and nowhere to record a mention whose domain does not exist yet.
- **B. One shared press source feeding every domain (chosen by the author).** Articles and their mentions live in
  their own tables; each mention is routed to the domain whose classes its item belongs to.
- **C. Press only as dossier evidence for candidates found elsewhere:** simplest, quotes least, but finds nothing new.
  Rejected by the author's "find candidates and support them".

## Design

### Layout

- `pipeline/press/` (package): `outlets.py` (discovery per outlet: feed or sitemap, culture filter), `fetch.py`
  (the polite HTTP client), `extract.py` (article text, title, date, language; mention spans), `link.py`
  (Wikidata matching and domain routing), `stages.py` (the press stages below).
- `config/press.json`: the outlets (id, name, base URL, languages, discovery entries, culture filter, `since`),
  `user_agent`, `delay_seconds`, `max_articles_per_run`, the Jev cut-offs below.
- `deploy/systemd/sv-pipeline-press.{service,timer}`.
- `python -m pipeline press discover|filter|link|route|enrich|run` (one sub-stage each, plus `run`).

### Storage (builder's call)

Two sibling tables in the same SQLite queue, so a press row and a candidate row are joined by QID:

- `press_articles`: `url` (key), `outlet`, `lang`, `title`, `published` (date), `found_at`, `stage`, `outcome`,
  `data_json` (culture probability, reason for a drop). No body text.
- `press_mentions`: `url`, `qid`, `label` (as written in the article), `kind` (`work` or `person`), `domain` (or
  `waiting:<domain>`), `p` (Jev's probability that the article is about it), `passages` (up to three quotations of
  ≤ 25 words, each with its paragraph index), `route` (`candidate`, `enrichment`, `waiting`, `dropped`).

Jev's answers for an article go in the existing `jev` table with `press:<sha1 of url>` in its `qid` column, so the
cache and its hashing stay as they are.

### Press stages

Article text is fetched when a stage needs it and kept in memory for that run only.

1. **Discover.** For each outlet, read its configured entries (RSS/Atom feeds, sitemap indexes, news sitemaps),
   keeping URLs under its culture paths and on or after its `since` date. New URLs enter `press_articles`.
   Backfill is simply the first run: whatever the entries list. Per-outlet notes from the recon of 2026-11-23:
   Vatican News and L'Osservatore Romano publish a sitemap index per language; SIR, Roma Sette, Famiglia Cristiana
   and KTO publish RSS; Aleteia has sitemaps per language subdomain; Avvenire and the Register redirect their
   sitemap URL and have no `/rss` or `/feed`; La Civiltà Cattolica answers 403 to `/feed/` for our agent; EWTN's
   sitemap lists its television programmes (its news is on sister sites the author may add). The builder settles
   each outlet's entries and culture paths in `config/press.json` and lists what was not reachable.
2. **Filter (culture).** One Jev `Noul` on the article's title and its first ~150 words: is this article mainly about
   a work of culture (film, series, book, music, art, theatre) or an artist? Below `culture_drop` (start 0.40)
   the article is dropped and never fetched again.
3. **Link.** Mentions are found in the article's markup and text: italicised or quoted titles, links to Wikipedia,
   and capitalised names next to a creator's name. Each mention is searched on Wikidata (`wbsearchentities` in the
   article's language, then en) and its hits are typed (P31/P279 against the domains' classes, Q5 for people).
   One hit of a known type: linked. Several: one Jev `Choice` among them, given the article passage; kept only at
   ≥ 0.80 with confidence ≥ 0.60, else dropped as ambiguous. Then one Jev `Noul` per linked mention: does the
   article discuss this work or person substantively, not in passing? Below `mention_keep` (start 0.60): dropped.
   The passages kept are the paragraphs that name the item, cut to ≤ 25 words around the mention.
4. **Route.** Each kept mention goes:
   - to the **enrichment list** when stage 1's `covered()` finds a SeminaVerbi article for the item (covered on any
     wiki);
   - to its domain's queue otherwise, with `queue.add(qid, domain, "press:<outlet>", …)` (an existing candidate just
     gains the source);
   - to **waiting** when its domain has no module yet (`waiting:music`, `waiting:art`, `waiting:artists`); when
     the domain lands, its first run takes them (the hook for music, art and artists).
5. **Enrich.** Regenerate `Semina Verbi:Press for existing articles` on en, owned by the bot like the ranking
   page: per article (by item), the wikis that have it, then each press article (outlet, date, title as link,
   language, the passages). Newest first; an entry an editor marks done (`{{SV press done|url}}` on the page, kept
   by the bot) leaves the list. (Builder's call; the page name is the author's to change.)

### What a press source changes downstream

- **Triage (stage 2):** `press:<outlet>` is a seed source, like the Vatican list: an outlet chose the work. A
  press-only candidate whose mention was kept at ≥ 0.60 is not held to the sitelink floor (decision 2 below).
- **Dossier (stage 4):** the domain's dossier gains one note per press mention of the item (outlet, language, URL,
  the passages), placed first, so Jev's themes and connection read the Catholic reading first. When the article
  is fetched again for this, Jev reads the paragraphs around the mention (≤ ~150 words), and only the passages are
  stored (decision 1 below).
- **Proposal (stage 6):** `sources` lists the press articles with their outlet, date and link, and a theme's
  evidence may be a press passage (still ≤ 25 words).
- **Ranking page:** unchanged, except that a proposal's line may show the outlets that discussed it (builder's call).

### Schedule

`sv-pipeline-press.timer`, daily at 04:30 (off the votes timer's three-hour marks), runs `press run` through
`runeditor.py` (the enrichment page is a wiki write) with `TYPESAFE_API_KEY` from the service's environment file.
Each run reads at most `max_articles_per_run` new articles (start 200) across outlets, round-robin, so the first
backfill spreads over days. Domain stages 1–6 still run as now, by hand (`run --publish`).

## Calls made for the builder (the author may overturn any)

- Storage in sibling tables of the same queue; article Jev answers keyed `press:<sha1(url)>`.
- Jev only judges (Choice/Noul): it cannot write names, so mentions come from markup and Wikidata search, and Jev
  filters, disambiguates and weighs them.
- Cut-offs to start: `culture_drop` 0.40, disambiguation 0.80/0.60, `mention_keep` 0.60. A calibration run on the
  existing articles' press coverage tests them before anything is posted.
- 25-word quotations, at most three per mention.
- Timer daily at 04:30, 200 articles per run, 10 s between requests per outlet.
- The enrichment page on en only, like the ranking page (the pipeline writes only en's project namespace).
- A press source counts as a seed for triage.
- An outlet that refuses us (403, robots) is skipped and reported, never worked around.
- **Routing by class:** each domain module exposes `WORK_CLASSES` and `owns(instance_of, sparql, cache=None)`, built
  on `common.owns` and its cached subclass closure, the interface of the music/art design (its plan's Task 1, which
  lands first as its own PR). `link.py` asks each present module's `owns()` in `DOMAINS` order; a mention of a
  domain with no module yet is held as `waiting:<domain>` by the classes in `config/press.json`. A new domain is
  routed to by adding its module. `covered()` is called with a provisional row built from the item's Wikidata
  sitelinks.
- **Calibration before routing:** `press run --dry-run` runs discover, filter and link on the first backfill and
  prints every route with its probabilities, and the share of mentions that hit an existing SeminaVerbi article;
  nothing enters the candidates queue or the wiki until the author has read it.
- **`{{SV press done|url}}`** is a small new template in `templates/` (renders nothing visible but a tick), synced
  like the others; the bot reads it back from the page to drop finished entries.

## Left to the builder

- Each outlet's discovery entries, culture paths and date parsing, in `config/press.json`.
- Article extraction (main text, title, date, language) per outlet's markup; a library such as `trafilatura` is
  fine if its licence allows (Apache-2.0).
- Mention detection rules and the Jev question wording, which calibration then tests.
- Test doubles for the outlets (synthetic fixtures), Wikidata and Jev, like the existing tests.

## Decided after the builder check (author, 2026-11-23)

1. **What Jev reads:** only the paragraphs around each mention, at most ~150 words per question (the culture filter
   reads the title and the first ~150 words). Never whole articles. **Prerequisite before the first live run (not
   before building):** TypeSafe's written confirmation that it does not train on API inputs, recorded in
   `docs/content-tasks.md` with its date.
2. **Press-only works and the minimum of Wikipedia languages:** exempt when the mention was kept at ≥ 0.60
   (`mention_keep`); a lower-scored press-only work is held to the domain's floor.
3. **Contact in the user agent:** the page `Semina Verbi:Press bot` on en: who reads (the SeminaVerbi pipeline of the
   BibleGet project, operated by the Catholic Digital Commons Foundation, a non-profit), why, how often, what is kept
   and for how long, and how an outlet asks to be left out. No personal email. Its text is part of the plan; it is
   published only with the author's approval in session, like any wiki edit.
4. **Outlets that refuse us** (a 401/403/429, a robots `Disallow`, La Civiltà Cattolica's 403 today): skipped and
   reported, never written around.
