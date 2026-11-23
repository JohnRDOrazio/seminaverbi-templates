# Article pipeline, phase 3: music and the visual arts, works only (design)

Issues: #20 (shared design), #22 (music and sacred music), #23 (visual arts and architecture). Builds on
phases 1 and 2 (`2026-11-20-article-pipeline-film-design.md`, `2026-11-20-article-pipeline-literature-design.md`):
the queue, stages 1, 3, 5 and 6, `common.py`, the proposal pages, the ranking page, the vote timer and the
calibration report are unchanged unless this document says so.

## Intent

The film and literature domains fill the proposal list with screen and written works. SeminaVerbi also
covers music (29 articles today) and the visual arts (the Ghent Altarpiece), and the author wants
proposals in both, sacred music and sacred art included. Success: a backlog of music and art proposals,
each a single work, whose connection, field and themes the author would accept or reject on the evidence
shown, with the famous sacred works first and seeds of the Word in secular works not lost among them.

## Decisions taken with the author (2026-11-23)

- **Works, not people, in these domains.** A proposal is generally a single work: a song, an album, a Mass
  or oratorio, a painting, a fresco cycle, an altarpiece, a sculpture, an icon, a window.
- **Artists come later, through one shared source** for every field (music, art, film, literature): an
  artist is proposed when their faith or affinity with the Gospel shows in their own words, life or art.
  That source has its own design; these domains only report the people they meet (calibration) and never
  propose them.
- **Sacred music and sacred art are in scope**, beside works with implicit seeds of the Word.
- **Catholic media will feed these domains** once that source lands (its own design, in parallel). This
  design only provides the hook it needs (below).

## Goals

- `pipeline/domains/music.py` and `pipeline/domains/art.py`, thin modules on `common.py`, with the domain
  interface of phase 2 (`candidates`, `add_calibration`, `calibration_qids`, `triage`, `dossier`,
  `dossier_stage`, `covered`), registered in `pipeline/domains/__init__.py`.
- `music` and `art` blocks in `config/pipeline.json`.
- `python -m pipeline --domain music|art run|calibrate|…` working like film and literature; the ranking page
  gets a "Music" and an "Art" section by itself (it groups by domain).
- A calibration route for art, which has a single existing work to compare against (see Calibration).

## Non-goals

- People (see above). A composer, painter, band or singer reached by any source is reported, not queued.
- **Architecture** (churches, cathedrals, monasteries: #23's last source) and **comics** (`comics` sits under
  visual arts; *New Gods* is filed there): later, each with its own candidate classes and dossier (architecture
  decided 2026-11-23).
- Sources beyond Wikidata and Wikipedia/Wikiquote in this phase: MusicBrainz, CPDL, IMSLP, museum
  open-access APIs (Met, AIC, Smithsonian, Rijksmuseum), Europeana and Iconclass outside Wikidata. #22 and #23
  list them; they are enrichment for a later phase, once the Wikidata backlog has run. Iconclass notations
  *on Wikidata* (P1257) are used (below).
- Commons images on items: that happens at stage 7, when the item is imported, as now.
- Monthly scans of new releases.

## Anti-goals

- A proposal per movement of a Mass, per aria of an oratorio, per panel of an altarpiece or per scene of a
  fresco cycle (the counterpart of seven Narnia proposals).
- A gallery: twenty Annunciations proposed because each depicts the Annunciation. #23: in Christian art the
  filter that matters is **significance**, not eligibility.
