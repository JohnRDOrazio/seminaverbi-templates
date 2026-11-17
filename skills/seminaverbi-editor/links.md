# Links on the SeminaVerbi wikis

All six wikis share **one** interwiki table. Prefixes without a language point to English projects on
every wiki: `w:` and `wikipedia:` → en.wikipedia.org, `wikisource:` → en.wikisource.org, `wiktionary:` →
en.wiktionary.org, `seminaverbi:` → the en SeminaVerbi wiki. `cathopedia:` → it.cathopedia.org.
So **always write the language**.

## Internal (same wiki)

`[[Il Signore degli Anelli]]`, `[[Il Signore degli Anelli|il romanzo]]`. Link a category inline with a
leading colon: `[[:Categoria:Musica|musica]]` (any wiki also accepts `[[:Category:…]]`). A bare
`[[Category:X]]` puts the page *in* the category instead.

## Wikipedia

Always `[[w:<lang>:Title|text]]`, `<lang>` being the page's own wiki language, and new text uses it on en
too (`w:en:`), for uniformity:

| wiki | example |
|---|---|
| en | `[[w:en:Second Vatican Council\|Second Vatican Council]]` |
| it | `[[w:it:Concilio Vaticano II\|Concilio Vaticano II]]` (existing it pages use `[[wikipedia:it:…]]`, same thing) |
| es | `[[w:es:Concilio Vaticano II\|Concilio Vaticano II]]` |
| fr | `[[w:fr:Concile Vatican II\|concile Vatican II]]` |
| de | `[[w:de:Zweites Vatikanisches Konzil\|Zweiten Vatikanischen Konzil]]` |
| pt | `[[w:pt:Concílio Vaticano II\|Concílio Vaticano II]]` |

Before linking, confirm the article exists on that language's Wikipedia (and use its exact title). If it
does not, link the English article with `w:en:` and tell the author, or leave the text unlinked.

## Cathopedia

Cathopedia is the Catholic encyclopedia, in Italian. It gets a prominent, consistent place:

- **it**: link Catholic topics inline to Cathopedia, in preference to Wikipedia, when Cathopedia has the
  article: `[[cathopedia:Concilio Vaticano II|Concilio Vaticano II]]`. Check that the page exists first.
- **en, es, fr, de, pt**: inline links go to the reader's-language Wikipedia; the Catholic topic also gets
  a line with `{{SV cathopedia|<Italian title>|<text in the wiki's language>}}` under the "See also"
  section (or "External links" when the section lists only external sites). It adds "(Cathopedia, in
  Italian)" in the wiki's language and a tracking category. Heading names:

| wiki | See also | External links |
|---|---|---|
| en | See also | External links |
| it | Voci correlate | Collegamenti esterni |
| es | Véase también | Enlaces externos |
| fr | Voir aussi | Liens externes |
| de | Siehe auch | Weblinks |
| pt | Ver também | Ligações externas |

## Other SeminaVerbi wikis

Inline: `[[:it:Il Signore degli Anelli|the Italian article]]` — the **leading colon** matters: without it
the link moves to the sidebar's language list. Translations of the same article are connected through
Wikibase sitelinks, never with `[[it:…]]` lines at the bottom of the text.

## Other sources

`newadvent`, `vatican` and similar sites are not registered prefixes: use plain external links,
`[https://www.vatican.va/… Lumen Gentium, n. 16]`, or `{{SV cite}}` in the references.
