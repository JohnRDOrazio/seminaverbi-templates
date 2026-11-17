# SeminaVerbi wiki MCP — design

Date: 2026-11-17. Status: draft for approval.

Item 2 of the program in `2026-11-14-shared-templates-design.md` (templates → **wiki MCP** → writing and
refining articles → auto-translation). Item 1 is live on all six wikis.

## Intent

The author wants to write SeminaVerbi again, in every language, with Claude Code doing the wiki work
while he drives and checks as it goes. Claude needs to read and edit the six wikis
(`{en,it,es,fr,de,pt}.seminaverbi.bibleget.io`) as its own bot, **and** to know the house conventions
well enough that its edits don't need fixing:

- internal links within a SeminaVerbi wiki;
- links to Wikipedia and other wikis through their registered interwiki prefixes, with **Cathopedia**
  given a prominent, consistent place (Cathopedia is effectively Italian-only);
- the SV templates, above all BibleGet Bible quotes;
- categorization by the category tree laid out at the bottom of the main page.

The first job it will do is port the main page to fr, es, de and pt, creating each wiki's categories as
en and it have them.

## Goals

- An MCP server Claude Code uses to read, create and update pages on all six wikis, as a dedicated
  editor bot that is not the sync bot.
- A Claude Code skill, kept in this repository, that carries the SeminaVerbi editing conventions (links,
  templates, categories, article standard) and a check-before-save routine.
- One machine-readable category tree with names in all six languages, the single source for the skill,
  the category check, and the main page's category navigation.
- A `SV cathopedia` template, so Cathopedia appears the same way on every wiki.
- Small helper scripts for what the MCP server cannot do: Wikibase sitelinks, and comparing a wiki's
  categories with the tree.

## Non-goals

- Writing or refining articles (item 3) and the translation pipeline (item 4). The main page port is the
  first *use* of this work, done in a session with the author, not part of the build.
- Converting existing articles to SV templates or to the new link rules.
- A review/approval queue: edits go live; the author reviews as he drives.

## Anti-goals (failure even if it "works")

- The bot can delete, protect, block, or edit as an administrator.
- An edit that quietly links to English Wikipedia from a non-English wiki, invents a category outside the
  tree, or leaves a Bible verse unquoted.
- The bot password ends up in the repo, in a transcript, or in Claude's output.
- Conventions written in two places that drift apart (e.g. a template list in the skill that no longer
  matches `config/`).

## Constraints (verified 2026-11-17)

- **Interwiki table is shared by all six wikis.** `w:` and `wikipedia:` → en.wikipedia.org on every
  wiki; `cathopedia:` → it.cathopedia.org; `wikisource:`, `wiktionary:` → English projects; `seminaverbi:`
  → the en wiki. Language prefixes (`it:`, `fr:` …) are interlanguage prefixes for the sister
  SeminaVerbi wikis. Existing content already names the language explicitly: it uses
  `[[wikipedia:it:…]]` (30×), the es main page uses `[[w:es:…]]`. **Decision: the table stays as is;
  the language is always written.** Changing it could silently re-point existing links.
- **Cathopedia:** it.cathopedia.org is the real site; es.cathopedia.org has 4 articles; en/fr/de/pt
  redirect to the www.cathopedia.org portal.
- **Categories:** en has 50 category pages, it 84, es/fr/de/pt ~30 each (maintenance categories only).
  Only 2 en and 1 it category pages carry a Wikibase item, so categories have no interlanguage links.
  The en tree has typo'd names: `Category:Hitory`, `Category:Geografy`. The main page's category list
  (62 en / 63 it distinct categories) is written twice (a `<mobileonly>` collapsible list and a
  `{{Navbox}}`), with placeholder `List1`… entries.
- **Main page dependencies:** the en/it main page uses `Navbox`, `Quote`, `Text and translation`,
  `Lang`, and the legacy `Bible quote`. None of these exist on es/fr/de/pt (it also lacks `Quote`,
  `Text and translation`, `Bible quote`). The es main page prose is already Spanish, but its category
  links still point to English names. fr/de/pt main pages are empty.