- Lyrics copied into a proposal or a dossier beyond the lines Wikipedia itself discusses; any lyric excerpt
  on a proposal stays within the existing 25-word evidence limit. No full lyrics for anything not in the
  public domain (#22).
- Gnostic or esoteric readings of famous works repeated as their meaning (#23): the dossier may carry them,
  the connection judgment must not rest on them.
- A song and its album, or a painting and the cycle it belongs to, proposed as one page when they are two
  works on Wikidata with articles of their own (one work, one page, one field), or proposed twice when one
  is only a part of the other.

## Constraints

As phases 1 and 2: workstation only; only stage 6 writes, as SV editor bot, to the project namespace on en;
Wikidata (CC0) and Wikipedia/Wikiquote read as notes; `TYPESAFE_API_KEY` never printed; at most
`max_new_per_run` candidates entering stage 3 per run; SPARQL through `pipeline.sources.Sparql` (its
User-Agent; one query at a time). Measured while writing: WDQS throttles a generic User-Agent to one
request a minute, so the candidate queries must stay few and bounded (`common.bounded`, `LIMIT` 500).

## Approaches considered

1. **Two thin domain modules on `common.py` (chosen).** `music.py` and `art.py`, each with its sources,
   classes, headings, triage rule, folding rule and question wording, as literature did; the shared stages
   change only where a hook is needed. Consistent with phase 2's decision and the cheapest to review.
2. **One `arts` module for music and art.** Fewer files, but the two share almost nothing beyond `common.py`
   (forms and performers against depicted subjects and collections; lyrics against iconography), and the
   ranking page would mix them.
3. **Museum and MusicBrainz data first.** Richer for art, but new licences, new clients and a second
   identity scheme to map to Wikidata QIDs before the first proposal; Wikidata already carries P180 (depicts)
   and P1257 (Iconclass) for the famous works, which are the ones the floor keeps.

## Design

### Which items each domain owns

Every candidate is a Wikidata item; a domain owns an item by its P31 classes (with `wdt:P279*` closure in
the queries):

- **music**: musical work/composition (Q105543609), song (Q7366), album (Q482994), hymn (Q484692), and the
  classes the builder finds Wikidata uses for single releases and musical settings.
- **art**: painting (Q3305213), sculpture (Q860861), icon (Q132137), altarpiece (Q15711026), reredos and
  retable (Q46686, Q3714446), stained-glass window (Q21061279), mural (Q219423, frescos included), mosaic,
  tapestry and the liturgical goldsmith's works the builder confirms (chalices, reliquaries, crosses).

Each module exposes the owned classes as `WORK_CLASSES`, and a function
`owns(instance_of: Iterable[str], sparql, cache: Path | None = None) -> bool` for a row's stage-0 P31 list.
It is built on `common.owns(instance_of, work_classes, sparql, cache=None)`: the `sparql` client resolves the
subclass closure of `WORK_CLASSES`, and the optional `cache` is the JSON file where closures are kept
(30 days), so a repeated check costs no query. This is the **Catholic media hook**: that source resolves an article to QIDs
and asks each domain whether it owns the item, so it can add the row to the right domain with a source tag
of its own. Nothing else in this design depends on that source.

An item owned by another domain (a hymn text that is a literary work, already a literature row) is reported
and skipped, as literature does today (`_other_domain`). People (Q5), bands and groups are reported and
skipped in every source.

### Candidates, music (stage 0)

Sources, tagged per row:

- **`form`**: works whose form of creative work (P7937), genre (P136) or class (P31/P279*) is a sacred form:
  Mass, Requiem, oratorio (Q85477), Passion (Q781815), cantata (Q174873), motet (Q188285), Magnificat
  (Q200809), Stabat Mater (Q210080), hymn and Christian hymn (Q484692, Q856713), psalm setting, anthem,
  Te Deum, Vespers. Bach's *Mass in B minor* carries none of these in P31 or P136 (checked 2026-11-23), so
  P7937 is the property that matters for the classical repertoire; the builder confirms the QIDs of "Mass"
  and "Requiem" as musical forms and lists every QID in `config/pipeline.json` (`music.forms`).
- **`genre`**: songs and albums in Christian popular genres (P136): gospel (Q180268), contemporary Christian
  music (Q1379958), Christian music (Q6452410), Christian rock (Q753520), and their subgenres the builder
  confirms.
- **`facet`**: `common.facet_patterns` with music's subjects: main subject (P921) or based on (P144) Jesus,
  Mary, the Bible or a book of it (a psalm setting), a saint or a biblical figure. Lyricist (P676) and
  composer (P86) are read for the dossier, not as sources.
- **`artist`**: songs and albums performed (P175), composed (P86) or written (P676) by the artists in
  `music.artists` (QIDs with a comment naming each), as literature's seed authors: a source of their works
  only. Start with the artists SeminaVerbi already covers (Bob Dylan, George Harrison, Eric Clapton, Johnny
  Cash, Fabrizio De André, Fiorella Mannoia, Nek, David Bowie, the Beatles, Genesis, J. S. Bach,
  Tchaikovsky), plus composers of the sacred repertoire (Palestrina, Monteverdi, Handel, Haydn, Mozart,
  Bruckner, Fauré, Duruflé, Pärt, Frisina); the author edits the list by pull request.
- **`calibration`**: the works in the music tree on the six wikis (below).

### Candidates, art (stage 0)

- **`depicts`**: artworks of the owned classes whose depicts (P180) is Jesus (Q302), Mary (Q345), a canonized
  saint (P411), a biblical figure (`biblical_classes`) or a biblical scene or event item (the builder confirms
  the classes: Nativity, Crucifixion, Annunciation, Last Supper and their kind).
- **`iconclass`**: artworks with an Iconclass notation (P1257) starting with `7` (the Bible) or `11`
  (Christian religion), which Wikidata carries for many museum works.
- **`genre`**: artworks with genre (P136) religious art (Q2864737) or Christian art (Q645717).
- **`list`**: a curated list in `config/pipeline.json` (`art.works`, QIDs with a comment) of works with
  seeds of the Word that no facet reaches: famous secular works the author or #23 names (the Vatican Museums'
  highlights; Millet's *The Angelus* is reached by `depicts`, Van Gogh's or Hopper's are not). A listed work
  passes without a floor, as film's seed lists do.
- **`calibration`**: the existing art works, and a benchmark set (see Calibration).

### Folding: parts into their whole

As literature folds a volume into its series, a part folds into its whole when the whole is a work the same
domain owns:

- **music**: a movement, aria, number or chorus with part of (P361) a musical work (the *Kyrie* of the *Mass
  in B minor*, the *Hallelujah* chorus of *Messiah*) folds into that work; the part is recorded on the whole's
  row (`parts`). A **song does not fold into its album**: both are works, and SeminaVerbi already has song
  pages for an album's songs (*Art of Dying*, *Awaiting on You All*, *Hear Me Lord* from *All Things Must
  Pass*). #22's rule stands for the writer, not the pipeline: an album mostly about one song is written on
  the song's page, and the voters see both proposals side by side.
- **art**: a panel, scene or wing with part of (P361) an altarpiece, polyptych, fresco cycle or ceiling of the
  owned classes folds into it (the panels of the Ghent Altarpiece into the altarpiece; the *Creation of
  Adam* into the Sistine Chapel ceiling). Folding stops at a building: a fresco cycle that is part of a chapel
  stays the candidate (architecture is out of scope).
- The literature rules carry over unchanged: the folded part is dropped at triage with `folded into <QID>` if
  an earlier run queued it; a whole already approved, created or declined absorbs nothing; a whole's sitelink
  count for the floor is the highest of its own and its parts'; a part with two wholes stays a candidate of
  its own and the ambiguity is logged.

### Covered (stage 1)

Unchanged (`stages.covered`), plus literature's rule for wholes: a whole is a translation suggestion, never a
proposal, when one of its parts is covered on some wiki.

### Triage (stage 2)

- Drop items that are not works of the domain (a recording, a performance, an edition of a score, a print
  or copy after a painting, a study or sketch, as the builder finds them in P31), with the reason.
- Sitelink floors, per domain: `music.sitelink_floor` and `art.sitelink_floor` (values below, from the
  measured counts). Sources that name a work directly (`list`) pass without a floor; works reached only through a seed artist have
  their own, higher floor (below).

### Quick check (stage 3)

Music: film's mechanics with music's wording (the work need not be religious; an implicit seed in one song,
verse or movement is enough).

Art: most candidates are religious by subject, so plausibility filters nothing. The art question asks for
**significance** instead (#23): whether the work, beyond depicting a religious subject, shapes how the scene
or the faith is imagined or understood, or carries a theological reading in its sources. `quick_drop`
(0.25) applies to that probability (decided 2026-11-23). The wording is a starting point for calibration.

### Dossier (stage 4)

As film and literature, in up to `dossier_languages` languages, starting with the work's original language
when it is one of the six (P407 for a song or album; the country of origin or creator's language for art is
not reliable, so art starts with en, it, then the others), the lead plus:

- **music**: sections on background, composition, writing and recording, lyrics and their meaning, text and
  its sources, structure, music, theme, reception, liturgical use (and the it/es/fr/de/pt equivalents; the
  builder fixes the list). For a whole with parts, each part's page read the same way, headed
  `<part page> / <heading>`.
- **art**: sections on description, subject, iconography, interpretation, symbolism, commission, history,
  analysis, reception (and equivalents). For a whole with parts, each part's page, as for music.
- **From Wikidata (CC0), one note per row**: what the work depicts (P180, labels), its creator (P170),
  composer (P86) and lyricist (P676), its date, its collection or location (P195, P276), and the text it
  sets or is based on (P144). When P144 or P921 points to a book of the Bible or a psalm, the note says so,
  so the writer can quote it with `{{SV Bible quote}}` (#22, #23).
- Wikiquote, as film (the composer's or songwriter's words on a work).

Lyrics: a Wikipedia section on lyrics is read as notes like any other; the proposal shows only the 25-word
excerpt `stages.adjudicate` already limits evidence to. Public-domain liturgical texts (the *Stabat Mater*,
the Mass ordinary) are named by reference in the dossier, not copied.

### Adjudication, proposal, votes

Unchanged, with each domain's wording for the connection choice. The four connection keys stay (the
template and the ranking page expect them); art's text for `entertainment` reads "Christian or biblical
imagery used for decoration, satire or shock, without engaging its meaning". The field is asked from the
root of the tree, as for film and literature. Expected leaves: sacred-music, classical-music, popular-music,
folk-music, opera, musical-theatre; fresco, oil-painting, icon-painting, sculpture, stained-glass, mosaic,
tapestry, goldsmithing, engraving. One work, one field is the work's **medium or form** (the skill's rule):
`sacred-art` sits under religion > Christianity and is for pages on the tradition itself, so a calibration
answer of `sacred-art` for a painting is a disagreement to report.

### Calibration

- **music**: the 20 works in the music tree on the six wikis (2026-11-23): *All Things Must Pass*, *Art of
  Dying*, *Awaiting on You All*, *Hear Me Lord*, *My Sweet Lord*, *What Is Life* (songs); *August*, *John
  Wesley Harding*, *Miserere*, *The Holy Land*, *Ballate per uomini e bestie*, *La buona novella* (albums);
  *Holy Mother*, *Presence of the Lord*, *I.N.R.I.*, *The Prayer*, *Wedding Song (There Is Love)*, *Baba
  Yetu*; Tchaikovsky's *Legend* and *Liturgy of St John Chrysostom*. The 9 people (Bach, Tchaikovsky, Dylan,
  Bowie, De André, Mannoia, Nek, the Beatles, Genesis) are listed as skipped, for the artists source.
