# Connect every article to its item, then switch to `{{SV infobox}}`

Date: 2026-11-18. Follows `2026-11-17-sv-infobox-categories-design.md` (merged in PR #4).

## Intent

SeminaVerbi should show one infobox design on all six wikis, with its facts and categories kept
**once**, on the article's Wikibase item, instead of the ~30 different Wikipedia infoboxes the articles
carry today, each with its own parameters per language. Two steps, in order:

1. **Connect** every SeminaVerbi article to an item (the infobox and the categories read the item).
2. **Switch** each page's Wikipedia infobox to a bare `{{SV infobox}}`, after moving onto the item any
   fact the old infobox shows that the item lacks.

## Current state (survey of 2026-11-18)

- en 35 articles, 18 unconnected; it 52 articles, 32 unconnected; es/fr/de/pt only their main page
  (unconnected).
- Exact Wikipedia-title matches in the local mirror: 1 (it: David Bowie → Q46886, the item en already
  uses). A label search finds almost none: the local mirror (~13k items) mostly lacks these subjects.
  They must be imported from Wikidata.
- WikibaseImport is installed on the repo wiki (en), with its Wikidata→local ID mapping table;
  `server/EnrichItems.php` adds the Wikidata statements the SV infoboxes read (`server/properties.txt`)
  to local items that lack them.
- Infoboxes in use: en ~19 kinds (Infobox song, television, film, album, person, musical artist,
  stage production, saint, food, musical composition, scientist, comics…, software, file format,
  television episode, book series; plus helpers Film date, Album ratings/reviews); it 11 (Album, Bio,
  Artista musicale, Brano musicale, FictionTV, Film, Personaggio, Libro, Dramma, Opera d'arte, plus
  Bandiera). Several were hand-modified to add tree categories; since PR #4 every filed page also calls
  `{{SV categories}}` or an SV infobox.
- `{{SV infobox}}` layouts (`config/infobox.json`): book, film, tv, album, song, play, artwork, person,
  work, group. Layout from *instance of* via `config/infobox_types.json`; any row key passed to the
  template overrides the item's value.

## Decisions by the author

- **Off-topic pages are not connected.** They go on a deletion list for the author (the bot has no
  delete right): it JavaScript, Interprete JavaScript, Cookie, Cross-site request forgery, Bevanda
  alcolica, OpenStreetMap, Musica pop, Anchor, Progetto:Fumetti/Citare fumetti/Esempi; en
  Draft:Sandbox. (Interprete JavaScript and Draft:Sandbox were proposed by me and not objected to.)
- **Jägermeister is on topic** (the logo and the story behind it). Christmas tree, Easter egg and Easter
  bread are genuine articles.
- **Approach 1** (below): bare `{{SV infobox}}`; facts live on the item.
- **it `{{Bio}}` → a new `{{SV bio}}`** that writes a person's opening sentence from the item, in each
  wiki's language, with no categories, so a fact changed once (on the item, from Wikidata) updates the
  infobox and the opening sentence on every wiki (Step 2.6 and "`{{SV bio}}`" below). This replaces the
  earlier decision to write Bio's sentence out as prose.

## Approaches considered

1. **Bare `{{SV infobox}}`, facts moved to the item first** — chosen. One design and one copy of the
   facts for every language. Costs: ~60 page edits, statements on items, a parameter→property map for the
   infobox kinds in use.
2. Page-filled `{{SV infobox|director=…}}` with the old values as parameters — fewer item writes, but
   each language keeps its own copy of the facts: the inconsistency the author wants gone.
3. Redefine the Wikipedia template names as wrappers that call `{{SV infobox}}` — no page edits, but the
   old parameters are silently ignored, facts missing from the item vanish, and the pages keep
   Wikipedia's template names.

## Goals

- Every on-topic article on every wiki is connected to exactly one item; translations share it.
- Every connected article that had a Wikipedia infobox shows `{{SV infobox}}` instead, with no fact the
  old infobox showed in an SV row lost unless the author accepted the loss for that page.
- Categories unchanged by the switch (the item already carries them).
- The main pages of es/fr/de/pt join the main page's item (Q4).
- A deletion list of the off-topic pages for the author.

## Non-goals

- Deleting pages or the now-unused Wikipedia templates (the author decides later; the bot cannot).
- New infobox designs or layouts beyond what the in-use kinds need (`{{SV bio}}` is the one new template) (a kind with no fitting layout uses
  `work` / `person` and is reported).
- Article prose, translations, the theme content tasks (`docs/content-tasks.md`).
- Rows the SV layout doesn't show: an old infobox field with no SV row is dropped without ceremony.

## Anti-goals (a failure even if it runs)

- A page loses a tree category, or shows an empty infobox where it had facts.
- An article is connected to the wrong item (e.g. the film instead of the novel; the album instead of
  the song of the same name). One work, one page, one item.
- A wrong fact written to an item (a guessed date, a name resolved to the wrong person). Unresolvable
  values are reported, never guessed.
- Statements written to an item that Wikidata could have supplied: Wikidata is the first source.

## Constraints

- Items live on en (the repository); client wikis use site IDs `en`…`pt` (never `<lang>wiki`, which is
  Wikipedia).
- Writes as SV editor bot (`runeditor.py`), tag `seminaverbi-mcp`, guarded edits (base revision,
  preview keeps every tree category, saved revision verified) as in `tools/file_items.py`.
- Server-side steps (WikibaseImport, EnrichItems) run as the vhost user via `maintenance/run`; database
  size checked before/after (guardrail: stop if `seminaverbi_en` grows by more than 200 MB).
- Requests to Wikidata carry only article titles and QIDs, with the repository's generic User-Agent.
- One-shot tools, idempotent: a second run plans nothing.

## The design

### Step 1 — Connect

For each article not connected (excluding the deletion list and main pages):

1. **Find the Wikidata item**: the article's title on the Wikipedia of the same language
   (`wbgetentities sites=<lang>wiki titles=<title>` on Wikidata); failing that, the Wikipedia link the
   page itself carries (interlanguage link, `{{Interprogetto}}`/`{{Collegamenti esterni}}` with
   Wikidata, or the import's edit summary), then a Wikidata label search. Candidates are filtered by the
   page's kind (the old infobox: song vs album vs film). One confident candidate → proceed; several or
   none → the author.
2. **Local item**: if the mapping table already has that Wikidata ID, use the local item; else import it.
   WikibaseImport's own `importEntities.php` no longer runs on MediaWiki 1.44, so `EnrichItems.php` gets
   an import mode (`--import Q…`: labels, descriptions, Wikipedia sitelinks, mapping row, then its
   statements as for `--item`), deployed to the tools directory. *(Builder check; my call.)*
   **No Wikidata item at all**: create a local item (labels, description, sitelink), as the editor skill
   already prescribes. *(Answered by the skill.)*
3. **Translations**: before creating anything, check whether the same subject is already connected on
   the other wiki (it: David Bowie ↔ en's Q46886; it/en "Hear Me Lord", "Miserere (album)", "The Holy
   Land"): both pages share one item.
4. **Sitelink**: add the SeminaVerbi sitelink (`en`/`it`), then file the item (run `tools/file_items`
   for it, so its field/themes move to the item like the others).

The main pages of es/fr/de/pt get sitelinks on Q4. The tool prints the deletion list.

### Step 2 — Switch to `{{SV infobox}}`

Per connected page with a Wikipedia infobox:

1. **Enrich the item from Wikidata** (`EnrichItems.php --item`), so Wikidata's facts are there first.
2. **Map the old infobox**: a table `config/infobox_import.json`: for each infobox kind in use, the
   template name(s) per wiki → the SV layout, and each parameter → an SV row key (thus a property via
   `infobox.json`/`wikibase.json`). Unmapped parameters are ignored (non-goal).
3. **Compare**: for each mapped row the SV layout shows, if the old infobox has a value and the item
   has none for that property, convert it:
   - item-valued rows: a wikilink (or plain name) resolved to an item through the linked page's local
     sitelink, else its Wikipedia title on Wikidata (import if needed); never by label alone;
   - dates: parsed only from unambiguous forms (`{{Film date|…}}`, `{{Start date|…}}`, ISO, "17 November
     1999", a bare year), with the matching precision;
   - image: the file name, if the file exists on Commons, on en/it Wikipedia or locally. All six wikis
     see the same file repositories (local, `shared`, `enwiki`, `itwiki`, `wikimediacommons`), so the
     name renders on every wiki. If the item's image property (commons-media) refuses a file that isn't on
     Commons (non-free posters, covers, logos), the page keeps `image=<file>` on `{{SV infobox}}`: the
     one allowed exception to the bare template. *(Builder check; my call.)*
   - anything else → reported as unresolved.
4. **Gate**: a page is switched only if every mapped row with a value is now covered on the item (or the
   author accepted the listed losses for that page). Otherwise it is reported and left unchanged.
5. **Swap**: replace the infobox call (with its helper sub-templates inside it) by `{{SV infobox}}`,
   keeping a `type=` only when the item's instance of doesn't give the right layout. Guarded edit as in
   `file_items`; `{{SV categories}}` on the page is removed when `{{SV infobox}}` takes over (same
   categories).
6. **it `{{Bio}}`** is not an infobox: it writes the biography's opening sentence from page parameters
   (and adds Wikipedia categories). On a switched page `{{Bio|…}}` becomes `{{SV bio}}`, carrying over
   only the free text Bio held (the parts not derived from facts, e.g. a pseudonym note, what follows the
   occupation, references), after Bio's facts are moved to the item like the infobox's. The swap is
   gated: `{{SV bio}}`'s rendered sentence must match the old one's facts (name, places, dates,
   occupation, nationality); a page where they differ is blocked and reported with both sentences.
   *(Author's decision, 2026-11-18.)*
7. **Report**: per page — switched / blocked (with the unresolved rows) / no infobox.

### `{{SV bio}}`

A new template and Lua function (alongside `Module:SV/Infobox`, reading the same item and properties)
that renders a person's opening sentence in the wiki's language. Parts:

- **Name**: the item's label in the wiki's language; optional `name=` for a fuller form (e.g. "Filippo
  Neviani") and `note=` for free text after it ("pseudonimo di …").
- **Dates and places**: birth place and date, death place and date, formatted like the infobox does,
  in the parenthesis conventions of each language (it "(Sassuolo, 6 gennaio 1972)", en "(born 6 January
  1972 in Sassuolo)" or "(6 January 1972 – …)").
- **Predicate**: copula in the right tense (living or dead: it "è / è stato", en "is / was", es "es /
  fue", fr "est / était", de "ist / war", pt "é / foi"), article and occupation label agreeing with the
  person's sex or gender (P21): Wikidata's female form of the label (P2521) where it exists, else the
  label; nationality from country of citizenship (P27) as the adjective form where available, else
  omitted rather than guessed; several occupations joined as each language joins lists.
- **Tail**: `rest=` free text continuing the sentence; references via `ref=` (or written after the
  call).
- **No categories.** Missing facts leave their part out (no empty parentheses); the infobox tracking
  category applies when the name or occupation is missing.
- Per-language grammar lives in `i18n/*.json` plus small rules in the module; tests cover all six
  languages, male/female/unknown, living/dead, missing parts.
- New properties for `config/wikibase.json` as needed (sex or gender, country of citizenship, the
  female-form qualifier); `server/properties.txt` gains their Wikidata IDs so EnrichItems fills them.

### Rendering checks and the author's checkpoint

Wikidata imports and enrichment run first (Wikidata is the source of truth; they need no review). Then
one dry run shows the author, per page: the facts converted from the old infobox that would be written
to the item, the rows still unresolved, and the future infobox rendered with `action=parse` (item values
plus the converted ones passed as parameters). Nothing else (converted facts, page edits) is written
until the author has seen it. *(Builder check; my call.)*

The deletion list is complete for the pages to connect: the en pages with software, file-format and
comics infoboxes (SQLite, New Gods) are already connected and filed. *(Builder check; answered.)*

## Left to the builder

- Where the tools live (extend `tools/file_items.py` or new `tools/connect_items.py` and
  `tools/switch_infobox.py`), and how server-side imports are invoked (SSH + `maintenance/run` from a
  script, or a list for the author to run).
- The exact contents of `config/infobox_import.json`, built from the infobox kinds in the survey.
- Report formats; caching of Wikidata answers within a run.
