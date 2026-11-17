# SeminaVerbi complete category tree, adjudicated with Jev — design

Date: 2026-11-17. Status: draft for approval. Builds on `2026-11-17-wiki-mcp-design.md` (PR #2).

## Intent

The author wants "a consistent and coherent representation of all expressions of human culture, from the
arts to the sciences, in rational and sensible category trees", identical in meaning on all six wikis,
together with the Gospel themes tree. The tree is **complete from the start**, so agents writing new
pages always find the category of reference. Where a placement is disputed (two wikis disagree, a field
could sit under two parents, an existing page doesn't fit), **Jev** — TypeSafe's System One model, which
answers typed questions with calibrated probabilities — adjudicates. Confident answers are applied; close
calls go to the author. The `seminaverbi-editor` skill learns to use Jev the same way when it files
articles.

The author has given permission to re-imagine the tree: rename categories, move and rename pages.

## Goals

- A complete field tree in `config/categories.json`, anchored on established classifications, with a
  one-line **definition** per node in English (used by Jev, the category pages and the skill) and names
  in six languages.
- A Gospel themes tree, structured rather than flat, consistent with the field tree's conventions.
- `tools/jev.py`: Jev questions the repository and the skill use — placing a node, classifying an
  article into the field tree, judging Gospel themes for an article.
- `config/category_decisions.json`: every adjudication with its question, options, probabilities,
  confidence, outcome (applied / author) and date.
- A migration from today's en/it/fr category pages to the new tree: page moves with redirects, members
  re-filed, stray parents fixed, driven by a reviewed map.
- The skill teaches the Jev routine.

## Non-goals

- Refining article prose (item 3 of the program). Article re-filing in the migration only changes
  category lines.
- Maintenance/tracking categories (Wikipedia leftovers): untouched here; cleaned when each article is
  refined.
- A Jev integration inside MediaWiki: Jev runs from the repository's tools and the agent's session only.

## Anti-goals

- Jev's answer applied when it is not confident, or applied without a record.
- A category added or moved on a wiki without being in `categories.json`.
- An existing article losing a category without receiving its replacement.
- The TypeSafe API key printed, logged or committed.

## Constraints (verified 2026-11-17)

- `TYPESAFE_API_KEY` is set in the author's environment; the `typesafe-sdk` Python package is not yet
  installed in the repo venv. Current models: `jev-1.14.0` (Choice, Score, Noul, Box), price $0.042 per
  million input tokens; output free. A Choice takes up to 255 options; a request fits 64k tokens (32k per
  question). Questions in one request run in parallel.
- `tools.categories --create` has **not** been run on any wiki (only a dry run on en), so es/de/pt have
  no tree pages: they get the new tree directly.
- `Category:Video games` does not exist on en; `Category:Video gaming` is on Q47288, whose en sitelink
  already reads `Category:Video games` — moving the page makes the two agree.
- Existing usage is small: en has 17 category pages with members (55 member links), it 18 (150, mostly
  template/maintenance categories); es/fr/de/pt have essentially none. fr has `Architecture` under the
  English "Visual arts".
- Known inconsistencies to resolve: en Comics / Video gaming under Popular culture vs it under Arti
  visive; en themes Forgiveness, Repentance, Conscience and `Family relationships` without parent; it
  top-level categories without parent; it Musica in "Arti dello Spettacolo" (capital S); it Perdono,
  Famiglia, Cultura popolare in template categories; Wikibase Q47288 links en to `Category:Video games`
  while the tree says "Video gaming"; current tree places History/Philosophy under Formal sciences and
  Medicine under Social sciences.
- The bot (`SV editor bot`) can create, edit and move pages (autoconfirmed; `createeditmovepage` grant);
  category pages move like other pages, but MediaWiki does **not** re-file members when a category moves.
- `tools.categories` already creates missing pages and Wikibase sitelinks, and never edits existing pages.

## Approaches considered

- **A. Draft from established frameworks, Jev adjudicates the disputes (chosen).** The structure comes
  from OECD and UNESCO; code finds contested points; Jev answers each with probabilities; confident
  answers are applied and logged, close calls go to the author.
- B. Jev places every node level by level from a flat list: more calls, and only as good as the parent
  options offered; most placements are not in doubt.
- C. Mirror Wikipedia's category graph: not a tree, cyclic, different in every language.

## Design

### The field tree

Top level (parents of everything, under the root `Seeds of the Word` and its localized names):

1. **Arts** — after UNESCO's Framework for Cultural Statistics (2009) cultural domains: literature;
   performing arts (music, theatre, dance, opera…); visual arts and crafts (painting, sculpture, drawing
   and printmaking, photography, crafts and decorative arts incl. ceramics, glass, mosaic, textile arts
   and tapestry); architecture and design; audiovisual and interactive media (film, television, radio,
   animation, video games); books and press / comics; cultural and natural heritage (monuments, sacred
   art, cultural traditions, festivals, folklore); gastronomy (culinary arts).
2. **Humanities** — after OECD Fields of Science and Technology (FOS 2007) group 6: history and
   archaeology; languages and literature studies; philosophy, ethics and religion — split so **theology**
   is its own node; arts studies (art history, musicology, film studies).
3. **Social sciences** — FOS group 5: psychology, economics, education, sociology, law, political
   science, social and economic geography, media and communications.
4. **Natural sciences** — FOS group 1: mathematics, computer and information sciences, physical sciences,
   chemical sciences, earth and environmental sciences, biological sciences.
5. **Engineering and technology** — FOS group 2 (summarized to its main branches).
6. **Medical and health sciences** — FOS group 3.
7. **Religion** — a top-level node for religious traditions and practice as cultural expression
   (Christianity with liturgy, hagiography, Christian art references by cross-listing in the definitions;
   other religions), distinct from theology as a discipline. Needed because Semina Verbi examines how
   cultures relate to the Gospel.
8. **Popular culture** — only what is genuinely about mass/everyday culture and is not already an art
   form (fandom, sport, fashion, toys and games, internet culture). Comics, video games, flags etc. move to
   their domain (the Jev adjudications decide the contested ones).

Rules that make it "rational": one parent per node; a node's definition says what belongs and what
doesn't; "formal sciences" disappears as a top-level node (mathematics and computer science live in
natural sciences per FOS); every name is a plural or mass noun in natural Wikipedia-style form per
language; English names drop odd forms ("Sculpting" → "Sculpture", "Medicinal science" → "Medicine",
"Video gaming" → "Video games", matching Q47288).

Target size: roughly 120–150 nodes, ~3 levels deep; depth beyond 3 only where a domain needs it (e.g.
film genres are **not** categories — genres are infobox data).

### The Gospel themes tree

Root `Gospel themes` (localized), with groups drawn from the Catechism's structure, so themes don't
overlap and an agent can find the right one:

- **Theological virtues**: faith, hope, charity (love).
- **Cardinal virtues**: prudence, justice, fortitude, temperance.
- **Sin, grace and salvation**: sin, temptation, repentance, conversion, forgiveness, mercy, redemption,
  grace, sacrifice.
- **The human person**: conscience, freedom, dignity of the person, suffering, death, hope of eternal
  life (resurrection), vocation.
- **Communion and society**: family, marriage, friendship, community, work, justice and peace, care for
  creation.
- **Christ and the Church**: incarnation, the Cross, resurrection of Christ, the Eucharist, Mary,
  holiness, prayer.

Existing en Forgiveness / Repentance / Conscience and `Family relationships`, it Perdono / Coscienza /
Famiglia map into it (Family relationships → Family).

### Definitions

Every node gets `"definition"`: one or two English sentences (what belongs, what doesn't), written from
the source framework. Category pages show the localized definition when one exists (translations may be
added later; English is the required one). Jev reads the English definitions.

### Jev (`tools/jev.py`)

Python, `typesafe-sdk` pinned in `requirements.txt`, model pinned to `jev-1.14.0`, key from
`TYPESAFE_API_KEY` (never printed). Three operations, each a function plus a CLI subcommand; all return
the answer, probabilities and confidence, and append to a decisions log when asked to:

- `place(node, candidates) -> Decision` — **Choice**: "Which parent best fits `node` in a classification
  of human culture?" State: the node's name and definition, the root-to-candidate paths, and the
  candidates' definitions; options = candidate parent keys with their definitions. Used for tree
  adjudications.
- `classify(article_text, tree) -> Decision` — **hierarchical Choice** returning **one** field node:
  top level first, then each level below the chosen node (beam width 2 kept only as a fallback when the
  top path's confidence collapses, as in TypeSafe's hierarchical-classification cookbook), stopping at a
  leaf or when the best child is not confident (the article is then filed at the parent level).

**One work, one page, one field (author's decision, 2026-11-17).** A page is filed in exactly one field
category: the work's own medium or the person's main domain. A novel is in Literature; its film
adaptation is a separate page in Film; each links to the other. A work's *subject* (a saint, a biblical
story, a historical event) is expressed by the Gospel themes, by links and by the infobox — not by a
second field category.
- `themes(article_text, themes) -> dict[key, probability]` — **one Noul per theme**, all in one request:
  "Does the article genuinely explore `<theme>` as defined, not merely mention it?"

Policy (in code, tunable, recorded with each decision):
- Choice applied when the top option's probability ≥ 0.80 **and** confidence ≥ 0.6; otherwise "author".
- Theme applied when Noul ≥ 0.75; 0.4–0.75 → "ask the author"; < 0.4 → not applied.

`config/category_decisions.json`: list of `{id, date, question, node, options, probabilities,
confidence, choice, outcome: "applied" | "author", author_choice?}`. The author's choice, when given,
is recorded next to Jev's.

### Migration (`tools/migrate_categories.py`)

- `config/category_migration.json`: per wiki (en, it, fr — the only wikis with old topic categories),
  `old category name → new node key`, or `"split"` when the old category's members belong to different
  new nodes (e.g. en Popular culture): each member is then filed individually with `classify` on its
  text, close calls to the author. A category that still has members can never map to nothing (a test
  enforces it). Maintenance categories are left alone and not listed. Drafted by code from the current
  pages + the new tree, contested entries settled by Jev, reviewed by the author before running.
- An old page with no successor (e.g. `Formal sciences`) is not deletable by the bot: after its members
  are re-filed it becomes `#REDIRECT [[:Category:<nearest node>]]`, and the run lists such pages so the
  author (admin) can delete them if he prefers. A move whose target already exists is not attempted;
  the old page becomes such a redirect and its members are re-filed to the existing target.
- For each mapped category on a wiki: if the new title differs, **move** the category page (leave a
  redirect, so old links resolve), **re-file every member** by replacing its `[[Category:Old…]]` line with
  the new one (sort keys preserved), then the moved page's text is set to the tree's (definition + parent).
  This tool **does** edit existing pages — but only category lines in members and the category pages
  themselves; dry run prints every edit as a diff; edits tagged `seminaverbi-mcp`, summary localized.
- Then `tools.categories check --create` creates every missing node on every wiki and links all six by
  sitelinks (existing en/it items reused; Q47288's en link follows the en move to `Video games`).

### Skill updates

`SKILL.md` gains a **Jev routine** and the one-work-one-page-one-field rule (adaptations get their own
page, linked both ways): before filing an article, run `tools/jev.py classify` and `themes` on the
article text; apply confident answers; show close calls to the author; never tag a theme Jev scores
below 0.4. For a tree question (a new node, a disputed placement), run `place` and record the decision;
the tree changes only through a PR to `categories.json`. `categories.md` (generated) gains definitions.

## Testing

- Tree data: every node has six names, an English definition, unique keys and names, one parent; no
  "Formal sciences"; the size is within 100–200; each existing en/it category with members appears in the
  migration map.
- `tools/jev.py`: request shape (state, options, model pin) and policy against a fake client (applied vs
  author thresholds; beam stops on low confidence; theme bands); key never in output.
- Migration: against fake clients — move + re-file + page text; dry run edits nothing; a member whose
  category line can't be found is reported, not silently skipped; sort keys preserved.
- Live: dry run of the migration on en and it reviewed by the author before the real run; afterwards
  `tools.categories check` reports no missing / wrong parent on all six wikis.

## Left to the builder

- Exact node list and names within the frameworks and size; translations of names.
- Exact prompt wording of the three Jev questions (they must state the definitions and the meaning of
  "genuinely explores").
- Formatting of the decisions log and of dry-run output.
