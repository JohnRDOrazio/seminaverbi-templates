# Translating a SeminaVerbi article

How an article on one wiki becomes the same article on another (en, it, es, fr, de, pt). Pilot:
en:Nicolas Steno → it:Niccolò Stenone (2026-11-20). The rules of the `seminaverbi-editor` skill apply
(`skills/seminaverbi-editor/SKILL.md`, `article-standard.md`, `links.md`, `templates.md`); this file only adds
what translation needs.

## Source and target

- Translate from the most complete version: en or it (read both when both exist, and carry over anything one
  has and the other lacks only if it is sourced). Never translate a translation of a translation when the
  original is there.
- One page per target wiki, connected to the **same item** as the source (sitelink `it`, `es`… on the en
  repository with `wikibase-edit-entity`). Never create a second item.
- Title: the name the subject has in that language (the target-language Wikipedia's title is a good guide; the
  en Wikipedia page's langlinks give it). Songs follow the wiki's convention (en `X (song)`, it
  `X (brano musicale)`); on es/fr/de/pt use `X (canción)`, `X (chanson)`, `X (Lied)`, `X (canção)` only when
  the bare title is ambiguous. Check the title is free on the target wiki first.

## What carries over unchanged

`{{SV infobox}}`, `{{SV bio}}`, `{{SV categories}}`, `{{SV discernment}}`, `{{SV references}}`, `{{SV WIP}}`
(keep it if the source has it; the date stays): the templates localise themselves. Field and themes come
from the item: never add category lines.

## What has to be translated or adapted

- **Prose:** a faithful translation in natural, idiomatic prose of the target language, not a paraphrase and
  not a summary. Every Gospel theme argued in the source must stay argued.
- **`{{SV bio}}`:** `name`, `note` and `rest` are free text: translate them and check the whole opening
  sentence in the preview (it must read as one grammatical sentence).
- **Bible quotes:** same passages, book names and punctuation of the target language
  (en `Mk 10:51-52`, it `Mc 10,51-52`, es `Mc 10,51-52`, fr `Mc 10,51-52`, de `Mk 10,51-52`, pt `Mc 10,51-52`).
  Avoid the tokens another language holds (pt `Gl`, `Jo`, `Jn`, `Ab`, `Br`; fr `He`, `Jc`, `Ab`, `Sg`): the
  forms to write instead are in the skill's Bible references section.
- **Quotations (`{{SV quote}}`):** use the **official text in the target language** when one exists
  (vatican.va publishes papal documents and councils in most of these languages): fetch it and copy it
  verbatim. Only when no official text exists, translate it yourself and say so in the source line
  ("traduzione nostra", "nuestra traducción", "notre traduction", "eigene Übersetzung", "tradução nossa").
  When the quotation's original language is the target language, drop `original`/`original_lang`.
  Never back-translate a translation when the original can be found: go to the original. When it cannot
  (not published, or not online), translate from the published translation the source cites and say so in
  the source line, naming it ("traduzione nostra dalla versione inglese di Lakond", "our translation from
  the Italian").
- **References:** keep the same sources; point to the target-language edition of a document when it exists
  (vatican.va `/it/`, `/es/`…); translate `date`, `publisher`; add `language=` for a source in another
  language. A Wikipedia citation points to the target-language Wikipedia article only when that article
  supports the same claim; otherwise keep the original citation, with `language=`.
- **Links:** every `[[w:en:…]]` becomes `[[w:<lang>:<that language's title>]]`, using the en Wikipedia
  langlinks (API: `prop=langlinks&lllang=<lang>`). No equivalent: leave the text unlinked (or `w:en:` with a
  note to the author). On **it**, Catholic topics link inline to Cathopedia when it has the page (check it:
  Cathopedia titles differ, e.g. `San Gregorio Barbarigo`), and there is no `{{SV cathopedia}}` line; on the
  other wikis `{{SV cathopedia}}` goes under the See also heading of that language.
- **Language variety:** no variety is preferred (Brazilian or European Portuguese, American or British English,
  Spanish of Spain or of the Americas): whoever writes the page chooses, and keeps it consistent within that page.
- **Headings:** translated; See also / External links use the names in `links.md`.
- **New sources:** a reliable source found while translating (official text, scholarship, established press) is not
  discarded: add it to the source page and to every translation, so the versions stay in step.
- **Facts:** do not add facts that are not in the source. If you find an error in the source while checking
  (a quote attributed to the wrong document, a wrong date), fix the source too and report it.

## Routine for each page

1. `get-page` the source on its wiki (keep `latestRevisionId`).
2. Collect the target titles: Wikipedia langlinks, Cathopedia (it), official texts of the quotations.
3. Write the full wikitext to a scratch file.
4. `parse-wikitext` on the target wiki with `title=` the target title, temporarily using `{{SV infobox|item=Q…}}`
   and `{{SV bio|item=Q…|…}}` (the page is not connected yet): check that every template exists, the Bible
   quotes carry the wiki's version, the categories appear, no red interwiki, the opening sentence reads
   well. Remove the temporary `item=` before saving.
   The preview does not show the passage text (the reader's browser fetches it), so it cannot prove that a
   reference names the right book. For each `{{SV Bible quote}}`, check the `data-ref` in the preview
   against the passage you mean: the book name it sends must be that book's in the wiki's language, and no
   abbreviation from the "never write" table in the skill's Bible references section may appear (pt `Jo 3,16`
   is Joel). Once the BibleGet extension renders quotes at parse time, the preview shows the passage text:
   then compare that text with the cited passage instead.
5. `create-page` with a summary in the target language naming the source page.
6. `wikibase-edit-entity` on en: add the sitelink for the target wiki to the item.
7. Report: the URL, anything left unlinked, any quotation translated by us, any fix made to the source.
