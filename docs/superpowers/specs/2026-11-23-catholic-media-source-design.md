# Article pipeline: Catholic media as a source (design)

Builds on the pipeline of `2026-11-20-article-pipeline-film-design.md` (stages 0–6, the queue, Jev through
`pipeline/judge.py`, the ranking page) and on literature's design. Music and art (a parallel design) and a shared
source for artists (later) plug into what this one produces; neither is built here.

## Intent

Today the pipeline reads only Wikidata, Wikipedia and Wikiquote, so a proposal rests on what Wikipedia says about a
work. The Catholic press reviews films, books, music and art from the faith's own point of view, which is exactly
the reading a Seeds of the Word article needs. This source reads eleven Catholic outlets (a twelfth since: see the last section), finds the articles that
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
  `user_agent`, `delay_seconds`, `max_articles_per_day`, the Jev cut-offs below.
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
   A second `Noul` then asks for a faith or values angle (`angle_drop`; see "Angle and stance" below).
3. **Link.** Mentions are found in the article's markup and text: italicised or quoted titles, links to Wikipedia,
   and capitalised names next to a creator's name. Each mention is searched on Wikidata (`wbsearchentities` in the
   article's language, then en) and its hits are typed (P31/P279 against the domains' classes, Q5 for people).
   One or several hits of a known type: one Jev `Choice` among them and "none", given the article passage; kept only
   at ≥ 0.80 with confidence ≥ 0.60, else dropped as ambiguous or, for "none", as no matching item. Then one Jev `Noul` per linked mention: does the
   article discuss this work or person substantively, not in passing? Below `mention_keep` (start 0.60): dropped.
   The passages kept are the paragraphs that name the item, cut to ≤ 25 words around the mention.
4. **Route.** Each kept mention goes:
   - to the **enrichment list** when stage 1's `covered()` finds a SeminaVerbi article for the item (covered on any
     wiki);
   - to its domain's queue otherwise, with `queue.add(qid, domain, "press:<outlet>", …)` (an existing candidate just
     gains the source);
   - to **waiting** when its domain has no module yet (`waiting:music`, `waiting:art`, `waiting:artists`); when
     the domain lands, its first run takes them (the hook for music, art and artists), with the same coverage
     decision: a work SeminaVerbi already covers goes to the enrichment list, never to the queue.
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
At most `max_articles_per_day` (250) new articles a UTC day across outlets, round-robin, whatever the runs, so the first
backfill spreads over days. Domain stages 1–6 still run as now, by hand (`run --publish`).

## Calls made for the builder (the author may overturn any)

- Storage in sibling tables of the same queue; article Jev answers keyed `press:<sha1(url)>`.
- Jev only judges (Choice/Noul): it cannot write names, so mentions come from markup and Wikidata search, and Jev
  filters, disambiguates and weighs them.
- Cut-offs to start: `culture_drop` 0.40, disambiguation 0.80/0.60, `mention_keep` 0.60. A calibration run on the
  existing articles' press coverage tests them before anything is posted.
- 25-word quotations, at most three per mention.
- Timer daily at 04:30, 250 articles per day (200 per run until the author raised it, 2026-11-23), 10 s between requests per outlet.
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
   reported, never written around. What follows from a refusal: "Outlets that refuse us" below.

## Outlets that refuse us (author, 2026-11-23)

The author's decision: "If a bot is refused access to a site, then the bot should stop reading that site for that day;
any new articles should still be available the day later. If a bot is refused continuously for say a week, then the
site should be removed and no longer consulted".

- **A refusal** is a 401, 403 or 429 from the outlet (an article or feed, redirects followed) or from its own robots.txt.
  It is not a redirect target's robots.txt, a disallowed path, a timeout, a 5xx or an unreachable host (an unreachable
  robots.txt stops the outlet for that run only, as before).
- **The day** is the UTC calendar day. The refusal is kept in `press_refusals` (outlet, day, reason); for the rest of that
  day every run, of any action, asks the outlet nothing, robots.txt included. The day after it is tried again. Articles
  not read stay `found`, unattempted; feeds not read to the end keep their old validators, so nothing is missed.
