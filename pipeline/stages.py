"""Pipeline stages: the shared stage loop, stage 1 ("already covered?"), stage 3 (quick judgment) and stage 5 (adjudication)."""
import re

from pipeline import load_config
from sync import categories
from sync.wiki import WikiClient

LANGS = ("en", "it", "es", "fr", "de", "pt")
WIKIPEDIA_SITES = tuple(f"{lang}wiki" for lang in LANGS)


def run_stage(queue, stage: int, fn, limit=None, only=None, domain: str | None = None) -> dict[str, int]:
    """Run fn(row) -> (outcome, data) over the pending rows of `stage` (of `domain`, when given), recording
    each outcome.

    `only(row) -> bool` leaves the other rows pending (applied before `limit`).

    An exception stops the loop, leaves that row without an outcome and is re-raised."""
    counts: dict[str, int] = {}
    rows = queue.pending(stage, domain=domain) if only else queue.pending(stage, domain=domain, limit=limit)
    if only:
        rows = [r for r in rows if only(r)][:limit]
    for row in rows:
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


def _title_matches(row, lang: str, wiki: WikiClient, item: str | None) -> tuple[bool, list[str]]:
    """(covered, possible duplicates) from the pages titled like the row's label or Wikipedia title on `lang`.

    Redirects are ignored. A page covers the row only when it is linked to the row's local item; any other
    page of that title is a possible duplicate ('lang:Title'), for the author to look at."""
    titles = sorted({row.labels.get(lang), row.sitelinks.get(f"{lang}wiki")} - {None, ""})
    if not titles:
        return False, []
    duplicates = []
    for title, info in wiki.page_info(titles).items():
        if info is None or info.redirect:
            continue
        if item and info.item == item:
            return True, []
        duplicates.append(f"{lang}:{title}")
    return False, duplicates


def covered(row, repo: WikiClient, wikis: dict[str, WikiClient]) -> tuple[str, dict]:
    """drop if SeminaVerbi covers the subject on all six wikis, translation if on some, else pass.

    A wiki covers it when the local item has a sitelink there or a page of the exact title is linked to that
    item; a page of that title on another subject only adds a `possible_duplicate` note. A calibration row
    always passes, with its coverage ({have, missing}) recorded."""
    item = _local_item(row, repo)
    linked = set(repo.item_sitelinks(item)[1]) if item else set()
    have, duplicates = [], []
    for lang in LANGS:
        if lang in linked:
            have.append(lang)
            continue
        hit, maybe = _title_matches(row, lang, wikis[lang], item)
        if hit:
            have.append(lang)
        duplicates += maybe
    missing = [lang for lang in LANGS if lang not in have]
    note = {"possible_duplicate": duplicates} if duplicates else {}
    if row.calibration:
        return "pass", {"have": have, "missing": missing, **note}
    if not have:
        return "pass", note
    if len(have) == len(LANGS):
        return "drop", {"have": have, **note}
    return "translation", {"have": have, "missing": missing, **note}


def quick(row, judge, wikipedia_for) -> tuple[str, dict]:
    """Stage 3: drop if Jev finds the work implausibly connected to the Gospel (below quick_drop).

    The synopsis is the Wikipedia lead in the first of en, it, es, fr, de, pt the row has a sitelink for.
    Calibration rows always pass, with the probability recorded; a row with no Wikipedia sitelink, or whose
    lead is empty, is dropped without asking Jev (calibration rows pass)."""
    for lang in LANGS:
        title = row.sitelinks.get(f"{lang}wiki")
        if title:
            lead = wikipedia_for(lang).lead(title)
            if not lead.strip():
                return ("pass" if row.calibration else "drop"), {"reason": "no lead"}
            p = judge.plausible(row, lead)
            break
    else:
        return ("pass", {}) if row.calibration else ("drop", {"reason": "no wikipedia sitelink"})
    if row.calibration or p >= load_config()["quick_drop"]:
        return "pass", {"p": p}
    return "drop", {"p": p}


THEME_KEEP = 0.50  # a theme is proposed at this probability or above
MAX_THEMES = 8  # the most themes one proposal lists, best first
DIRECT_PASS = 0.80  # a direct Christian or biblical influence this probable is proposed even with no kept theme
EXCERPT_WORDS = 25
MAX_DOSSIER_CHARS = 20_000  # the most dossier text one Jev question is sent
MAX_PARTS = 5  # a longer dossier is read in parts of MAX_DOSSIER_CHARS for the themes, at most this many


def _note_text(n: dict) -> str:
    return f"[{n['heading']}] ({n['lang']})\n{n['text']}"


def _dossier_text(notes: list[dict]) -> str:
    return "\n\n".join(_note_text(n) for n in notes)[:MAX_DOSSIER_CHARS]