- **Wikibase repo is on en**; every wiki is a client; articles get language links from sitelinks.
- MediaWiki 1.44.3, CentralAuth, shared user table with ActorSync (see accounts notes); user groups are
  per wiki. Bot passwords are global. Rate limits: logged-in 20 r/s burst 150 per IP.
- `@professional-wiki/mediawiki-mcp-server` v0.21.0 (MIT-style open source, updated 2026-11-05):
  multi-wiki `config.json`, bot-password auth, env-var substitution for secrets, change tags, bot-flagged
  edits, `readOnly` per wiki, `allowWikiManagement` switch. Tools include get/create/update page,
  `parse-wikitext` (renders without saving and reports wikilinks, templates, URLs), category members,
  search, links-here, history, uploads, and admin tools (delete/protect/move) gated by user rights.
  On a Wikibase repo it also offers `wikibase-search-entities`, `wikibase-get-entity`,
  `wikibase-edit-entity` (entity JSON, which includes sitelinks) and `wikibase-add-statement`.
- **Categories in use outside the main-page tree** are almost all leftovers of text copied from Wikipedia
  (template, maintenance, "Cantautori britannici"-style categories). The exceptions are a few *theme*
  categories: en `Forgiveness`, `Repentance`, `Conscience`, `Family relationships`; it `Perdono`,
  `Coscienza`, `Famiglia`. Main-page categories with members: en 9, it 7. The typo'd en categories
  (`Hitory`, `Geografy`) have **no members**.

## Approaches considered

- **A. ProfessionalWiki server + a SeminaVerbi editor skill.** Almost no code; conventions are prose the
  agent follows. Risk: nothing checks the rules mechanically.
- **B. Our own Python MCP** in this repo reusing `sync/wiki.py`, with SeminaVerbi-aware tools enforcing the
  rules in code. Most control; rebuilds what A already has, and we maintain it.
- **C. Hybrid (chosen by the author):** A, plus small helper scripts the skill calls where a mechanical
  check is worth it. One is needed now: a category check against the tree, which also creates missing
  categories with their sitelinks.

## Design

### Editor bot

- New global account **`SV editor bot`**, created with `createAndPromote` per the accounts notes
  (script name before `--wiki`, language code as `--wiki`; check for stray accounts afterwards), email
  confirmed server-side like the sync bot, attached by CentralAuth.
- Groups on all six wikis: `bot` (edits flagged, so the author filters them in recent changes, and higher
  API limits). No `sysop`, `bureaucrat`, `interface-admin`.
- Bot password **`mcp`** with grants: basic, high-volume editing, edit existing pages, create/edit/move
  pages, upload new files, upload/replace/move files, edit structured data (needed for Wikibase on en).
  No delete, protect, block, user-rights, or interface grants — the server's admin tools then fail with
  a permission error instead of acting.
- A change tag **`seminaverbi-mcp`** defined (Special:Tags) on all six wikis; the server tags every edit
  with it, so the author can list exactly what the agent did.
- Secret: `~/.config/seminaverbi/editor.env` (mode 600), same rules as `sync.env` — never read back or
  printed by Claude. Created by the author or by Claude writing a value Claude generated, never echoed.

### MCP server

- `@professional-wiki/mediawiki-mcp-server`, **pinned to an exact version** (0.21.0 or the current one
  at build time), run with `npx -y …@<version>`; version bumps are a deliberate change reviewed like the
  jrd-mcp adapter.
- `config.json` in this repo at `mcp/config.json` (no secrets: credentials come by env-var substitution
  from `editor.env`). Six wikis keyed `en, it, es, fr, de, pt`; default `en`; `allowWikiManagement:
  false`; bot edits on; change tag `seminaverbi-mcp`.
- Registered in Claude Code at user scope as MCP server **`seminaverbi`** with a small launcher that loads
  `editor.env` into the server's environment (like `runsync.py` does for the sync), so the secret never
  sits in `~/.claude.json`.

### Category tree

- New `config/categories.json`: a tree of nodes, each with a stable key and a name per language, plus
  optional `description` per language for the category page. Order is the main page order. Seeded from
  the en and it main pages; es/fr/de/pt names are translated by Claude and checked by the author during
  the first port. Fixes the typos (canonical en names `History`, `Geography`).