- **art**: one work exists (the Ghent Altarpiece; Rubens is a person, *New Gods* is comics). One row cannot
  calibrate a significance question, so art adds a **benchmark**: about 20 works in `art.benchmark` that the
  author approves before the run, mixing works that should pass strongly (Rublev's *Trinity*, the Isenheim
  Altarpiece, Leonardo's *Last Supper*, Michelangelo's *Pietà*, Caravaggio's *Calling of Saint Matthew*,
  Rembrandt's *Return of the Prodigal Son*, Millet's *The Angelus*), works with only implicit seeds, and
  controls that should not (a work using Christian imagery for shock; a secular work with no connection).
  Benchmark rows run stages 3–5 as calibration rows and appear in the report; unlike calibration rows they
  are not articles, so stage 1 lists no translation suggestion for them, and once the author has read the
  report `python -m pipeline --domain art release-benchmark Q… Q…` clears the calibration flag of the works
  the author names, so they become proposals at the next `publish` (they have already passed stage 5); the
  controls and the unnamed rows stay benchmark rows and are never published (decided 2026-11-23).
- The author reads each domain's report before its first proposal is posted, as for film and literature;
  wording and cut-offs change only through a pull request afterwards, and a change re-asks that domain's
  rows only.

### Configuration

`music` and `art` blocks in `config/pipeline.json` beside `film` and `literature`: `forms`, `genres`,
`subjects`, `artists`, `sitelink_floor` (music); `classes`, `depicts`, `iconclass_prefixes`, `genres`,
`works`, `benchmark`, `sitelink_floor` (art). A domain key overrides the top-level key of the same name for
that domain only (`common.domain_config`).

