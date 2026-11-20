# Article pipeline, phase 2: literature (design)

Issues: #20 (shared design), #24 (literature). Builds on phase 1
(`2026-11-20-article-pipeline-film-design.md`): the queue, stages 1, 3, 5 and 6, the proposal pages,
the ranking page, the vote timer and the calibration report are unchanged unless this document says so.

## Intent

The film and television pipeline is live (109 proposals on en, 2026-11-20). Literature is next: novels,
poems, plays, short fiction, essays and children's books that carry seeds of the Word, proposed on the
same queue and voted on the same way. Success: a backlog of literature proposals whose connection,
field and themes the author would accept or reject on the evidence shown, with series proposed once
and judged across their whole arc.

## Decisions taken with the author (2026-11-20)

- **Works only.** Authors are not proposed in this phase; author pages wait for #26 (people), which
  needs a different dossier (biography, conversion, letters on faith). Seed authors are only a source
  of their works.
- **A series is one page.** A volume that belongs to a literary series is folded into the series: the
  series is the candidate and the proposal, and its themes are read across the whole arc, as the
  television seasons are (the existing it pages *Harry Potter* and *Il Signore degli Anelli* already
  treat the whole work as one page).
- **Shared helpers plus a thin literature module** (approach 1 below).
- **Seed lists include Albino Luciani's *Illustrissimi*** (1976): the 40 letters to writers,
  characters and saints, beside Pope Francis's 2024 letter on literature.

## Goals

- `pipeline/domains/common.py`: the parts of `film.py` that are not film-specific, used by both domains.
- `pipeline/domains/literature.py`: candidates, triage, dossier and calibration for literature.
- Per-domain wording for Jev's quick and connection questions (today's text mentions television series
  and episodes).
- `python -m pipeline --domain literature run|calibrate|…` works like film; the vote timer and the
  ranking page cover both domains.

## Non-goals

- Proposing authors (see above), or any domain other than literature.
- Reading full texts (Project Gutenberg, Wikisource) in the dossier. A novel runs to 500,000 characters
  or more, against the 5 × 20,000 that adjudication reads; full texts are for the writer at stage 7.
- Open Library: the Wikipedia lead already serves the quick check.
- Changing film behaviour. The film tests pass unchanged after the move to `common.py`.

## Anti-goals

- A proposal per volume of a series (seven Narnia proposals).
- Themes argued from a book's reputation rather than its scenes (#24: children's and fantasy
  literature need the clearest evidence).
- A candidate that is an edition, a translation or a publisher's series rather than a work.
- Proposing the film adaptation here, or the novel under film: one work, one page, one field.

## Constraints

As phase 1: workstation only; only stage 6 writes, as SV editor bot, to the project namespace on en;
Wikidata CC0 and Wikipedia/Wikiquote read as notes; quotations short (public-domain works may be quoted
from the original or an open translation in the article, not in the proposal); `TYPESAFE_API_KEY` never
printed; at most `max_new_per_run` candidates entering stage 3 per run.

## Approaches considered

