# SeminaVerbi shared templates — design

Date: 2026-11-14. Status: draft for approval (builder check done).

## Context: the larger program

The SeminaVerbi wikifarm (`{en,it,es,fr,de,pt}.seminaverbi.bibleget.io`, MediaWiki 1.44, CentralAuth,
Wikibase repo on `en` with a client on every wiki, Scribunto, BibleGet extension) has been dormant since
December 2024. Content: ~31 articles on `en`, ~49 on `it`, only a main page on the other four.

The author (a priest, the site owner) wants to start writing again, in every language, and to fill the
small wikis from existing `en`/`it` articles, finishing incomplete ones on the way (e.g.
`it:Il Signore degli Anelli`, WIP since 2020, mostly gathered Wikipedia text and long excerpts).
Agreed order of work:

1. **Shared templates** (this document) — every translation depends on them.
2. A MediaWiki MCP server so Claude Code can read, draft and edit pages as a bot.
3. Writing / refining articles with that MCP.
4. Auto-translation: an `en`/`it` article fans out to the other wikis, linked through Wikibase sitelinks.

Items 2–4 get their own designs. This one only has to leave them a stable target.

### What an article is (shapes what the templates must support)

- It is about how a work's storyline, characters and message are shaped by, or aligned with, the Gospel
  (love, forgiveness, faith, hope, virtue, redemption, conversion). Bibliographic/commercial detail
  (editions, sales, box office) is kept to what identifies the work — the infobox is enough.
- It distinguishes genuine alignment with the Gospel from biblical imagery used for entertainment.
- It names and **quotes every Bible verse** the work references or alludes to, with the BibleGet templates.
- It reads the work with a discerning lens for seeds of true faith and for expressions of errors
  (Arianism, Gnosticism, Pelagianism, Nestorianism, Monophysitism, Docetism, Donatism, Marcionism,
  Modernism, New Age, woke ideology) — pointing at the concrete scene/lyric and the teaching it departs
  from, not applying labels loosely; ending with a section under the same heading on every wiki.

## Intent

Translated articles currently cannot render on `es/fr/de/pt` (≈40 templates each vs 500+ on `en`/`it`),
and `en` and `it` don't share template names either (it uses Wikipedia-Italian `Libro`, `Cita web`…,
en has no `Infobox book`). Instead of chasing each Wikipedia's template ecosystem, SeminaVerbi gets one
small family of its own templates, identical on all six wikis, so a translation only translates prose.

## Goals

- One template family, same names and parameters on all six wikis:
  - **Infobox** for a work (book, film, TV series, album, song, play, artwork) and for a person, filled
    mostly from the page's Wikibase item, labelled in the wiki's language.
  - **Citation** (web, book, periodical/journal, video) with fixed parameter names and localized labels.
  - **Bible quote** wrapping `Module:BibleQuote` with a per-language default version.
  - **Section/maintenance helpers** an article needs: the discernment section heading, a "work in
    progress" notice, and the reference list.
- Source of truth is a git repository; a sync script publishes it to all six wikis through the API.
- Local-language aliases (redirects) so editors on each wiki can type a native name.
- Existing `en`/`it` articles keep working untouched.

## Non-goals

- Converting existing articles to the new templates (happens when each article is refined, item 3).
- The MCP server and the translation pipeline (items 2 and 4).
- Reproducing Wikipedia's infobox breadth: only fields that identify the work.

## Anti-goals (failure even if it "works")

- Templates drift apart between wikis, or a fix must be made six times.
- A translation step has to rename templates or parameters.
- An edit on a wiki silently overwritten without warning (template pages must say "managed in git").
- Pulling in large Wikipedia dependency chains (Module:Arguments-style helpers are fine if vendored).

## Constraints (verified 2026-11-14)

- Scribunto is available on all wikis; WikibaseClient is loaded everywhere, so `mw.wikibase` works in Lua.
- Namespace names are localized (`Module:`/`Modulo:`, `Template:`/`Plantilla:`…); the API accepts the
  canonical English names (`Module:`, `Template:`) on every wiki, so the sync uses those.
