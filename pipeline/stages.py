"""Pipeline stages: the shared stage loop and stage 1, "already covered?"."""
from pipeline import load_config
from sync.wiki import WikiClient

LANGS = ("en", "it", "es", "fr", "de", "pt")
WIKIPEDIA_SITES = tuple(f"{lang}wiki" for lang in LANGS)


def run_stage(queue, stage: int, fn, limit=None) -> dict[str, int]:
    """Run fn(row) -> (outcome, data) over the pending rows of `stage`, recording each outcome.

    An exception stops the loop, leaves that row without an outcome and is re-raised."""
    counts: dict[str, int] = {}
    for row in queue.pending(stage, limit=limit):
        outcome, data = fn(row)
        queue.set_outcome(row.qid, stage, outcome, data)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _local_item(row, repo: WikiClient) -> str | None:
    for site in WIKIPEDIA_SITES:
        title = row.sitelinks.get(site)
        if title:
            found = repo.items_for(site, [title])
            if found:
                return next(iter(found.values()))
    return None


def _has_exact_page(row, lang: str, wiki: WikiClient) -> bool:
    titles = {row.labels.get(lang), row.sitelinks.get(f"{lang}wiki")} - {None, ""}
    return bool(titles) and any(wiki.page_info(sorted(titles)).values())


def covered(row, repo: WikiClient, wikis: dict[str, WikiClient]) -> tuple[str, dict]:
    """drop if SeminaVerbi covers the subject on all six wikis, translation if on some, else pass.

    A wiki counts as covering it when the local item has a sitelink there or an exact-title page exists."""
    if row.calibration:
        return "pass", {}
    item = _local_item(row, repo)
    linked = set(repo.item_sitelinks(item)[1]) if item else set()
    have = [lang for lang in LANGS if lang in linked or _has_exact_page(row, lang, wikis[lang])]
    if not have:
        return "pass", {}
    if len(have) == len(LANGS):
        return "drop", {"have": have}
    return "translation", {"have": have, "missing": [lang for lang in LANGS if lang not in have]}


def quick(row, judge, wikipedia_for) -> tuple[str, dict]:
    """Stage 3: drop if Jev finds the work implausibly connected to the Gospel (below quick_drop).

    The synopsis is the Wikipedia lead in the first of en, it, es, fr, de, pt the row has a sitelink for.
    Calibration rows always pass, with the probability recorded; a row with no Wikipedia sitelink passes unjudged."""
    for lang in LANGS:
        title = row.sitelinks.get(f"{lang}wiki")
        if title:
            p = judge.plausible(row, wikipedia_for(lang).lead(title))
            break
    else:
        return "pass", {"note": "no wikipedia sitelink"}
    if row.calibration or p >= load_config()["quick_drop"]:
        return "pass", {"p": p}
    return "drop", {"p": p}