- Two sections, both decided by the author (2026-11-17):
  - **Fields**: the arts and sciences categories from the main page.
  - **Gospel themes**: a curated, flat list of themes an article can explore (an article about a film goes
    in *Cinematography* **and** in each theme it really explores, e.g. *Forgiveness*). Parent: a root
    category `Category:Gospel themes` (localized). Seed list (my call, the author checks it in the first
    session): the themes the article standard names — love, forgiveness, faith, hope, virtue, redemption,
    conversion — plus those already in use: repentance, conscience, family (en `Family relationships` /
    it `Famiglia` merge into one node; the existing en/it theme pages are kept and linked, renamed only
    if the author agrees). New themes are added to the list (a PR to this repo) before the bot uses them.
- Maintenance categories (WIP, tracking) stay where the templates already define them.
- An article gets at least one field category and the Gospel-theme categories it genuinely explores
  (the skill tells the agent not to tag a theme the text doesn't discuss).
- Category pages created from the tree contain: the localized description (if any), the parent
  category, and nothing else. Top-level nodes have a root category (`Category:Semina Verbi` and
  localized equivalents) as parent so the tree is browsable from one place.
- The typo'd en categories have no members, so they are simply moved to the canonical names during the
  first session; existing it names are taken as canonical for it.
- Seeding reproduces the en/it main-page navigation exactly (order, grouping; the one-category difference
  between en and it is reconciled into a single node list the author checks), so en/it can switch to
  `SV category tree` later with a one-line edit. **Switching the en/it main pages is not part of this
  work.**
- Categories outside the tree that come from copied Wikipedia text are left alone; they are cleaned up
  when each article is refined (item 3).

### `SV category tree` template (main page navigation)

- A new Lua function renders the main page's category navigation from `categories.json` (shipped to the
  wikis as JSON data the module reads, the same way the existing modules read config), in the wiki's
  language, as one responsive collapsible list that works on desktop and mobile, with the Gospel themes
  under their own heading after the fields. This replaces the
  duplicated `<mobileonly>` list + `Navbox` and its placeholder entries, and removes the need to import
  `Navbox` and its modules into four wikis.
- Category links it renders show red when a category page is missing, which doubles as a visual check.

### Quotes on the main page

- The main page's non-Bible quotes (`Quote`, `Text and translation` with author, title, publisher…) get
  one new shared template **`SV quote`**: text, author, source (wikitext, may be a link), and optional
  `original` + `original_lang` for a quote shown with its original-language text. The module marks the
  original with `lang`/`dir` attributes itself, so the ported pages need no `Lang` template; any other
  inline foreign phrase uses a plain `<span lang="…">`. Bible passages on the
  ported main pages use `SV Bible quote`. en/it main pages are left as they are until the author decides
  to switch them.

### `SV cathopedia` template

- `{{SV cathopedia|Concilio Vaticano II}}` (optional second parameter: link text) renders a link to
  `cathopedia:<title>`. On non-Italian wikis it adds a localized "(in Italian)" note; on it, no note.
  Adds a tracking category per wiki (localized, e.g. `Category:Pages linking to Cathopedia`) so the
  Cathopedia footprint can be listed.
- Localized aliases in `config/aliases.json` like the other SV templates (e.g. it `SV cathopedia`
  unchanged, fr/es/de/pt as the builder sees fit).

### Link rules (taught by the skill)

- Internal: `[[Title]]` / `[[Title|text]]` to pages on the same wiki; categories linked inline with a
  leading colon (`[[:Category:X]]`); a red link is allowed only when the article is planned (the agent
  says so when it leaves one).
- Wikipedia: always `[[w:<lang>:Title|text]]` with `<lang>` the page's own wiki language (on en,
  `[[w:Title|text]]` is equivalent; the skill writes `w:en:` anyway for uniformity in new text). Before
  linking, the agent confirms the target exists in that language Wikipedia; if it doesn't, it links the
  English article and says so, or leaves the text unlinked.