### Infobox types (for stage 7)

`config/infobox_types.json` has the `composition`, `song` and `album` layouts mapped, and an `artwork`
layout whose fit is visual-arts, crafts and sacred-art but **no local type item mapped to it**. When the
first art article is written, the local items for painting, sculpture, icon, altarpiece and stained-glass
window (imported then) are mapped to `artwork` by pull request. Not a pipeline change; noted so stage 7
does not stall.

## Measured candidate counts (Wikidata, 2026-11-23)

Measured on QLever's Wikidata mirror (`qlever.dev/api/wikidata`, read-only, for sizing only: WDQS throttled
a generic User-Agent to one request a minute and every count timed out there). Distinct items, by sitelink
count:

| Set | all | ≥ 5 | ≥ 10 | ≥ 15 | ≥ 20 | ≥ 30 |
|---|---|---|---|---|---|---|
| music `form`: Mass (Q217295), Requiem music (Q6941842), oratorio, Passion, cantata, church cantata, motet, hymn, Christian hymn, Magnificat, Stabat Mater, psalm setting, litany, carol, hymn setting (P7937, P136 or P31) | 13,232 | 782 | 321 | 139 | 92 | 48 |
| music `genre`: gospel, CCM, Christian music, Christian rock (P136), songs/albums/works | 3,802 | 133 | 52 | 19 | 11 | 5 |
| music `facet`: P921/P144 Jesus, Mary, Bible, Christianity, a book of the Bible, a saint | 75 | 16 | 4 | 3 | 3 | 0 |
| music `award`: GMA Dove Award categories (P361 Dove) on works | 1 | 0 | 0 | 0 | 0 | 0 |
| music, union of form + genre + award | 17,080 | 941 | 389 | 167 | 106 | 53 |
| music `artist`: songs/albums/works of the 12 calibration artists (QIDs not yet confirmed) | 3,032 | 1,295 | 687 | 399 | 190 | 53 |
| art `depicts` Jesus or Mary | 20,225 | 894 | 297 | 127 | 66 | 19 |
| art `depicts` a canonized saint | 23,680 | 1,119 | 355 | 148 | 77 | 25 |
| art `genre` religious or Christian art | 81,464 | 1,589 | 490 | 194 | 94 | 26 |
| art `iconclass` 7… or 11… (P1257) | 25,193 | 1,047 | 380 | 159 | 78 | 19 |
| art, union of the four | 88,717 | 1,750 | 560 | 231 | 115 | 36 |
| scale: all paintings and sculptures | 1,217,594 | 6,457 | 2,015 | 883 | 434 | 144 |