- **A refused day** (toward removal) is one with a refusal and no successful response (2xx or 304) other than robots.txt.
- **Removal:** at seven refused days with no successful day between them. Each is a different calendar day, so they span
  seven days at least. A day with neither a refusal nor a success (no run, or only errors) neither counts nor breaks the
  sequence. Any successful day starts it again.
- **Removal is state** (`press_outlets`: last successful day, removed on, since, reason, reinstated on), never an edit of
  `config/press.json`. A removed outlet is never asked again, until `press reinstate OUTLET`, which starts the count again
  but leaves today's refusal, if any, standing for today.
- **Seen:** every run that reads outlets ends with `press refused:` and `press removed:`, with since when; `press outlets`
  lists both without asking anything.
- **Dry runs:** `run --dry-run` keeps refusals in the store it is given (a copy). `discover --dry-run` honours the real
  store's state and writes none, so a refusal it meets does not stop a real run later that day.

## The day's limits and robots.txt (author, 2026-11-23)

- **Limits are the UTC day's**, shared by every run: `max_articles_per_day` and each outlet's `max_per_day`, for the
  articles added (`found_at`) and, apart, for the articles read. An article is counted the first time that day that filter
  or link sets out to fetch it (`press_reads`, emptied of earlier days), failures included; never twice a day. Link's
  fetches of articles kept on earlier days count too, and filter leaves them room, at most half. Counted in one SQL
  statement, so runs at the same time share the budget.
- **robots.txt** is read as RFC 9309 says (`robots.py`): `*` and `$`, the longest match, Allow on a tie, our group (the
  groups of one name combined; `SeminaVerbi` counts as ours when `SeminaVerbi-press` is not named) else `*`, escapes
  normalised, 500 KiB, unreadable lines ignored, Crawl-delay kept. urllib.robotparser knew no wildcards and took the first
  matching rule.

## What each outlet gives (author, 2026-11-23)

The author's decision: "if no useful proposals come from a source within a month or two, we should consider dropping the
source". A report (`report.py`, `press outlets`), never an automatic drop.

- **Useful:** a queue row (no benchmark or calibration row) with press evidence from the outlet, its page made and its
  status open, approved or created. Not declined, not unpublished, not a part retired into its whole. Enrichment entries do
  not make an outlet useful; they are shown beside the mark.
- **Marks:** none useful 30 calendar days after the first successful read: review; 60: consider dropping. Days without a run
  are not treated apart. One useful proposal and the outlet is never marked again.
- **First read and days read** are stored (`press_outlets.first_read`, `days_read`: 2xx or 304 other than robots.txt). A store
  made before takes them from its articles' `found_at` and its last successful day.
- **Credit:** where the article was read, not the outlet its evidence counts for in the queue: the question is what reading
  the source gives, and most of Vallini's posts count as L'Osservatore Romano. Such candidates are told apart on his line.
  The same article found in both outlets is credited to both. Enrichment entries are counted by work.
- Never read (no entries, or never answered), removed, or no longer configured: shown as such, not marked.

## Open: retention (author, 2026-11-23: "We'll cross that bridge when we get to it")

Nothing is deleted today, and the bot's page says so. A rule matching "as long as a SeminaVerbi article or an article
proposal cites it" would delete, for every article that no mention with route `candidate` (of a queue row not dropped or
declined), `enrichment` or `waiting` points to, its mentions, fingerprint, cached Jev answers (`press:<sha1>`) and the
title, date and scores of its row, keeping the bare URL (or a hash of it) so that it is not discovered, fetched and judged
again. It would do so some weeks after the article was filtered or linked (to allow a redo, and a copy to be recognised),
and, for an article cited until then, when its last citing row is dropped or declined, its enrichment entry is marked
done, or the SeminaVerbi article no longer links it, which is the one thing the pipeline cannot see today.

## Precision (2026-11-23 dry run)

The first `press run --dry-run` (200 articles from 9 outlets, 65 kept, 60 linked, 25 mentions routed) showed five
problems. What changed:

