# Sacred architecture: a pipeline domain (short design)

Issue #23 (visual arts and architecture), last source. Builds on the music and art domains (#57) and the shared
`common.owns` contract. Decided with the author on 2026-11-23.

## Intent

SeminaVerbi should be able to propose sacred buildings of artistic value, such as Gaudí's Sagrada Família:
churches, cathedrals, basilicas, chapels, abbeys and monasteries whose architecture, art and symbolism carry the
Gospel. Until now `art` stops at a building (`STOP_CLASSES`) and the music/art design put architecture "later,
as its own domain". This is that domain.

## Goals

- An `architecture` domain with the same stages as art: candidates (stage 0), coverage (1), triage (2), quick
  significance check (3), dossier (4), adjudication (5), publication (6).
- Pages are filed under the existing field node **Sacred architecture** (`sacred-architecture`; item Q48079 on the SeminaVerbi Wikibase, not Wikidata).
- The press routes buildings here (today a building mention has no domain).

## Non-goals

- Secular architecture, landscape architecture and urbanism.
- Folding art into buildings: a fresco cycle or altarpiece in a chapel stays an `art` work (Giotto's
  *Lamentation* stays in art); `art.STOP_CLASSES` is unchanged.
- People (architects): the artists source (project 3).

## Approaches

1. **Its own domain (chosen).** Own candidate classes, significance wording, dossier sources and field node.
2. Inside `art`, by allowing buildings as works: quicker, but pages would file under visual arts and the dossier
   would ask a painting's questions of a cathedral. Rejected.

## Design

- **Classes (`WORK_CLASSES`).** The closure of church building (Q16970), cathedral (Q2977), basilica (Q163687),
  chapel (Q108325), abbey (Q160742) and monastery (Q44613), with `common.owns` as art does. Measured on
  2026-11-23: 951 items typed directly with one of these classes have ≥ 15 sitelinks (all Wikimedia sites);
  23 have ≥ 50 (Notre-Dame
  de Paris 125, Sagrada Família 110, Sistine Chapel 90...).
- **Candidates (stage 0).** Buildings of those classes with a Christian religion (P140) or a Christian
  dedication, above `sitelink_floor` (15, like art), ordered by sitelinks, in bounded queries as art's; plus
  `works`, a hand list (the Sagrada Família, Q48435, first). A UNESCO World Heritage designation (P1435) is a
  second source below the floor.
- **Triage (2).** As art: the floor, with the press exemption (≥ 0.60), and the queue's other-domain guard (a
  building already queued under art stays there). The floor is also waived for a candidate from the UNESCO source or
  from `works`, as literature waives it for sources that name a work directly; it still meets the significance check.
- **Quick check (3): significance.** Jev's `plausible` wording for architecture:
  > Does the building described in `text` shape how the Christian faith is lived or understood (through its
  > liturgy, as a place of pilgrimage, or through a theology expressed in its forms), or express the beauty of
  > the faith through the richness and quality of its art and symbolism, beyond being a well-known religious
  > building? A building that set how a church is imagined, or whose art and symbolism carry a theological
  > reading discussed in its sources, counts; an ordinary parish church, however old, does not.
- **Dossier (4).** Wikipedia sections on the architecture, the iconographic and symbolic programme, the art it
  holds, its history (commission, architect, consecration) and its liturgical use; Wikidata facts: architect
  (P84), architectural style (P149), religion (P140), dedication (P825), location (P131), inception (P571),
  heritage designation (P1435).
- **Calibration.** The existing sacred-architecture pages, and a benchmark like art's (held back, released by
  command): strong (Sagrada Família, Chartres, Hagia Sophia...), weak (well-known but ordinary churches) and
  controls (famous secular buildings). The builder drafts it; the author approves it before the first live run.
- **Press.** `press_stage0` returns nothing (a building has no parts to fold); `link.class_route` routes the
  building classes to `architecture`.

## Left to the builder

The exact candidate queries and their bounds (measured on live WDQS, as for art), the heading lists for the
dossier, the benchmark draft, and the infobox type mapping if a building type has no layout yet.

## Order

After the press fixes (Wikidata retries, section pages, the "discussed or only named" check, the type check), so
architecture starts from clean press data.
