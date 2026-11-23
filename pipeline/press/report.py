"""What each outlet has given, for the author to review: nothing here removes an outlet.

A candidate, a proposal or an enrichment entry is credited to the outlet where the article was read, whichever outlet
its evidence counts for in the queue (a blog's post that counts as the newspaper's is the blog's here: reading the blog
gave it), and, when the same article was found in both, to both: either would have given it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pipeline.press import store as press_store

REVIEW, DROP = 30, 60  # days since the first successful read without a useful proposal
PUBLISHED = ("open", "approved", "created")


@dataclass
class Figures:
    first_read: str | None = None  # the first day anything but robots.txt was read (UTC)
    days: int = 0  # calendar days since
    days_read: int = 0
    read: int = 0  # articles read and judged, or recognised as a copy of one
    kept: int = 0
    candidates: int = 0
    proposals: int = 0  # published and not declined
    approved: int = 0
    created: int = 0
    enrichment: int = 0
    counted_as: tuple[str, ...] = ()  # the other outlets some candidates count for as evidence
    counted_elsewhere: int = 0
    entries: bool = True
    configured: bool = True
    removed: str | None = None

    @property
    def mark(self) -> str | None:
        """What to say of an outlet read long enough without a useful proposal; None for a removed one."""
        if not self.first_read or self.proposals or self.removed or not self.configured or self.days < REVIEW:
            return None
        return f"no useful proposals for {self.days} days: " + ("consider dropping" if self.days >= DROP else "review")


def figures(store, queue, outlets: list[dict]) -> dict[str, Figures]:
    """{outlet: its figures}, the configured ones first, then those only the store knows."""
    known = {o["id"]: o for o in outlets}
    reads, removed = store.reads(), store.removed()
    articles = store.db.execute("SELECT outlet, stage, outcome, data_json LIKE '%\"duplicate\"%' "
                                "FROM press_articles ORDER BY outlet").fetchall()
    ids = dict.fromkeys([*known, *sorted({r[0] for r in articles} | set(reads) | set(removed))])
    out = {oid: Figures(entries=bool(known.get(oid, {}).get("entries")), configured=oid in known,
                        removed=removed.get(oid, {}).get("on")) for oid in ids}
    today = date.fromisoformat(press_store.utc_today())
    for oid, (first, days) in reads.items():
        out[oid].first_read, out[oid].days_read = first, days
        out[oid].days = (today - date.fromisoformat(first)).days
    for outlet, stage, outcome, duplicate in articles:
        f = out[outlet]
        # one kept by filter that link then found gone was read too
        f.read += outcome in ("keep", "drop") or (outcome == "skip" and (duplicate or stage == "linked"))
        f.kept += outcome == "keep"
    given: dict[tuple[str, str], dict[str, set[str]]] = {}  # (route, outlet) -> {qid: the outlets its evidence counts for}
    for route in ("candidate", "enrichment"):
        for m in store.mentions(route=route):
            art = store.article(m.url)
            for oid in {art.outlet, *(c.outlet for c in store.copies(m.url))} if art else ():
                given.setdefault((route, oid), {}).setdefault(m.qid, set()).add(art.evidence_outlet)
    for (route, oid), qids in given.items():
        f = out[oid]
        if route == "enrichment":
            f.enrichment = len(qids)
            continue
        elsewhere: set[str] = set()
        for qid, counts in qids.items():
            row = queue.row(qid)
            if row is None or row.calibration:
                continue
            f.candidates += 1
            if oid not in counts:
                f.counted_elsewhere += 1
                elsewhere |= counts
            retired = row.status == "open" and row.data.get(0, {}).get("retired")  # folded into its whole
            if row.page and row.status in PUBLISHED and not retired:
                f.proposals += 1
                f.approved += row.status == "approved"
                f.created += row.status == "created"
        f.counted_as = tuple(sorted(elsewhere))
    return out


def lines(store, queue, outlets: list[dict]) -> list[str]:
    out = []
    for oid, f in figures(store, queue, outlets).items():
        if f.first_read:
            when = (f"first read {f.first_read} ({f.days} days ago), read on {f.days_read} "
                    f"day{'s' if f.days_read != 1 else ''}")
        else:
            when = "never read" + (" (no entries configured)" if f.configured and not f.entries else "")
        line = f"  {oid}: {when}"
        if f.removed:
            line += f", removed on {f.removed}"
        if not f.configured:
            line += ", not in the configuration"
        line += (f"; articles read {f.read}, kept {f.kept}; candidates {f.candidates}, proposals published {f.proposals} "
                 f"(approved {f.approved}, created {f.created}); enrichment entries {f.enrichment}")
        if f.counted_elsewhere:
            line += (f"; {f.counted_elsewhere} of the candidates counted as another outlet's evidence "
                     f"({', '.join(f.counted_as)})")
        if f.mark:
            line += f" -- {f.mark} (enrichment entries: {f.enrichment})"
        out.append(line)
    return out


def marked(store, queue, outlets: list[dict]) -> str:
    """The line that ends a run."""
    return "press review: " + (", ".join(
        f"{oid} ({f.mark}; enrichment entries: {f.enrichment})"
        for oid, f in figures(store, queue, outlets).items() if f.mark) or "none")