What the numbers decide (builder's calls, from these counts):

- **Floors:** `art.sitelink_floor` 15 (231 candidates before folding and the significance check);
  `music.sitelink_floor` 10 for `form`, `genre` and `facet` (389), and 20 for works reached only through a
  seed artist (190: at 15 the Beatles' and Bach's catalogues alone pass 399). A domain key per source,
  `music.artist_sitelink_floor`, carries the second value.
- **Mass and Requiem** are forms in P7937 (Q217295, Q6941842), as the *Mass in B minor* shows; "hymn tune"
  (Q5956785, 25,612 items, mostly hymnal entries) is left out of the forms.
- **The Dove Awards are dropped as a source:** Wikidata records them on artists, not works (one work).
  #22's other popular sources (artists, genres) cover the ground.
- **P921/P144 is thin for music** (75 items): kept, because the hits are exactly the settings the author
  wants (psalm settings, works on a saint), but it is not what fills the list.
- `max_new_per_run` (200) bounds each run's Jev spend whatever the floors let through.

## Settled details (builder's calls; the author may overturn any)

- **Module shape:** two modules, `owns()` and `WORK_CLASSES` as the only new interface, used by the media
  source later and by `_other_domain` now. A row stores only its direct P31 classes, so `owns()` reads the
  subclass closure of `WORK_CLASSES`, fetched once per run with one SPARQL query per class and cached in
  `data/` with the date.
- **Query weight:** no `wdt:P31/wdt:P279*` over the forms or the art classes in a candidate query (the
  closure query above supplies the subclasses as a `VALUES` list); every candidate query goes through
  `common.bounded`. If WDQS keeps the pipeline's User-Agent at one request a minute, a run makes about 15
  queries per domain, which is acceptable; QLever is not used by the pipeline.
- **Folding:** P361 only, into a whole of the same domain's classes, never into a building; songs never fold
  into albums.
- **Art quick check:** a significance question replaces plausibility for art; music keeps plausibility.
- **Benchmark:** a list in config, flagged in the row's stage-0 data (`benchmark: true`), released by command.
- **Sources this phase:** Wikidata and Wikipedia/Wikiquote only; Iconclass via Wikidata's P1257.
- **Seed artists:** the 12 calibration artists plus 10 composers of the sacred repertoire, by QID in config.

## Left to the builder

- The exact form, genre, class and scene QIDs (checked on Wikidata), and the subclass closures that stay
  under the WDQS timeout.
- The section headings per language for music and art.
- The Jev wording for both domains (calibration tests it), within the shape above.
- Test doubles, following the existing domain tests.

## Answered by the author (2026-11-23)

1. **Architecture: later**, as its own domain. `art` stops folding at a building.
2. **Art's quick check is significance**, for art only: does the work shape how the scene or the faith is
   imagined or understood, beyond depicting a religious subject. Music keeps plausibility.
3. **Art benchmark:** the builder drafts about 20 works (QIDs: strong, implicit, and two or three controls)
   in `art.benchmark`; the author approves the list before the first live art run. Benchmark rows are released
   one QID at a time, so the controls never become proposals. Approval is an open item before the live run,
   not a blocker for building.