- Cathopedia: on **it**, Catholic topics link inline to `[[cathopedia:Title|text]]` in preference to
  Wikipedia when Cathopedia has the article. On **other wikis**, inline links go to the reader's-language
  Wikipedia, and the Catholic topic gets an `{{SV cathopedia|…}}` line under "See also" / "External links"
  (localized heading).
- Sister SeminaVerbi wikis: inline cross-language links use `[[:<lang>:Title|text]]` (leading colon;
  without it the link moves to the sidebar). Interlanguage links between translations come from Wikibase
  sitelinks, never from `[[fr:…]]` lines in the text.
- Other registered prefixes (`wikisource:`, `wiktionary:` are English; `newadvent` is not registered —
  use plain external links): the skill lists the few useful ones and that they are English-targeted.

### Skill: `seminaverbi-editor`

- Lives in `skills/seminaverbi-editor/` in this repo, installed into Claude Code by symlink into
  `~/.claude/skills/`. Contents:
  - `SKILL.md`: when to use it; the edit routine; pointers to the reference files.
  - `links.md`: the link rules above, with examples per language.
  - `templates.md`: **generated** from `config/`, `i18n/` and the template files by a script in this repo
    (names, localized aliases, parameters, defaults per wiki, e.g. which Bible version each wiki uses),
    so it can't drift; CI fails if the generated file is stale.
  - `categories.md`: generated from `categories.json` (the tree, per language).
  - `article-standard.md`: the article standard (Gospel focus; name and quote every verse with
    `SV Bible quote`; discern errors at the concrete scene; closing discernment section via the SV
    template).
- **Edit routine** (every save): fetch the current page; write the wikitext; run `parse-wikitext` on it
  and check the reported links/templates — no unknown templates, no English Wikipedia link on a
  non-English wiki, categories all in the tree, every Bible reference rendered through `SV Bible quote`;
  run the category check helper for new categories; save with a summary in the wiki's language; report
  the diff link to the author.

### Wikibase

- The server's Wikibase tools stay enabled: the agent uses `wikibase-edit-entity` to attach a translated
  page to its item's sitelinks (item 4 will need this) and reads items with `wikibase-get-entity`.
- Skill rule: on items, the agent changes **sitelinks and labels/descriptions only**, unless the author
  asks for a statement change in that session; statements feed the infoboxes and come from
  `EnrichItems.php`.

### Helper script (in this repo, reusing `sync/wiki.py`)

- `python -m tools.categories check --wiki <lang> [--create] [--dry-run]` — compares the wiki with
  `categories.json`: tree categories missing, tree categories whose page names a different parent; and,
  as information only, categories used by main-namespace articles that are not in the tree. It never
  edits an existing page. `--create` creates the missing tree categories (description + parent, tagged
  edit) and puts each in the same Wikibase item as its counterparts on the other wikis (creating the item
  with labels when none of them has one), via `wbsetsitelink` / `wbeditentity`. The agent may run
  `--create` without asking each time; the author reviews via the change tag.
- It reads `editor.env` through the same loader pattern as the sync and never prints the secret.

### Testing

- Lua: `SV cathopedia`, `SV quote`, `SV category tree` under the existing lupa harness (it/non-it note,
  tracking category, localized names, red link output, original-language quote).
- Python: `categories.json` validity (every node has all six names, unique keys, no cycles);
  generated skill docs up to date; `tools.categories` against a fake client like the sync tests (missing /
  wrong-parent / untracked detection, existing pages never edited, new vs. existing Wikibase item, dry run
  writes nothing).
- Live smoke after deploy: `whoami` through the MCP shows `SV editor bot` with `bot` and no admin
  groups; a `delete-page` attempt on a test page fails with a permission error; an edit to
  `User:SV editor bot/sandbox` on each wiki shows the change tag and bot flag.

## Left to the builder

- Exact shape of `categories.json` and of the generated docs, within the rules above.
- Localized aliases and message wording for the new templates.
- How `SV category tree` reads the tree on the wiki (JSON page vs. generated Lua data module).
- Launcher mechanics for the MCP server, provided the secret stays out of `~/.claude.json`.