- Local Wikibase property IDs **differ from Wikidata's** (local `P2016` = instance of, `P458` = title,
  `P725` = image, `P490`/`P501` = follows/followed by). Items were imported with WikibaseImport and are
  thin: Q4178 (*The Lord of the Rings*) has only image, instance-of, title, follows, followed by — no
  author or date. Property labels exist in en/es/fr/de/pt (it to be checked).
- `Module:BibleQuote` exists on `en` and `it` but the two copies differ (2303 vs 2522 bytes); it
  hard-codes `NABRE` as default and a version list of NABRE, NVBSE, LUZZI, CEI2008, DRB, VGCL.
- BibleGet versions available: en NABRE/DRB, it CEI2008/LUZZI, es BLPD, la NVBSE/VGCL. **No French,
  German or Portuguese version** exists in BibleGet today.
- Wikis sit behind Anubis for anonymous GETs to `/w/index.php` and `/wiki/`; `api.php` with a logged-in
  bot is not gated. MediaWiki core interwiki and CentralAuth are configured; OAuth is installed with
  `seminaverbi_en` as central wiki.

## Approaches considered

- **A. Templates in git, pushed by a sync script (chosen).** Reviewable in pull requests, one source of
  truth, wikis can't drift. Cost: on-wiki edits to these pages get overwritten on the next sync, so each
  page carries a "managed in git — edit at <repo>" notice and the sync refuses to overwrite a page whose
  latest revision wasn't made by the sync bot unless forced (it prints the diff instead).
- **B. `en` is master, a bot copies changes out.** Friendlier for on-wiki edits, but no review and a
  broken edit reaches every wiki in minutes. Not chosen.
- **Import each Wikipedia's template sets.** Rejected by the author; huge, divergent dependency chains.
- **Interwiki transclusion (`$wgEnableScaryTranscluding`).** Transcludes rendered output from another
  wiki, so an infobox could not read the local page's item; poor caching. Ruled out.

## Design

### Repository

New private repo `seminaverbi-templates` (GitHub `JohnRDOrazio/seminaverbi-templates`, local
`~/development/seminaverbi-templates`), same conventions as jrd-mcp (CI, PRs, Co-Authored-By trailers).
Layout (builder may refine):

```
modules/          Lua modules, one file per module page (Module:SV/...)
templates/        wikitext of each template page
i18n/<lang>.json  UI strings per wiki language (field labels, headings, notices)
config/wikibase.json   semantic name -> local property ID (instance_of: P2016, title: P458, ...)
config/aliases.json    per-language redirect names -> canonical template
sync/             the sync script
tests/            Lua unit tests (run locally against a stub mw / mw.wikibase)
```

### Naming

Canonical English names with an `SV` prefix to avoid colliding with the existing imported Wikipedia
templates: e.g. `Template:SV infobox work`, `Template:SV infobox person`, `Template:SV cite`,
`Template:SV Bible quote`, `Template:SV discernment`, `Template:SV WIP`, `Template:SV references`;
modules under `Module:SV/…`. Parameter names are English on every wiki. Local aliases are redirects
(`Plantilla:SV cita` → `Plantilla:SV cite`, etc.) listed in `config/aliases.json`.
Exact names are the builder's call, provided they are identical across wikis.

### Infobox

- Reads the item connected to the current page (`mw.wikibase.getEntityIdForCurrentPage()`), or an
  explicit `item=` parameter.
- Fields come from a small per-type field list (book: author, original title, original language,
  first published, genre, image; film: director, screenwriter, main cast, release date, …; album/song:
  artist, release date, genre; person: birth/death date and place, occupation, image).
- Labels: property labels from Wikibase in the page language, overridable from `i18n/<lang>.json`.
- Any field can be overridden or supplied by a parameter when the item lacks it; local overrides win.
- Values that are items render as the item's label in the page language, linked to the local article
  if that wiki has a sitelink, otherwise to the Wikipedia article in that language, otherwise plain text.
- Missing data must not break rendering; the infobox shows what it has and adds a tracking category
  (`SV infobox missing data`).
- Property IDs come only from `config/wikibase.json`, never hard-coded in Lua.

### Enriching the items

