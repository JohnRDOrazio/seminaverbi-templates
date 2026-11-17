---
name: seminaverbi-editor
description: Use when reading, writing, porting, translating or categorizing pages on the SeminaVerbi wikis ({en,it,es,fr,de,pt}.seminaverbi.bibleget.io) through the seminaverbi MCP server, or when running the repository's category helper.
---

# SeminaVerbi editor

You edit the six SeminaVerbi wikis as **SV editor bot** through the `seminaverbi` MCP server (wiki keys
`en it es fr de pt`). Edits go live immediately, flagged as bot edits and tagged `seminaverbi-mcp`; the
author is driving and reviews as you go, so report every save.

Source of truth for templates, categories and these notes: the repository
`~/development/seminaverbi-templates` (github.com/JohnRDOrazio/seminaverbi-templates). Never edit `SV …`
templates or `Module:SV/…` on a wiki: they are overwritten by the sync. Change them in the repository.

## Reference files (read the ones the task touches)

- `article-standard.md`: what a SeminaVerbi article is, and must contain. Read before writing article prose.
- `links.md`: internal, Wikipedia, Cathopedia and cross-wiki links. Read before writing any link.
- `templates.md` (generated): every SV template, its parameters, aliases and per-wiki defaults.
- `categories.md` (generated): the only categories you may write, per language.

## Edit routine (every save)

1. Fetch the current page (`get-page`) on the right wiki and keep its `latestRevisionId`. If someone other
   than you edited it since the author last looked, show the author the change before overwriting anything.
2. Write the full wikitext.
3. Check it with `parse-wikitext` on that wiki, and fix before saving:
   - every template it reports is an `SV …` template from `templates.md`, or one that already exists on
     that wiki (check with `get-page` on `Template:<name>` if unsure); never introduce Wikipedia templates;
   - no link to `w:`/`wikipedia:` without a language on a non-English wiki (see `links.md`);
   - the article calls `{{SV infobox}}` or `{{SV categories}}`, and has no `[[Category:…]]` line for a
     tree category (field and themes come from the item, below); any other category you write is in
     `categories.md`; the SV templates' own maintenance and tracking categories (`{{SV WIP}}`,
     `{{SV cathopedia}}`, the infobox's tracking categories…) are fine;
   - every Gospel theme on the item is argued in the text (see Categories);
   - every Bible verse named or alluded to is quoted with `{{SV Bible quote|ref=…}}`;
   - red links only for articles that are planned; say which ones you left.
4. If a tree category the page needs does not exist yet on that wiki, create it with the helper (below),
   never by hand.
5. Save with a short summary **in the wiki's language**: `create-page` for a new page; `update-page` with
   `latestId` set to the `latestRevisionId` from step 1, so the server refuses the save if anyone edited
   the page in between (then go back to step 1).
6. Tell the author what you changed and give the diff link.

## Categories

- Only the names in `categories.md` (a field tree of the arts and sciences, and the Gospel themes in six
  groups). A new node needs a pull request to `config/categories.json` first (then
  `python -m tools.skilldocs` and a sync); ask the author.
- **One work, one page, one field.** A page gets exactly one field category: the work's own medium or
  form, or the person's main domain. A novel goes in its literature category; its film adaptation is a
  separate page in the film category; the two pages link to each other. A work's subject (a saint, a
  biblical story) is carried by the themes, the links and the infobox, never by a second field category.
- **Field and themes are statements on the article's item**, not category lines: `SV field` once (the
  one field node), `SV Gospel theme` once per theme, each pointing to the node's item (the `item` in
  `config/categories.json`). Write them with `wikibase-add-statement` on en. `{{SV infobox}}` (or
  `{{SV categories}}` on a page without an infobox) outputs the categories on every language's page,
  within about a minute of the statement.
- Themes: only leaf themes (never a theme group), only those the text genuinely explores. **The page
  must elaborate how the work or person relates to each Gospel theme**, with the scenes, lyrics or
  passages that show it. When it does not, add a line to `docs/content-tasks.md` (in the repository):
  either research and enrich the text, or remove the theme statement when the relationship cannot be
  clearly established. Tell the author which you propose.
- Create missing tree categories, with their cross-language links, from the repository root:
  `.venv/bin/python ~/.config/seminaverbi/runeditor.py tools.categories check --wiki <code> --create`
  (`--dry-run` first if the author wants to see the list). You may run it without asking each time; it
  never edits an existing page. Without `--create` it only reports.

## Filing with Jev

Jev (TypeSafe's System One model) gives calibrated judgments; code applies them by fixed thresholds.
Run these from the repository root (`TYPESAFE_API_KEY` is in the environment; never print it), with the
article's final wikitext saved to a temporary file:

1. **Field:** `.venv/bin/python -m tools.jev classify <file>` returns one field node and an outcome.
   `applied` (every step down the tree at probability ≥ 0.80 and confidence ≥ 0.60): use it.
   `author`: show the author Jev's choice and probabilities and wait for the author's decision.
2. **Themes:** `.venv/bin/python -m tools.jev themes <file>` gives a probability per leaf theme.
   ≥ 0.75 `applied`: add it. 0.40–0.75 `author`: list them for the author with the probabilities.
   < 0.40: never add it.
3. **Tree questions** (a new node, or where an existing one belongs):
   `.venv/bin/python -m tools.jev place <node-key> <candidate-parent-key>... --record`. The answer is
   logged in `config/category_decisions.json`; an `author` outcome goes to the author, whose ruling is
   recorded next to Jev's (`author_choice`). The tree changes only through a PR to `categories.json`.

Jev's `applied` field and themes go on the item as statements (above); `author` outcomes still wait for
the author. Jev judges; it does not write. Never let a Jev answer override the author, and when Jev is unavailable,
ask the author instead of guessing.

## Wikibase (items live on en)

- Translations and categories are linked across languages through **sitelinks** on the item, never with
  `[[fr:…]]` lines in the text. Use `wikibase-get-entity` / `wikibase-edit-entity`.
- The SeminaVerbi wikis' site IDs are `en`, `it`, `es`, `fr`, `de`, `pt`. Never `enwiki`, `itwiki`…:
  those are **Wikipedia**. The repository mirrors Wikidata items, Wikipedia sitelinks included.
- **A new article first finds its subject's item:** `wikibase-search-entities` on en (the mirror holds
  about 13k Wikidata items), then add the SeminaVerbi sitelink (`en`, `it`…) to it. Create an item only
  when none exists.
- On items, change only sitelinks, labels, descriptions, `SV field` and `SV Gospel theme`, unless the
  author asks in this session for another statement change: other statements feed the infoboxes and are
  filled from Wikidata by `EnrichItems.php`.

## Never

- Delete, protect or move pages the author did not ask about (the bot lacks delete/protect rights anyway).
- Read, print or copy the bot password (`~/.config/seminaverbi/editor.env`).
- Copy Wikipedia text wholesale; write for SeminaVerbi's purpose (`article-standard.md`).
