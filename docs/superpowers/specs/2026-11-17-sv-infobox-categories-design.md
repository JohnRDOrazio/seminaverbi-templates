# One SV infobox driven by the Wikibase item, with categories from the item — design

Date: 2026-11-17. Status: draft for approval. Builds on `2026-11-14-shared-templates-design.md` (the
existing `Module:SV/Infobox`) and `2026-11-17-category-tree-jev-design.md` (the category tree, PR #3).

## Intent

The author wants SeminaVerbi's own infoboxes instead of copies of Wikipedia's, which differ in every
language edition and drag in dozens of subtemplates and Wikipedia tracking categories. The infobox must
look and behave the same on all six wikis.

The author also wants articles filed **without anyone adding category lines by hand**. Every article is
connected to a Wikibase item; the item carries the article's field of the arts and sciences and its
Gospel themes; the page outputs those categories itself, in the wiki's language.

Agreed in conversation:
- Approach A: one `{{SV infobox}}` driven by the item. The layout follows the item's "instance of";
  parameters are optional overrides (`type=` where the item's type is missing or ambiguous).
- Facts come from the item, in stages: categories first, more facts and layouts after.
- Pages without an infobox get their categories from `{{SV categories}}`, the same module without the
  box.

## What exists today (verified 2026-11-17)

- `Module:SV/Infobox` with `{{SV infobox work}}` (`type=` book, film, tv, album, song, play, artwork) and
  `{{SV infobox person}}`: rows read from the item, labelled from Wikibase in the wiki's language, dates
  formatted per language (precision, BC, Julian days), parameters override, a maintenance category when
  the item gives nothing. Layouts in `config/infobox.json`, property IDs in `config/wikibase.json`, both
  shipped in `Module:SV/Data` by the sync. Used on one page per wiki so far.
- Until today the module could not find the page's item: `getEntityIdForCurrentPage()` and local
  `getSitelink()` returned nil because the category items were linked under Wikipedia's site IDs. Fixed
  (PR #3): SeminaVerbi site IDs are `en`…`pt`; `enwiki`…`ptwiki` are Wikipedia.
- The repository mirrors Wikidata items (≈13,000), with their statements and Wikipedia sitelinks;
  `EnrichItems.php` fills article items' statements from Wikidata. 28 of the 29 connected article items
  have "instance of" (P2016); most have genre, country, image, title, dates, director/cast/composer, birth
  and death data.
- Coverage: en 17 of 35 articles connected to an item, it 20 of 52, es/fr/de/pt 1 of 2 each (the main
  page). en 29 and it 40 articles use Wikipedia-derived infoboxes, several hand-modified by the author to
  add a field category.
- Every tree category (fields and themes) has an item linked on all six wikis (e.g. Music = Q47743).
- Wikibase client Lua is available on all six wikis; InstantCommons is on (Commons images display);
  any registered user may create properties.

## Goals

1. Two properties on the repository, both of type item:
   - **SV field** — the article's one field in the tree ("one work, one page, one field").
   - **SV Gospel theme** — any number of leaf Gospel themes.
   Their IDs join `config/wikibase.json` as `sv_field` and `sv_theme`.
2. `{{SV infobox}}`: picks the layout from the item's "instance of" through a mapping in
   `config/infobox.json` (instance-of item → layout); `type=` overrides; an item with no mapped type gets
   a generic `work` layout (or `person` for humans) and a tracking category. `{{SV infobox work}}` and
   `{{SV infobox person}}` keep working as before.
3. Category output, from the infobox and from `{{SV categories}}`: for each SV field / SV Gospel theme
   value, the category this wiki links to that value's item, as `[[Category:…]]` with the page's sort
   key. Values that are not tree categories, a second SV field, or a value with no category on this wiki
   go to tracking categories instead of being dropped silently.
4. Filing moves to the item: a tool reads each connected article's current tree categories (from its
   wikitext and its templates), writes the matching SV field / SV Gospel theme statements, and — once the
   page outputs them through the module — removes the hand-written tree category lines. Non-tree
   categories (maintenance, Wikipedia leftovers) are left for article refinement.
   - **Themes: the union** of the tree themes the item's pages carry on all wikis (author's decision: a
     theme belongs to the work, not to one wiki's article; Jev's theme check can remove one later, when
     an article is refined).
   - **Field: one value.** When the item's pages disagree on the field, the tool stops for that item and
     asks the author.
   - **Field vs. type (author's decision):** when the field does not fit the item's "instance of" (e.g. a
     television series filed under Film, today *Daredevil* and *Fringe*), Jev's article classification
     re-checks the field; a confident answer is applied, a close call goes to the author, and the
     decision is recorded in `config/category_decisions.json` like every other Jev decision.
5. The `seminaverbi-editor` skill and the Jev routine write field and theme decisions to the item
   instead of the page, and every new article gets an item (imported from Wikidata when the subject has
   one, so its facts come with it; created otherwise).

## Stages

1. **Categories from the item** (this design, planned first): goals 1–5 for the articles that already
   use `{{SV …}}` templates or none; layouts as they are plus the instance-of mapping.
2. **Replace the Wikipedia infoboxes** (en 29, it 40 pages) with `{{SV infobox}}`, page by page or in a
   reviewed bulk pass, adding layouts the pages need (e.g. musical group, video game, comic, franchise).
   The author's category-adding modifications become unnecessary.
3. **More facts**: further rows per layout, and items for the remaining unconnected articles.

## Non-goals

- Infobox data entered as page parameters as the normal way of working (parameters remain overrides).
- An extension that adds categories without any template on the page: categories come from the parser,
  so the page calls the module (infobox or `{{SV categories}}`).
- Editing Wikidata, or changing the imported Wikidata items' statements other than adding SV field /
  SV Gospel theme.

## Anti-goals

- An article losing a tree category during the move to the item (the statement must be in place and
  rendering before a category line is removed).
- Different infobox content for the same item on two wikis, other than language.
- Statements written for the Wikidata-mirrored items that are not SeminaVerbi articles.
- A silent wrong category: anything unexpected lands in a visible tracking category.

## Constraints

- Statements live on en (the repository); pages on all six wikis read them. Wikibase re-renders client
  pages when an item they use changes (usage tracking); the plan verifies this works on the farm before
  relying on it (an edit to an item must show on a client page without a manual purge).
- The bot (SV editor bot) edits items with the `seminaverbi-mcp` tag, bot flag, never as an admin.
- `Module:SV/Data` stays one `mw.loadData` module; new config goes into the existing JSON files and the
  sync.
- Tests: the repository's Lua tests (LuaEnv) and Python tests; the full suite passes at every commit.

## Calls made in the design (the author may overrule)

- **Wikipedia-infobox pages are filed in stage 1 too.** Their items get the statements and the pages get
  the module's categories now; the categories their hand-modified Wikipedia templates add stay until
  stage 2 replaces those templates. The same category emitted twice is one category in MediaWiki, so
  nothing doubles. Only hand-written category lines are removed in stage 1; the templates are not edited.
- **New articles find their item in the mirror first.** The repository already mirrors ≈13,000 Wikidata
  items, so the skill searches it (`wikibase-search-entities`) and adds the SeminaVerbi sitelink to the
  existing item, facts included. Only when the subject is not mirrored does it create a new item (labels,
  description, sitelink). Importing new items from Wikidata through `EnrichItems.php` is stage 3.
- **Re-render check.** The plan verifies early that an item edit re-renders a client page. If it does not,
  the filing tool purges the pages it touches and verifies their categories, and the farm's dispatch
  configuration becomes a separate ops task brought to the author before anything else.
- **`{{SV infobox work}}` and `{{SV infobox person}}` output the item's categories as well**, like
  `{{SV infobox}}`.
- Tree items are recognised by ID: the filing tool records each node's item in `config/categories.json`
  (from the sitelinks), and the module checks values against that list.

## Left to the builder

- The exact instance-of → layout table (from the types in use: television series, film, human, musical
  group, literary work, book series, theatrical production, album, media franchise …), and whether to
  follow "subclass of" one level when a type is not mapped.
- Sort keys, tracking category names (in the six languages' i18n files), and the wording of the property
  descriptions.
- How the filing tool reports and confirms (dry run first, as the other tools).