Because local items are thin, the infobox is only useful if each item gets its identifying statements.
A repo script (or the WikibaseImport maintenance script, `importEntities.php`) imports the needed
properties from Wikidata for the items of articles we write or refine, mapping Wikidata IDs to local IDs
via WikibaseImport's mapping table. Scope here: the property set the infoboxes use, for the items of
existing `en`/`it` articles. Ongoing enrichment for new articles is a step in item 3's workflow.

### Citation

One `SV cite` template with a `type=` (web, book, journal, video, song/album liner, interview) and fixed
English parameters (`url`, `title`, `author`, `date`, `publisher`, `access-date`, `language`, `quote`,
`archive-url`, `archive-date`, `isbn`, `pages`). Renders a consistent style with labels from i18n.
Dates accept ISO (`2026-11-14`) and render in the page language.

### Bible quote

`SV Bible quote` with `ref=` and optional `version=`, `inline=`. One shared `Module:SV/BibleQuote`
(merging the two current copies) whose version list and per-language default come from config.
Defaults: en NABRE, it CEI2008, es BLPD; fr/de/pt use Latin NVBSE (Nova Vulgata) with a short localized
note that a translation in that language is not yet available. The default is one config value per wiki:
when BibleGet adds a fr/de/pt version, changing it and re-syncing updates every quote (and drops the note). References
are written in a notation BibleGet accepts in every language (the translation step may need to localize
book names; BibleGet's own parser handles en/it/es names — builder to confirm for others).
The existing `Bible quote` / `Citazione bibbia` templates stay as they are.

### Discernment section and helpers

`SV discernment` emits the localized heading of the closing section (e.g. en "Seeds of the Word and
points of caution", it "Semi del Verbo e punti di attenzione"; final wording per language in i18n, to be
reviewed by the author). `SV WIP` replaces the Wikipedia `WIP open`. `SV references` wraps `<references/>`
with a localized heading.

### Sync script

- Logs in with a bot password for a dedicated bot account (shared with the MCP in item 2; creating it
  is the first step of the plan), with only the rights to edit Template/Module/MediaWiki pages.
- For each wiki: compute the target pages from the repo, compare content, and edit only what changed,
  with summary `sync from seminaverbi-templates@<sha>`. Creates alias redirects.
- `--dry-run` prints the diff per wiki. Refuses to overwrite a page whose last revision is not by the
  sync bot, unless `--force <page>`.
- Run manually after a merge to `main` (CI-triggered sync is a later option, not in scope).

### Testing

- Lua unit tests run locally with stubs for `mw` and `mw.wikibase` (field selection, fallbacks, label
  language, override precedence, missing data).
- A smoke page per template on `en` and one small wiki (`User:<bot>/SV test`) rendered after sync; the
  sync can print parse warnings via `action=parse`.

## Left to the builder

Exact template/module names (identical across wikis), repo layout details, the Lua test harness,
the citation style details, which of WikibaseImport vs a custom script enriches items, CI setup.

## Calls made after the builder check (checked against the live wikis 2026-11-14)

- **Book names in `ref=`:** BibleGet parses localized book names (`Jean3,16`, `Johannes3,16`,
  `João3,16`, `Juan3,16` all resolve), so the translation step may localize book names and the module
  passes `ref=` through unchanged. No normalization in Lua.
- **Item-valued fields:** imported items already carry Wikipedia sitelinks (Q1185 *The Hobbit* has 100,
  Q2742 "book series" has 17) and multilingual labels, so the infobox links via the item's
  `<lang>wiki` sitelink. Enrichment uses the server-side WikibaseImport `importEntities.php`, which
  imports referenced items with labels and sitelinks; it runs under the resource guardrails (DB size
  before/after, abort threshold). No local "Wikidata ID" property needed.
- **Accounts:** two separate bot accounts, matching the WordPress pattern: `SV templates bot` for the sync
  (bot password with edit-protected/editinterface-free grants, used only on Template/Module/MediaWiki
  pages by the script's own scoping) and a separate editor bot for the MCP (item 2's design). The sync's
  overwrite guard keys on the sync bot's username, so MCP edits to a template page are detected.
  Smoke-test pages live under `User:SV templates bot/`.

## Decided by the author

- fr/de/pt Bible quotes: Nova Vulgata with a "translation not yet available" note, switchable per wiki.
- Templates live in git and are pushed by a sync script (approach A).
