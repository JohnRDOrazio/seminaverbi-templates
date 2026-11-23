"""Press notes for the stage-4 dossier: the stored passages of each mention, never the article."""
from pipeline.press.credit import outlet_name

MAX_PASSAGES = 3


def _head(art, outlets: dict[str, str]) -> str:
    name, title = outlet_name(art, outlets), art.title or art.url
    return f"{name} ({art.published}): {title}" if art.published else f"{name}: {title}"


def press_notes(store, qid: str, outlets: dict[str, str]) -> list[dict]:
    """One note per mention of the work that has stored passages, newest article first.

    The text is only the stored passages (at most 3 x 25 words); the article is not fetched again. `label`
    ("<outlet> (<date>): <title>") names the quotation on the proposal page, beside its URL. Copies of
    one article (see duplicates) are one note, the others' links under `also`.
    """
    notes, seen = [], set()
    cite = {m.url: m for m in store.mentions(qid=qid) if m.passages and m.supports}
    for m in cite.values():  # neutral or only critical: no support for a proposal
        art = store.article(m.url)
        if art is None or m.url in seen:
            continue
        copies = store.copies(m.url)
        cited = next((c for c in copies if c.url in cite and c.url == art.same_as), None)
        if cited is not None:  # the newspaper's own article is the note
            copies = [art] + [c for c in copies if c is not cited]
            art, m = cited, cite[cited.url]
        seen |= {art.url, *(c.url for c in copies)}
        head = _head(art, outlets)
        note = {"lang": art.lang, "url": art.url, "heading": head, "label": head,
                "text": " … ".join(m.passages[:MAX_PASSAGES])}
        if copies:
            note["also"] = [{"url": c.url, "label": _head(c, outlets)} for c in copies]
        notes.append((art.published or "", note))
    notes.sort(key=lambda t: t[0], reverse=True)
    return [n for _, n in notes]
