"""The benchmark code the domains that hold one share: works held back as calibration rows until
`release-benchmark`, and the curated works listed by QID."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from . import common

PERSON = "Q5"


def listed(sparql, domain: str, qids: list[str]) -> list[dict]:
    """All of `iter_listed`."""
    return list(iter_listed(sparql, domain, qids))


def iter_listed(sparql, domain: str, qids: list[str]) -> Iterator[dict]:
    """The curated works by QID: one query per chunk, no class filter (a listed work need not be of a work
    class), the query limit kept; people are reported and left out. A chunk that fails comes after the rows of the
    earlier ones."""
    for i in range(0, len(qids), common.CHUNK):
        pattern = f"  VALUES ?item {{ {common.values(qids[i:i + common.CHUNK])} }}"
        for row in common.run_query(sparql, domain, f"listed works {i // common.CHUNK + 1}", common.query(pattern)):
            if PERSON in row.get("types", "").split():
                print(f"{domain} candidates: {row['item']} is a person, skipped")
            else:
                yield row


def released(queue, qid: str) -> bool:
    row = queue.row(qid)
    return bool(row and row.data.get(0, {}).get("released"))


def hold(queue, domain: str, benchmark: list[dict]) -> None:
    """A benchmark work some other source found is held back too (a benchmark calibration row, as `add`
    makes it), so it is never proposed before `release-benchmark`, whatever the order of the commands."""
    for work in benchmark:
        row = queue.row(work["qid"])
        if row and row.domain == domain and row.status is None and not row.data.get(0, {}).get("released") and not (
                row.calibration and row.data.get(0, {}).get("benchmark")):
            queue.add(row.qid, row.domain, row.sources[0], row.labels, row.sitelinks, True,
                      {"benchmark": True, "benchmark_kind": work["kind"]})
            if not row.skipped:  # the author's skip stays
                queue.redo(1, row.qid)


def settled(queue, domain: str, qid: str) -> bool:
    """True, reported, for a row that already has a status (open, approved, created, declined): calibration
    leaves its flag and its outcomes alone."""
    row = queue.row(qid)
    if row and row.status is not None:
        print(f"{domain} calibration: {qid} is already {row.status}, left alone")
        return True
    return False


def add(queue, sparql, domain: str, qids: list[str], benchmark: list[dict], work_classes: Iterable[str]) -> int:
    """Add the works SeminaVerbi already covers, flagged as calibration, and every benchmark work with
    stage-0 data {"benchmark": True, "benchmark_kind": kind} (held back until `release-benchmark`); returns the
    rows added. A QID that answers to no work class, or is a person, is reported and skipped; a benchmark work
    need not be of a work class. A row that already has a status, or belongs to another domain, is left
    alone (reported). A live benchmark row is held first, so that it starts again at stage 1."""
    hold(queue, domain, benchmark)
    kinds = {b["qid"]: b["kind"] for b in benchmark}
    kinds = {qid: kind for qid, kind in kinds.items()
             if not common.other_domain(queue, qid, domain) and not released(queue, qid)
             and not settled(queue, domain, qid)}
    found: set[str] = set()
    added = 0
    ordinary = [q for q in qids if q not in kinds and not common.other_domain(queue, q, domain)
                and not released(queue, q) and not settled(queue, domain, q)]
    for i in range(0, len(ordinary), common.CHUNK):
        values = f"  VALUES ?item {{ {common.values(ordinary[i:i + common.CHUNK])} }}\n"
        for cls in work_classes:
            rows = common.run_query(sparql, domain, f"calibration ({cls})",
                                    common.query(values + f"  ?item wdt:P31/wdt:P279* wd:{cls} ."),
                                    limit=common.LIMIT)
            for row in rows:
                if row["item"] in found or PERSON in row.get("types", "").split():
                    continue
                labels, sitelinks, data = common.row_data(row)
                found.add(row["item"])
                added += queue.add(row["item"], domain, "calibration", labels, sitelinks, True, data)
    for qid in ordinary:
        if qid not in found:
            print(f"{domain} calibration: {qid} skipped (a person, or not an {domain} work)")
    for row in (r for r in listed(sparql, domain, list(kinds)) if r["item"] in kinds):
        labels, sitelinks, data = common.row_data(row)
        data = {**data, "benchmark": True, "benchmark_kind": kinds[row["item"]]}
        added += queue.add(row["item"], domain, "calibration", labels, sitelinks, True, data)
    return added