def _pieces(n: dict) -> list[dict]:
    """The note, or, when it does not fit in one part, copies of it holding consecutive paragraphs that do
    (a single paragraph longer than a part is cut)."""
    if len(_note_text(n)) <= MAX_DOSSIER_CHARS:
        return [n]
    room = MAX_DOSSIER_CHARS - len(_note_text({**n, "text": ""}))
    out, current = [], ""
    for p in (p.strip() for p in re.split(r"\n\s*\n", n["text"])):
        if not p:
            continue
        p = p[:room]
        if current and len(current) + 2 + len(p) > room:
            out.append({**n, "text": current})
            current = ""
        current = f"{current}\n\n{p}" if current else p
    if current:
        out.append({**n, "text": current})
    return out


def _parts(notes: list[dict]) -> list[list[dict]]:
    """The notes packed in order into at most MAX_PARTS parts, each within MAX_DOSSIER_CHARS once joined."""
    parts, size = [[]], 0
    for piece in (p for n in notes for p in _pieces(n)):
        length = len(_note_text(piece))
        if parts[-1] and size + 2 + length > MAX_DOSSIER_CHARS:
            if len(parts) == MAX_PARTS:
                break
            parts.append([])
            size = 0
        size += length + (2 if len(parts[-1]) else 0)
        parts[-1].append(piece)
    return parts


def _paragraphs(notes: list[dict]) -> list[tuple[str, str]]:
    """(paragraph, url) for the non-empty blank-line-separated paragraphs of the notes, in order, while their
    total length stays within MAX_DOSSIER_CHARS (the one that crosses it is cut)."""
    out, left = [], MAX_DOSSIER_CHARS
    for n in notes:
        for p in re.split(r"\n\s*\n", n["text"]):
            p = p.strip()
            if not p:
                continue
            if left <= 0:
                return out
            out.append((p[:left], n["url"]))
            left -= len(out[-1][0])
    return out


def _excerpt(paragraph: str) -> str:
    words = paragraph.split()
    return " ".join(words[:EXCERPT_WORDS]) + ("…" if len(words) > EXCERPT_WORDS else "")


def _first_wiki(languages: dict[str, int]) -> str:
    """The language with most notes; ties broken en, it, es, fr, de, pt; "en" when there are none."""
    return max(LANGS, key=lambda lang: (languages.get(lang, 0), -LANGS.index(lang))) if languages else "en"


def direct(connection: dict) -> bool:
    """Jev finds a direct Christian or biblical influence at DIRECT_PASS or above."""
    return (connection.get("choice") == "direct"
            and connection.get("probabilities", {}).get("direct", 0) >= DIRECT_PASS)


def adjudicate(row, judge, data) -> tuple[str, dict]:
    """Stage 5: connection type, field, kept themes with evidence, outline and first wiki for the proposal.

    The themes are asked of each part of the dossier (see _parts) and each keeps its best probability, so a
    seed of the Word in one storyline or episode counts; connection and field are asked of the first part.
    A theme is kept when its status is applied or author and its probability is at least THEME_KEEP; the best
    MAX_THEMES are listed. A row with no kept theme is dropped unless Jev finds a direct Christian or biblical
    influence at DIRECT_PASS or above (calibration rows pass with the result kept); the connection type is
    shown on the proposal and never drops a row."""
    dossier = row.data.get(4, {"notes": [], "languages": {}})
    parts = _parts(dossier["notes"])
    text = _dossier_text(parts[0])
    connection = judge.connection(row, text)
    field = judge.field(row, text)
    names = {n.key: n.names["en"] for n in categories.leaf_themes(data)}
    best: dict[str, tuple[dict, int]] = {}
    for i, part in enumerate(parts):
        for key, v in judge.themes(row, _dossier_text(part)).items():
            if key not in best or v["p"] > best[key][0]["p"]:
                best[key] = (v, i)
    kept = sorted(((k, v, i) for k, (v, i) in best.items()
                   if v["status"] in ("applied", "author") and v["p"] >= THEME_KEEP and k in names),
                  key=lambda kvi: -kvi[1]["p"])[:MAX_THEMES]
    themes = []
    for key, v, i in kept:
        evidence = {"url": None, "excerpt": ""}
        paragraphs = _paragraphs(parts[i])
        if paragraphs:
            paragraph, url = paragraphs[judge.evidence(row, key, [p for p, _ in paragraphs])]
            evidence = {"url": url, "excerpt": _excerpt(paragraph)}
        themes.append({"key": key, "p": v["p"], "status": v["status"], "evidence": evidence})
    outline = (["Lead", "Story and the Gospel"] + [f"Theme: {names[t['key']]}" for t in themes]
               + ["Discernment", "See also", "References"])
    result = {"connection": connection, "field": field, "themes": themes, "outline": outline,
              "first_wiki": _first_wiki(dossier["languages"]),
              "theme_scores": {k: v["p"] for k, (v, _) in best.items()}}
    if not themes and not direct(connection) and not row.calibration:
        return "drop", result
    return "pass", result
