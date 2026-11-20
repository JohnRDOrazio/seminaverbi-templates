# Article pipeline, phase 1: the shared core and film and television (design)

Issues: #20 (shared design, approved 2026-11-19), #21 (film and television). Literature (#24) is
phase 2 and gets its own design once phase 1 has produced real proposals.

## Intent

SeminaVerbi grows by articles on works and people that carry seeds of the Word. Today every article
starts from the author's memory. The pipeline finds candidates in open data, judges them with Jev,
and puts the promising ones on the wiki as **proposals** that trusted users vote on. A proposal that
reaches the vote threshold is approved, and the article is then written the way articles are written
now (item, page with `{{SV WIP}}`, link), in an editor session with the author.

Success for phase 1: a backlog of film and television proposals on en, each one with a connection
type, a field, themes with their evidence, and sources, good enough that the author would vote for
the strong ones and could reject the weak ones on the evidence shown; and a vote loop that promotes
proposals without the author touching it.

## Goals

- The shared stages 0–6 of #20, as plain Python in this repository, idempotent and resumable.
- Film and television sources and filters (#21) plugged into them.
- `{{SV proposal}}`, one proposal page per survivor, the ranking page, the portal pages, the vote
  threshold and the bot that promotes approved proposals.
- Calibration against the existing film and television articles before any new proposal is posted.

## Non-goals

- Writing articles without the author. Stage 7 stays an editor session.
- Literature and the other domains (later phases; the core must not need changes to add them, only a
  new domain module).
- Running anything heavy on the VPS.
- Monthly scans of new releases: phase 1 is the backlog. The monthly run is the same command with a
  date filter, added once the backlog is through.

## Anti-goals (a failure even if it works)

- Proposals that read as Wikipedia summaries, or that copy Wikipedia text or long quotations.
- Biblical imagery used for entertainment (an exorcism horror film) presented as a Gospel influence.
  Such a work may be proposed, but its connection type must say so.
- Paying Jev twice for the same question on the same candidate.
- Flooding the wiki with weak proposals: only candidates that survive stage 5 get a page.
- Importing items into the local Wikibase before a proposal is approved.

## Constraints

- Stages 0–6 run on the workstation (the OOM outage of 2026-11-16). Only stage 6 writes to the wiki,
  and only to the project namespace on en. Imports at stage 7 keep the 200 MB `seminaverbi_en` guard.
- Wiki writes as **SV editor bot** (`runeditor.py`), never as administrator; never print its password.
- Jev thresholds, as everywhere else: applied at probability ≥ 0.80 and confidence ≥ 0.60 for a
  field, ≥ 0.75 for a theme; a proposal lists a theme only at 0.50 or above.
- Licences: Wikidata (CC0) and the Wikipedia/Wikiquote APIs (read as notes) only. **No IMDb datasets
  and no TMDb in phase 1.** The popularity floor is Wikidata's sitelink count.
- Rate limits: Wikidata SPARQL queries batched, one at a time, with a descriptive generic
  User-Agent (no personal details); Wikipedia API calls throttled.
- `TYPESAFE_API_KEY` comes from the environment and is never printed.

## Approaches considered

- **A. Local queue, bot run from the workstation (chosen).** Candidates, Jev calls and verdicts in a
  local SQLite file; only stage 6 touches the wiki; the vote checker runs on a workstation timer.
  Approvals wait while the workstation is off; that costs hours, and is accepted.
- B. The same, with the vote checker as a cron job on the VPS: approvals always on time, but bot
  credentials and a scheduled job on the server that ran out of memory on 2026-11-16. Rejected.
- C. The wiki as the only store: no database, but rejected candidates would need pages or would be
  judged (and paid for) again on every run. Rejected.

## Design

### Layout

A package `pipeline/` at the repository root, beside `sync/` and `tools/`:

- `pipeline/queue.py`: the SQLite store (path `data/pipeline.sqlite`, git-ignored).
- `pipeline/stages.py`: the shared stages 1, 3, 5, 6 and the vote loop.
- `pipeline/domains/film.py`: stage 0 sources, stage 2 filters and the stage 4 dossier for film and
  television. A domain module exposes `candidates()`, `triage(row)` and `dossier(row)`; the shared
  stages call only those.
- `pipeline/__main__.py`: one command per stage, plus `run` (all stages in order) and `votes`.
- `config/pipeline.json`: the vote threshold (`3` to start; author's to raise), the sitelink floors,
  the Jev cut-offs for stages 3 and 5, the seed lists per domain.
- `templates/SV proposal/` in the usual template layout, synced to every wiki like the other SV
  templates (only en uses it).

### The queue

One row per candidate: Wikidata QID (primary key; a candidate without one is out of scope in phase 1),
domain, source(s) it came from, labels and sitelinks as fetched, the stage reached, the outcome of each
stage with its time, the dossier (as notes), and every Jev call (question, answer, probabilities,
confidence, time). Each stage processes only rows whose previous stage has an outcome and whose own
stage has none, so any run can be stopped and resumed, and nothing is judged twice. `--redo STAGE QID`
clears one row's stage for a deliberate re-run.

### Stages

0. **Candidates (film).** Wikidata, in three sources:
   - **seed lists**: the 1995 Vatican film list (the films linked from its en.wikipedia list page,
     resolved to QIDs), and films with `award received` (P166) the Prize of the Ecumenical Jury or a
     SIGNIS award; the Arts & Faith Top 100 is added if a machine-readable copy exists, otherwise
     dropped from phase 1;
   - **facets**: films (Q11424 and subclasses) and television series (Q5398426 and subclasses) with
     `main subject` (P921) or `based on` (P144) pointing to Jesus, the Bible or a book of it, a saint,
     a biblical figure or Christianity, or with genre (P136) Christian, biblical or religious film;
   - **calibration set**: the existing film and television articles (their items' Wikidata QIDs).
1. **Already covered?** Using the Wikipedia sitelinks the local items mirror: look up the
   candidate's Wikipedia sitelinks on the local Wikibase (`wbgetentities` by `sites`/`titles`); a
   local item that has a SeminaVerbi sitelink means covered. Also a title search on the six wikis.
   Covered on every wiki it should be: dropped. Covered on some: recorded as a **translation**
   suggestion (listed on the ranking page, no proposal page). Calibration rows always pass this
   stage, with their coverage recorded, so a calibration article missing on some wikis is still listed
   as a translation suggestion.
2. **Triage (film).** Notability floor: sitelinks ≥ 15 for facet candidates, none for seed-list
   candidates (the list already chose them). Drop non-fiction clips, trailers and episodes (episodes
   are judged through their series).
3. **Quick judgment.** One Jev question on a short synopsis (the Wikipedia lead in the richest
   language, as notes): does the work plausibly show the Gospel's influence or a seed of the Word,
   even an implicit one in a single storyline, character arc or episode? Under `quick_drop` (0.25,
   lowered from 0.40 after the first calibration put 4 of 18 existing articles under it): dropped,
   logged.
4. **Dossier (film).** Research notes, never article text: the lead and the plot, premise, story,
   themes and reception sections of the Wikipedia articles in the languages it has (up to three: the
   original language, en, it), the creators' statements on faith or meaning from Wikiquote where
   present; for a series, the season sections of its article and its season pages (the P527 parts
   that are a television series season, else its P1811 list of episodes), with the episode summaries
   of their `{{Episode list}}` templates. Each note keeps its source URL and language.
5. **Adjudication.** On the dossier: (a) connection type, a Jev Choice of *direct Christian or
   biblical influence*, *implicit seeds of the Word* (in the whole work or in one storyline,
   character or episode), *biblical or Christian imagery used for entertainment*, *no connection,
   not even an implicit one*; (b) field: `jev classify`, whose deepest confident node is kept;
   (c) themes: `jev themes`, asked of each part of the dossier (parts of at most 20,000
   characters, at most 5), each theme keeping its best probability, so one strong episode is
   enough; at most 8 themes at 0.50 or above, best first, each with the passage that supports it,
   from the part that scored it. A row with no kept theme is dropped, unless Jev finds a *direct*
   influence at 0.80 or above (Gospel Road had none at 0.50 in its short pages); the connection type is
   shown to the voters and drops nothing (the first calibration found Jev answering *no real
   connection* for 7 of 18 existing articles, series whose ties are implicit or partial). The decisions live in the
   queue and on the proposal page (not in `config/`: `config/adjudications.json` is the tree's
   placement log and stays that).
6. **Proposal.** For each survivor, the bot writes
   `Semina Verbi:Article proposals/<title>` on en with `{{SV proposal|…}}` and a `<vote />`, and
   regenerates `Semina Verbi:Article proposals` (open proposals by votes, per domain; then approved;
   then created; then declined; then translation suggestions). The title is the en Wikipedia title, or the label in
   the richest language. The first wiki proposed is the language with most dossier sources.

### `{{SV proposal}}`

Parameters: `qid` (Wikidata), `labels` (per language, as passed by the bot: the local item does not
exist yet), `domain`, `connection` (and its probability), `field`, `themes` (each with its
probability and evidence), `outline` (the proposed sections), `sources`, `first_wiki`, `status`
(`open`, `approved`, `created`, `declined`), `reason` (for `declined`), `article` (once created). It renders a box with these, a link to the
Wikidata item, and the vote button. Discussion goes on the talk page. i18n strings in `i18n/` like the
other templates, though only en shows them in phase 1.

### Votes

`python -m pipeline votes` (as SV editor bot): for each `open` proposal, read its count by parsing
`{{NUMBEROFVOTESPAGE:<page>}}` (VoteNY's API only casts votes); at or above the threshold, set
`status=approved` on the page, record it in the queue, and regenerate the ranking page. A systemd user
timer on the workstation runs it every 3 hours. Stage 7 starts from `python -m pipeline approved`,
which lists the approved rows with their dossiers for the editor session.

### Declining

A proposal leaves the queue without approval only by hand: the author or a sysop sets
`status=declined` and a one-line `reason` on its `{{SV proposal}}` call (decided 2026-11-20). The
bot reads that back into the queue, never changes it, and never proposes that QID again; the ranking
page lists it under "Declined". Nothing expires on its own.

### Portals

A short page on each wiki, in its language (`Semina Verbi:Proposte di articoli` and the like), saying
what the proposals are and linking the en queue. Written by hand in an editor session, not generated.

### Calibration (before the first proposal is posted)

Run stages 3–5 on the calibration set (the existing film and TV articles) and show the author a
table: Jev's connection type, field and themes against what the articles already have. The author
decides whether the questions' wording or the cut-offs change before the backlog runs.

## Settled details

Facts checked while writing (2026-11-20):

- Local items mirror Wikidata's Wikipedia sitelinks (Q48412, *Risen*, has `enwiki`…`ptwiki` beside
  `en`), so `wbgetentities` with `sites=enwiki&titles=…` on the local Wikibase finds an existing item.
  Read-only API calls to the six wikis from the workstation are allowed at every stage.
- `{{NUMBEROFVOTESPAGE:<page>}}` and `<vote />` work on en (VoteNY REL1_44); `action=parse` of a
  fresh text is not served from the parser cache.

Calls made for the builder (the author may overturn any):

- **SPARQL:** a small client in `pipeline/` for `query.wikidata.org` (60 s timeout, one query at a
  time, retry with backoff on 429). Saints are matched by class (humans with `canonization status`,
  P411), biblical figures by a short curated list of classes in `config/pipeline.json`, not by
  enumerating QIDs. Each run takes at most `max_new_per_run` candidates into stage 3 (200 to start),
  so the Jev spend of one run is bounded.
- **Evidence for a theme:** one Jev Choice per kept theme (≥ 0.50, at most 8), choosing among the dossier's
  paragraphs the one that best supports it. The proposal shows that paragraph's source link and an
  excerpt of at most 25 words, never more.
- **Outline:** derived, not generated: the standard sections of `article-standard.md`, plus one
  section per kept theme, named after the theme.
- **No paying twice:** each Jev call is stored with a hash of its question text. Changed wording does
  not re-ask anything by itself; re-asking is explicit (`--redo STAGE`, for one QID or for all).
- **Page ownership:** the bot owns only the `{{SV proposal|…}}` call at the top of a proposal page and
  rewrites only that; text below it and the talk page are the community's. The bot never moves or
  deletes a proposal page (that would reset its votes).
- **Seed lists:** the Vatican list is read from the en.wikipedia article on the 1995 list; titles that
  do not resolve to a QID are reported, not guessed. Arts & Faith is in phase 1 only if the builder
  finds a machine-readable copy; otherwise it is reported as dropped.
- **Calibration:** the set is the articles in the film and television tree categories on all six wikis
  (by their items); an article whose item has no Wikidata QID is listed in the report and skipped.
  Calibration rows always pass stages 1 and 2 (stage 1 still records their coverage) and are never
  dropped at stage 3, so every disagreement shows.
- **The timer:** `votes` needs no Jev, only the bot; the systemd user timer runs it through
  `runeditor.py`, which reads the bot's credentials from `editor.env` as it does now.
- **Votes after a user leaves `voter`:** they still count; VoteNY does not distinguish, and the
  threshold is low-stakes (approval only queues an editor session).

## Left to the builder

- The SPARQL queries' exact shape and the subclass closure, within the rate limits.
- How a Wikipedia section is picked as "plot", "themes" or "reception" in each language.
- The Jev question wording for stages 3 and 5, which calibration then tests.
- Test doubles for Wikidata, the wikis and Jev, following the repository's existing tests.