1. **Shared helpers plus a thin literature module (chosen).** Move the generic parts of `film.py`
   (SPARQL building and paging, row data from Wikidata, page-to-QID mapping, calibration by field keys,
   Wikipedia section reading in the original language, following "has part" to sub-pages) into
   `common.py`. Each domain keeps its sources, headings, triage rule, sub-page rule and question wording.
   More work now; later domains (#22–#26) get cheaper.
2. **Copy `film.py` and adapt it.** Fastest, but about 200 duplicated lines per domain, and fixes (like
   the `Episode_list` one) must be made in every copy.
3. **One generic domain driven by JSON.** The seed sources differ too much (a Wikipedia list page, author
   QIDs, a papal letter) to fit one shape without special cases.

## Design

### Domain interface

A domain module provides what `film.py` provides today: `candidates`, `add_calibration`,
`calibration_qids`, `triage`, `dossier`, `dossier_stage`, plus a `QUESTIONS` mapping with its wording
for the quick check and the connection question (and its seed description). `judge.py` takes the wording
from the row's domain; film's current text moves to `film.QUESTIONS` unchanged, so film's cached Jev
answers stay valid.

### Candidates (stage 0)

Every candidate is a Wikidata item that is a literary work (instance of a subclass of Q7725634) or a
literary series; anything else a seed leads to is reported, never guessed. Sources, recorded per row as
film does:

- **Seed authors** (`literature.authors` in `config/pipeline.json`, QIDs with a comment naming each):
  their works by P50 (author). Start: Dante, Manzoni, Chesterton, Tolkien, C. S. Lewis, Waugh, Greene,
  Flannery O'Connor, Bernanos, Undset, Endō, Péguy, Claudel, Mauriac, Dostoevsky, plus authors with a
  documented conversion; the author edits the list by pull request.
- ***Illustrissimi***: read from the it.wikipedia article *Illustrissimi*, section "I personaggi di
  Illustrissimi" (a numbered list of 40 links), each link resolved to its Wikidata QID:
  - a human (Q5) → their works by P50 (Dickens, Twain, Goethe, Scott, Petrarch, Goldoni, Belli,
    Trilussa, Marlowe…; saints such as Thérèse of Lisieux or Francis de Sales → their writings);
  - a fictional character → the work by P1441 (present in work): Pinocchio, Figaro, Penelope, Chichikov;
  - a work → itself (*The Pickwick Papers*, when the link is the work);
  - anything else (Marconi, Hippocrates, King David, Jesus: no literary work) → reported.
- **Pope Francis, letter on the role of literature in formation (17 July 2024)**: a curated list in
  `config/pipeline.json` (`literature.francis_2024`) of the authors and works the letter names, each
  with its QID, read by hand from the vatican.va text when building; authors → works as above.
- **Wikidata facets** (SPARQL, as film): based on (P144) the Bible or a book or story of it; main
  subject (P921) Jesus, the Bible, Christianity, a saint (P411) or a biblical figure; genre (P136)
  Christian literature, Christian allegory, hagiography. The exact QIDs go in config, checked on
  Wikidata when building.

**Series folding.** When a candidate work has P179 (part of the series) pointing to an item that is a
literary series (an instance of a subclass of Q1667921, *novel series*, or of the classes the builder
finds Wikidata uses for book series of one author), the series is added as the candidate in its place,
carrying the volume's sources; the volume is recorded on the series row (`volumes`) and not added on its
own. Publisher and imprint series (Penguin Classics and the like) are not literary series and never
fold. A series item reached directly (a facet hit on *The Chronicles of Narnia*) is a candidate as is.

Folding happens in stage 0, before a row is inserted (builder's calls, 2026-11-20):

- the volume list goes in the series row's stage-0 outcome data (`volumes`), extended when a later run
  reaches another volume; no new column;
- a volume with no P179 to a literary series, or with more than one, stays a candidate of its own and
  the ambiguity is printed in the run log;
- a volume already in the queue as its own row (an earlier run) is dropped at triage with the reason
  `folded into <series QID>`; a series already approved, created or declined absorbs nothing new;
- a series with no Wikipedia article of its own borrows, for the quick check and the dossier lead, the
  page of its volume with the most sitelinks; its sitelink count for the floor is the highest of its own
  and its volumes'.

### Covered (stage 1)

Unchanged. A series is covered when the series item has a SeminaVerbi sitelink, or when a volume does
and the series does not (then it is a translation or a restructuring question: report it under
translation suggestions with a note, never propose it).

### Triage (stage 2)

- Drop editions and translations (instances of Q3331189, version, edition or translation) and anything
  not a work (as film drops episodes and trailers).
- Sitelink floor `literature.sitelink_floor` (start 15, as film): it applies to every work reached
  through an author (seed authors, and the writers *Illustrissimi* and the Francis letter address), to
  facet-only candidates and to series. Only a work a source names directly (a character's work, a work
  link in *Illustrissimi*, a work the Francis letter cites) passes without a floor, as film's seed lists
  do. Otherwise Dickens or Goethe alone would put hundreds of minor pieces in the queue.

### Quick check (stage 3)

Unchanged mechanics (Jev on the Wikipedia lead, drop below `quick_drop`), with literature's wording: the
work need not be religious; an implicit seed in one storyline, character or poem is enough.

### Dossier (stage 4)

As film, with literature's sections, in up to `dossier_languages` languages starting with the work's
original language (P407, language of work), or, when that is not one of the six (Russian, Norwegian,
Japanese, Latin…), with the six wikis' languages in film's order of preference:

- the Wikipedia lead and the sections whose headings match literature's list per language (plot,
  synopsis, summary, content, structure, themes, analysis, interpretation, religious or Christian
  themes, and their it/es/fr/de/pt equivalents: trama, contenuto, struttura, temi, analisi; argumento,
  temas; intrigue, résumé, thèmes; Handlung, Inhalt, Themen, Deutung; enredo, temas…; the builder fixes
  the list);
- for a series, each volume's page (its `volumes`, or the series' P527 has part) read the same way,
  headed `<volume page> / <heading>`, as season pages are;
- Wikiquote, as film.

A Wikisource or Gutenberg page, when Wikidata has one for the work, goes into the proposal's sources as
a link for the writer, not into the dossier.

### Adjudication, proposal, votes

Unchanged: themes read over the whole dossier in parts, a theme kept at 0.50 (at most 8), a direct
connection at 0.80 passes without a theme. The field is asked from the root of the tree, exactly as for
film (no change to `tools.jev classify`, so film's cached answers stay valid): most works will land under
literature, but a theological treatise reached through a saint belongs under its own field, and Jev must
be free to say so; calibration shows whether it does. Proposal pages, the ranking
page (grouped by domain) and the vote timer need no change beyond any domain parameter they lack.

### Calibration

The existing articles in the literature tree on all six wikis that are works: today *Harry Potter*,
*Il Signore degli Anelli*, *La storia infinita*, *Pinocchio* (it) and *The Lord of the Rings* (en).
Pages about people (Chesterton) are listed in the report as skipped. The set is small; the author reads
the report before the first literature proposal is posted, as for film.

### Configuration

A `literature` block in `config/pipeline.json` beside `film` (authors, `francis_2024`, facets, series
classes, `sitelink_floor`); a domain key overrides the top-level key of the same name for that domain
only, so film is untouched. Domain functions receive the whole config, as film's do.

### Not a duplicate

A novel and its film adaptation are two QIDs and two articles in two fields (one work, one page, one
field), so the same story may appear once under film and once under literature on the ranking page.
Their en Wikipedia titles usually differ ("… (novel)", "… (film)"); a clash gets the QID suffix as now.

### Reporting

Anything a seed leads to that is not a literary work (an *Illustrissimi* addressee with no works, a
link to a disambiguation page, a Francis entry with no QID) is printed in the `candidates` run log, as
film prints unresolved Vatican titles. The wording of the literature questions changes only through a
pull request after the author reads the calibration report, as for film; a change re-asks literature
rows only.

## Left to the builder

- What exactly moves into `common.py`, as long as film's behaviour and tests are unchanged.
- The literary-series classes and the facet QIDs, checked on Wikidata.
- The heading lists per language.
- The Francis 2024 list: read the letter on vatican.va and record each author and work it names, with
  its QID, in config (a research step at the start of the build).
- The literature question wording, which calibration then tests.