1. **Transient failures.** WDQS dropped a connection during routing and the whole run exited 1; Wikidata search
   answered `cirrussearch-too-busy-error` on five articles, which stopped linking them part-way. The SPARQL client
   retries a dropped connection like a read timeout, and the Wikidata client retries dropped connections, timeouts
   and a busy search, both with sync.wiki's bounded backoff. In `press route` (and its dry-run preview) a lookup
   still failing after the retries is reported and leaves that one mention unrouted for the next run.
2. **Section pages.** Famiglia Cristiana's sitemap lists its sections (`/cultura-e-spettacoli/musica`, `/libri`,
   `/arte`, `/cinema-serie-tv-streaming`, `/sanremo-2026`), read like articles. Measured on one article and one
   section page of each of the nine outlets: every article's JSON-LD names an article type among its own nodes
   (`NewsArticle`, `Article`, `BlogPosting`; KTO's programme pages `VideoObject`), no section page's does but KTO's
   home page (never discovered: its feed lists only programme pages), while og:type misleads (L'Osservatore
   Romano's articles say `website`). The node that describes the page (by `url`, `@id` or `mainEntityOfPage`)
   decides, or its one `mainEntity`; articles another node lists do not count. Only evidence rejects (a collection
   type describing the page, or articles only as listed members): inconclusive JSON-LD leaves it to og:type, except
   breadcrumbs alone without og:type (Avvenire's sections), which are a section. A page with no signal, or
   with a block that cannot be read, counts as an article. A page that is not one is skipped for good ("not an
   article"). JSON-LD is read leniently: one Osservatore article has a raw carriage return inside a string. No
   outlet needs a rule of its own.
3. **A sole hit was taken unasked.** "free climbing" became a 1986 film, "it takes a village" Hillary Clinton's
   book. A sole search hit now goes through the same `pick` (that item or none, at 0.80/0.60); a Wikipedia link
   still names its item itself. A rejected pick may be asked again where the title recurs in another window.
4. **The substantive question** now names the item (label and Wikidata description) and asks whether that work or
   person is one of the subjects the article discusses, rather than listed, named in passing, used as a comparison,
   or meant in another sense by the same name.
5. **Scholarly articles reached literature**: Wikidata files scholarly literature under literature, a literary work.
   `literature.owns` stops at scholarly literature (Q68200780).

**Before and after**, on the same 65 articles (mentions cleared, `press link` and `press route --dry-run` again):

| | before | after |
|---|---|---|
| articles linked | 60 (4 of them section pages; 5 more articles stopped part-way by a busy search) | 60 (the 5 sections skipped; one HTTP 503, linked on a second run) |
| mentions routed | 25 | 31 |
| earlier mentions kept / dropped | | 15 / 10 |

- Kept, as hoped: *Vous n'aurez pas ma haine*, *A Silvia*, *Le notti bianche*, *Una pura formalità*, *Gaza Surf
  Club*, *I bambini di Gaza* (p 0.71–0.97).
- Dropped, as hoped: free climbing and *It Takes a Village* (Jev picks none), the scholarly article (stop class),
  the two mentions of section pages, a wrong *GTA VI* item, *Dragons*, *Lumière intérieure*, *Vendetta privata*
  (now "no matching item"). Lost although right: *Ballando con le stelle* (the sole hit, picked at 0.74 with
  confidence 0.48, under the cut).
- **Not fixed:** the Pattinson casting article still keeps its Bond titles and *The Batman* (p 0.76–0.97), and the
  La Salette article Botticelli's *Birth of Venus* (0.80). Once the article was linked to the end (the busy search
  had stopped it), it yielded 17 mentions, among them Pattinson's filmography (*Tenet*, *Mickey 17*, *Cosmopolis*,
  …, p 0.63–0.78). Each window Jev reads is a paragraph of a Bond history or a filmography, so within it the film
  *is* what the paragraph is about; the question sees 150 words, not the article. The kept good finds (0.71–0.97)
  and the kept bad ones (0.62–0.97) overlap, so no `mention_keep` separates them: it stays 0.60. Paragraph count
  does not separate them either (*A Silvia* and *Vous n'aurez pas ma haine* are named in one paragraph). Fixing this
  needs an article-level signal (for example the article's title given to the substantive question, or a cap on
  mentions per article), which is the author's call rather than wording tuned to this sample.

## Angle and stance (author, 2026-11-23)

Decided by the author: "Yes the article filter should ask for a faith or values angle, because an article could be
critiquing an author or work that doesn't belong in SeminaVerbi, so we need the context to pick it up as a
candidate".

1. **Angle (filter).** An article at or above `culture_drop` is asked, as a question of its own, whether it treats the
   work or artist with a faith or values angle, or through the human questions the Gospel addresses (mercy,
   forgiveness, suffering, hope, justice...), favourably or critically, rather than as entertainment or industry
   news. The angle may be implicit. Jev reads the title and opening; below the cut, at most three texts of the title
   and the paragraphs around a mention (never other parts of the article), and the best score counts. Both that and
   the opening's score are stored. `angle_drop` 0.40, as `culture_drop`: a drop is for good, while an article kept
   wrongly still meets the pick, the substantive question, the stance and the votes.
2. **Stance (link).** Each kept mention is asked, over at most three windows of at most 150 words with the title, what
   the article makes of the item (its label and description) in the light of the Gospel: a Jev `Choice` of `bearing`
   (finds something that bears on the Gospel), `mixed` (that, and criticism), `neutral` (neither) and `critical` (only
   criticism). `stance_keep` 0.50 applies to the best window's bearing + mixed: the four probabilities sum to 1, so
   above half means that finding something is likelier than not, and the best of three windows is already lenient.
   Otherwise the outcome is `critical` when a window gives that 0.50 or more, else `neutral`.
   - Bearing and mixed may become candidates. Neutral and critical may not (route `dropped`): no queue evidence, no
     sitelink-floor exemption, no dossier note. This holds for mentions released from `waiting` too.
   - For a work SeminaVerbi covers, every mention goes to the enrichment list; only `critical` is marked there, since
     it can inform the discernment section.
   - Existing stores gain the columns by a guarded ALTER. A mention without an outcome (never asked, or with the first
     version's single score) stays as it is and is not critical.

The first version asked one score ("finds something that bears on the Gospel rather than only criticising") and took
a low one as critical. On real pages it marked *Heidi* (0.07) and *Jeeg* (0.08), named in a list in an article on the
Panini album, as critical: neutral mentions.

**Replay of the dry run's 200 articles** (the single-score version, f2b8251, with the page detection of 6b2d257):
129 dropped by culture (the same 129 as before), 13 dropped by angle, 11 section or home pages skipped (6 of them
among the 135 the culture filter had dropped, 5 among the 65 kept), 2 Avvenire articles not read (HTTP 503), 45 kept
and linked, 12 mentions (31 before). Of the 58 asked for the angle the opening showed it for 42, a later text for 3.
The Pattinson article went at the angle (0.08), and its Bond titles and *The Batman* with it. No linked mention was
critical (the one article that looks critical by its title, on the Tg1, fell at the angle, 0.37), so **the `critical`
and `mixed` outcomes are untested on real pages**. L'Osservatore Romano's pages were read with garbled accents
(served without a charset; fixed in 8321aa5 on another branch), so their verdicts are provisional.

**Stance asked again** with the Choice for those 12 mentions: 8 bearing, 4 neutral (*Heidi*, *Jeeg*, *The Birth of
Venus* 0.33, and *Una pura formalità* 0.31, a wanted find), none critical or mixed. A cut of 0.40 gives the same
outcomes. The combined code has not had a full run.

## Gaetano Vallini's blog (2026-11-23)

Added by the author as a twelfth outlet, `vallini`: https://gaetanovallini.blogspot.com/ ("Camera con vista"), where a
journalist of L'Osservatore Romano republishes his articles. Only the labels Cinema, Film, Musica, Letteratura, Libri
and Romanzo. Checked with our user agent on 2026-11-23, on 53 posts (2008–2025) of the feeds.

- **Discovery.** Blogger's label feeds, `orderby=updated` (newest change first), cut with `updated-max` (inclusive;
  the cut is asked a second later so that either reading overlaps). Offsets are never used. Per feed the store keeps
  (`press_walks`) the date the archive has been read down to and a mark; every run first reads from the top down to the
  mark, then goes on with the archive. Both move only once every entry above them is taken, so an interrupted run
  re-reads at most the pages since, and known URLs are dropped. A new, edited, backdated or newly labelled post has
  changed, so it comes to the top. Conditional requests only for the top page, whose validators are kept only after a
  complete pass. More posts changed in one second than a page holds: the page is enlarged (to 400), then reported.
- **Reading.** The pages are a Blogger dynamic view: no text without script, also with `?m=1` and `?v=0`. Each post is
  read from its own feed entry (`?path=`). Its content is paragraphs in any block element or none; blocks and double
  line breaks end a paragraph. HTML of any outlet served without a charset (L'Osservatore Romano's) is now read as
  UTF-8 when it is UTF-8: it was read as Latin-1, with garbled accents.
- **Cap.** `max_per_day` 40, on articles added and read in a UTC day. The outlet's share of a run (at most half of what the day has left) is attempted
  first; its failures count toward the cap and go behind its untried articles.
- **Attribution.** 47 of the 53 posts state the newspaper's issue, in a paragraph of its own (2022 on) or after © or
  "Copyright" (before), with the date as "8 novembre 2025", "21-22 ottobre 2019" (the first day is taken),
  "18.01.2008" or "16/17.1.2006". Those forms and no other count; the newspaper named in the text does not (a 2011 post
  quotes "«L'Osservatore Romano» del 13 ottobre"), nor do credits of different dates. The rest is the blog's own.
- **Duplicates.** At extraction, for this pair of outlets only, the 64 smallest hashes of the text's 5-word runs are
  kept; their overlap estimates the text in common. One article: overlap ≥ 0.50; or ≥ 0.25 with the same title (three
  words or more) and the post's stated issue within three days of the newspaper's date. Below about 100 words there is no
  fingerprint and no merge. On the real posts, 1378 pairs of different articles overlap by 0.016 at most; the same text
  with 3% of its words changed overlaps by 0.56 at least (always found), with 5% 0.44 at least (50 of 53 found by the text,
  all with title and date), with 8% by 0.38 (20 of 53 by the text). No original of a saved post was at hand, so finding
  a pair was tested only on synthetic texts and on edited copies.
- **What follows from a duplicate.** The second copy, either way round, is not judged: skipped, "duplicate". The
  evidence counts for the newspaper in the queue (also for a post that states its issue but whose original was not
  found), the dossier has one note and the enrichment page one entry, with both links; closing either URL closes it.
  A row's per-outlet press evidence (and its `press:` sources) is always recomputed from its supporting mentions (routed as
  candidates, not neutral or critical) under the outlet each counts for now, when a mention is routed and after filter,
  link and route (never in a dry run). On a pending row a change rebuilds the dossier, and triage again when it gained or
  lost the exemption from the sitelink floor; with no support left the exemption is gone, and the last `press:` source
  stays only on a row with no other, as what found it. On a row with a status only the evidence is corrected: its outcomes,
  dossier, proposal and status stay. Benchmark rows are not triaged again. `press outlets` does not read this map: it
  credits the outlet where the article was read.
- **Known limits.** Missed: originals for subscribers only (no text, so no fingerprint), partly shown or heavily edited
  ones, posts that gather several of the paper's articles (one is cited under Vallini whole), articles filtered before
  fingerprints existed. A false merge needs half the text in common: an article reprinted with an update. A copy that
  changes no evidence (its post already counted for the newspaper) adds its link when the dossier is next rebuilt, and one
  found once a row has a status never changes that row's proposal. The author is not checked: a post
  with another's article is attributed to Vallini.
- **Kept, beyond the list above:** the fingerprint (hashes only; no text can be read from them).
