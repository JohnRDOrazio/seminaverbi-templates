"""Press notes for the stage-4 dossier: the stored passages of each mention, never the article."""


MAX_PASSAGES = 3


def press_notes(store, qid: str, outlets: dict[str, str]) -> list[dict]:
    """One note per mention of the work that has stored passages, newest article first.

    The text is only the stored passages (at most 3 x 25 words); the article is not fetched again. `label`
    ("<outlet> (<date>): <title>") names the quotation on the proposal page, beside its URL.
    """
    notes = []
    for m in store.mentions(qid=qid):
        art = store.article(m.url)
        if art is None or not m.passages or not m.supports:
            continue
        name = outlets.get(art.outlet, art.outlet)
        head = f"{name} ({art.published}): {art.title}" if art.published else f"{name}: {art.title}"
        notes.append((art.published or "", {"lang": art.lang, "url": m.url, "heading": head, "label": head,
                                            "text": " … ".join(m.passages[:MAX_PASSAGES])}))
    notes.sort(key=lambda t: t[0], reverse=True)
    return [n for _, n in notes]
