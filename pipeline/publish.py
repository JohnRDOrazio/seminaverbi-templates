"""Stage 6: one `{{SV proposal}}` page per surviving candidate on the en wiki, and the ranking page."""
import re
import sys

from sync.wiki import SyncError

PREFIX = "Semina Verbi:Article proposals/"
RANKING_TITLE = "Semina Verbi:Article proposals"
SUMMARY = "Article proposal (pipeline)"
LANGS = ("en", "it", "es", "fr", "de", "pt")
KEPT_STATUSES = ("approved", "created", "declined")
_FORBIDDEN = re.compile(r"[#<>\[\]|{}]")
_CALL = re.compile(r"\{\{\s*(?:Template\s*:\s*)?SV[ _]+proposal\s*(?=[|}])", re.IGNORECASE)
_RANK = {"open": 0, "approved": 1, "created": 2}


def display_name(row) -> str:
    """A readable name for `row`: its first label in LANGS order, else a Wikipedia sitelink, else the QID."""
    label = next((row.labels[lang] for lang in LANGS if row.labels.get(lang)), None)
    return label or next((row.sitelinks[f"{lang}wiki"] for lang in LANGS if row.sitelinks.get(f"{lang}wiki")), row.qid)
_MASK = re.compile(r"<!--.*?-->|<nowiki>.*?</nowiki>", re.DOTALL | re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)


def page_title(row, queue=None) -> str:
    """The proposal page of `row`: stored on first use (and kept in `row.page`), reused afterwards."""
    if row.page:
        return row.page
    first = (row.data.get(5) or {}).get("first_wiki")
    name = row.sitelinks.get("enwiki") or row.labels.get(first) or row.labels.get("en") or ""
    name = " ".join(_FORBIDDEN.sub("", name).split()).lstrip(":").strip() or row.qid
    row.page = PREFIX + name
    if queue is not None:
        owner = queue.page_owner(row.page)
        if owner and owner != row.qid:  # another candidate's title: keep both pages
            row.page = f"{row.page} ({row.qid})"
        queue.set_page(row.qid, row.page)
    return row.page


def _escape(value) -> str:
    """A value safe as a template parameter on a saved page; Module:SV/Proposal decodes the entities back."""
    value = " ".join(str(value).split()).replace("&", "&amp;")  # so an entity in the text stays as typed
    value = value.replace("{{", "&#123;&#123;").replace("}}", "&#125;&#125;")
    value = value.replace("<", "&lt;").replace("~", "&#126;")  # no unclosed comment/ref/nowiki, no signature
    return value.replace("|", "{{!}}")


def _prob(decision: dict) -> str:
    return f"{decision.get('probabilities', {}).get(decision.get('choice'), 0.0):.2f}"


def _params(row, result) -> list[tuple[str, str]]:
    p = [("qid", row.qid)]
    p += [(f"label_{lang}", row.labels[lang]) for lang in LANGS if row.labels.get(lang)]
    p += [("domain", row.domain),
          ("connection", result["connection"]["choice"]), ("connection_p", _prob(result["connection"])),
          ("field", result["field"]["choice"]), ("field_p", _prob(result["field"]))]
    for n, theme in enumerate(result["themes"], 1):
        ev = theme.get("evidence") or {}
        p += [(f"theme{n}", theme["key"]), (f"theme{n}_p", f"{theme['p']:.2f}"),
              (f"theme{n}_status", theme["status"]), (f"theme{n}_evidence", ev.get("excerpt") or ""),
              (f"theme{n}_source", ev.get("url") or "")]
        if ev.get("label"):  # a press quotation: "<outlet> (<date>): <title>"
            p.append((f"theme{n}_source_label", ev["label"]))
    urls, labels = [], {}
    dossier = row.data.get(4) or {}
    for url, label in ([(n["url"], n.get("label")) for n in dossier.get("notes", [])]
                       + [(link, None) for link in dossier.get("links", [])]):
        url = url.replace(" ", "%20")
        if url not in urls:
            urls.append(url)
        if label and url not in labels:
            labels[url] = label
    status = row.status if row.status in KEPT_STATUSES else "open"
    p += [("outline", " ; ".join(result["outline"])), ("sources", " ".join(urls))]
    p += [(f"source{i}_label", labels[url]) for i, url in enumerate(urls, 1) if url in labels]
    p += [("first_wiki", result["first_wiki"]), ("status", status)]
    return p


def _render(params: list[tuple[str, str]], escape=True) -> str:
    return "{{SV proposal\n" + "".join(f"|{k}={_escape(v) if escape else v}\n" for k, v in params) + "}}"


def proposal_call(row, result) -> str:
    return _render(_params(row, result))


def _find_call(text: str) -> tuple[int, int] | None:
    """Span of the first top-level `{{SV proposal …}}` (comments and nowiki ignored), by balanced braces."""
    masked = _MASK.sub(lambda m: " " * len(m.group()), text)
    depth, i, start = 0, 0, None
    while i < len(masked):
        two = masked[i:i + 2]
        if two == "{{":
            if depth == 0 and _CALL.match(masked, i):
                start = i
            depth += 1
            i += 2
        elif two == "}}" and depth:
            depth -= 1
            i += 2
            if depth == 0 and start is not None:
                return start, i
        else:
            i += 1
    return None


def _split(call: str) -> list[tuple[str, str]]:
    """Named parameters of a call, raw values (stripped), split on top-level pipes (none inside a comment)."""
    body = call[2:-2]
    masked = _MASK.sub(lambda m: " " * len(m.group()), body)
    parts, depth, start, i = [], 0, 0, 0
    while i < len(body):
        two = masked[i:i + 2]
        if two in ("{{", "[["):
            depth += 1; i += 2
        elif two in ("}}", "]]"):
            depth = max(depth - 1, 0); i += 2
        elif masked[i] == "|" and depth == 0:
            parts.append(body[start:i]); start = i + 1; i += 1
        else:
            i += 1
    parts.append(body[start:])
    out = []
    for part in parts[1:]:
        name, eq, value = part.partition("=")
        if eq:
            out.append((name.strip(), value.strip()))
    return out


def _fields(call: str) -> dict[str, str]:
    """The parameters of a call as MediaWiki passes them: comments removed, stripped (what decisions read)."""
    return {k: _COMMENT.sub("", v).strip() for k, v in _split(call)}


def _carry(old_call: str, new_call: str) -> str:
    old, new = dict(_split(old_call)), _split(new_call)
    status = _fields(old_call).get("status", "").lower()
    mine = dict(new).get("status", "open")
    if status not in KEPT_STATUSES or (status != "declined" and _RANK.get(mine, 0) > _RANK[status]):
        return new_call  # the queue is further along than the page (declined on the page always wins)
    carried = ["status"] + {"declined": ["reason"], "created": ["article"]}.get(status, [])
    params = [(k, old[k] if k in carried and k in old else v) for k, v in new]
    names = {k for k, _ in params}
    params += [(k, old[k]) for k in carried if k in old and k not in names]
    return _render(params, escape=False)


def replace_call(old_text: str | None, call: str) -> str:
    if old_text is None:
        return call + "\n"
    span = _find_call(old_text)
    if span is None:
        raise ValueError("the page has no {{SV proposal}} call")
    return old_text[:span[0]] + _carry(old_text[span[0]:span[1]], call) + old_text[span[1]:]


def _display(title: str) -> str:
    return title.removeprefix(PREFIX)


def ranking(queue) -> str:
    rows = [r for r in queue.by_status_in(KEPT_STATUSES + ("open",)) if r.page and not r.calibration]
    lines = ["Article proposals of the SeminaVerbi pipeline, by votes. Vote on the proposal page.", ""]

    def listing(items, votes=False):
        if not items:
            return ["''None.''", ""]
        return [f"* [[{r.page}|{_display(r.page)}]]" + (f" ({r.votes or 0})" if votes else "") for r in items] + [""]

    opened = [r for r in rows if r.status == "open"]
    domains = sorted({r.domain for r in opened})
    # one line of links to the sections, with their counts, so a reader can jump to the domain they follow
    links = [f"[[#{d.capitalize()}|{d.capitalize()}]] ({sum(r.domain == d for r in opened)})" for d in domains]
    links += [f"[[#{s.capitalize()}|{s.capitalize()}]] ({sum(r.status == s for r in rows)})" for s in KEPT_STATUSES]
    links.append("[[#Translation suggestions|Translation suggestions]]")
    lines += ["Go to: " + " · ".join(links), ""]
    lines.append("== Open ==")
    for domain in domains:
        group = sorted((r for r in opened if r.domain == domain), key=lambda r: (-(r.votes or 0), r.page))
        lines += [f"=== {domain.capitalize()} ==="] + listing(group, votes=True)
    if not opened:
        lines += listing([])
    for status in KEPT_STATUSES:
        lines += [f"== {status.capitalize()} =="] + listing(sorted((r for r in rows if r.status == status),
                                                                key=lambda r: r.page))
    lines.append("== Translation suggestions ==")
    # existing articles (calibration rows) missing on some wikis are translation work too
    suggestions = queue.with_outcome(1, "translation") + [
        r for r in queue.with_outcome(1, "pass") if r.calibration and r.data.get(1, {}).get("missing")]
    if not suggestions:
        lines.append("''None.''")
    for r in suggestions:
        info = r.data.get(1, {})
        name = display_name(r)
        lines.append(f"* [https://www.wikidata.org/wiki/{r.qid} {name}] – on: {', '.join(info.get('have', []))};"
                     f" missing: {', '.join(info.get('missing', []))}" + (f" ({info['note']})" if info.get("note") else ""))
    return "\n".join(lines) + "\n"


def _write(en, title, build) -> bool:
    """Save `build(old_text)` to `title` unless it equals the page; True when written. A conflict is reported."""
    current = en.revision(title)
    old = current[1] if current else None
    text = build(old)
    if old is not None and text.rstrip() == old.rstrip():
        return False
    try:
        if current:
            en.edit(title, text, SUMMARY, baserevid=current[0])
        else:
            en.edit(title, text, SUMMARY, createonly=True)
    except SyncError as e:
        print(f"publish: {title} not written: {e}", file=sys.stderr)
        return False
    return True


def _page_call(current) -> dict | None:
    """The parameters of the proposal call in a `(revid, text)` revision, or None."""
    span = _find_call(current[1]) if current else None
    return _fields(current[1][span[0]:span[1]]) if span else None


def _read_back(queue, row, title, params: dict) -> bool:
    """Copy a hand-set or advanced status (declined, approved, created) from the page's call into the queue."""
    status = params.get("status", "").lower()
    if status not in KEPT_STATUSES:
        return False
    fields = {"reason": params.get("reason", "")} if status == "declined" else {}
    if row.status != status:
        queue.set_status(row.qid, status, page=title, **fields)
    return True


def _publisher(qid: str, call: str):
    """A `_write` builder: the page with its call replaced, only if the page as it is now has this QID's call
    (or does not exist yet); a missing or foreign call leaves the text unchanged, so nothing is written."""
    def build(old):
        if old is None:
            return replace_call(None, call)
        span = _find_call(old)
        if span is None or _fields(old[span[0]:span[1]]).get("qid") != qid:
            return old
        return replace_call(old, call)
    return build


def _publish_row(queue, en, row) -> int:
    """Write one proposal page; 1 when it was written. A row becomes open only once its call is on the page."""
    title = page_title(row, queue)
    call = proposal_call(row, row.data[5])
    existing = en.revision(title)
    if existing:
        params = _page_call(existing)
        if params is None:
            print(f"publish: {title} has no {{{{SV proposal}}}} call, left alone", file=sys.stderr)
            return 0
        if params.get("qid") != row.qid:
            print(f"publish: {title} belongs to another QID, left alone", file=sys.stderr)
            return 0
    written = int(_write(en, title, _publisher(row.qid, call)))
    params = _page_call(en.revision(title))
    if params is None or params.get("qid") != row.qid:
        return written  # not created (a failed edit, say): the row stays unopened and is tried next run
    if not _read_back(queue, row, title, params) and row.status is None:
        queue.set_status(row.qid, "open", page=title)
    return written


def publish(queue, en, cfg, domain: str | None = None) -> int:
    """Write the proposal page of every passing, non-calibration, non-declined row (of `domain`, when given),
    then the ranking page. A wiki error on one page skips it with a note; the others go on."""
    written = 0
    for row in queue.with_outcome(5, "pass"):
        if row.calibration or row.status == "declined" or (domain is not None and row.domain != domain):
            continue
        try:
            written += _publish_row(queue, en, row)
        except SyncError as e:
            print(f"publish: {row.page or row.qid} skipped: {e}", file=sys.stderr)
    if _write(en, RANKING_TITLE, lambda old: ranking(queue)):
        written += 1
    return written


_VOTES = re.compile(r"SVV(\d+):\s*([\d,.]+)\s*:SVV")
BATCH = 50


def count_votes(en, titles: list[str]) -> dict[str, int]:
    """Vote counts of `titles`: one action=parse per 50 pages, each line `{{NUMBEROFVOTESPAGE:<title>}}`."""
    counts = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start:start + BATCH]
        text = "\n".join(f"SVV{start + i}:{{{{NUMBEROFVOTESPAGE:{t}}}}}:SVV" for i, t in enumerate(chunk))
        html = en.parse(RANKING_TITLE, text)["text"]
        found = {int(m.group(1)): int(re.sub(r"\D", "", m.group(2)) or 0) for m in _VOTES.finditer(html)}
        for i, title in enumerate(chunk):
            if start + i not in found:
                print(f"votes: no count for {title}, taken as 0", file=sys.stderr)
            counts[title] = found.get(start + i, 0)
    return counts


def _set_status_param(call: str, value: str) -> str:
    """`call` with its effective (last) `status` parameter replaced; everything else stays byte-identical.
    Parameter boundaries are found on the text with comments and nowiki masked, as `_split` does."""
    masked = _MASK.sub(lambda m: " " * len(m.group()), call)
    body_start, end = 2, len(call) - 2
    depth, i, start, last = 0, body_start, body_start, None
    while i <= end:
        two = masked[i:i + 2]
        if i < end and two in ("{{", "[["):
            depth += 1; i += 2
        elif i < end and two in ("}}", "]]"):
            depth = max(depth - 1, 0); i += 2
        elif i == end or (masked[i] == "|" and depth == 0):
            name, eq, _ = masked[start:i].partition("=")
            if eq and name.strip().lower() == "status" and start > body_start:
                last = (start, i)
            start = i + 1
            i += 1
        else:
            i += 1
    if last is None:
        return call[:end].rstrip("\n") + f"\n|status={value}\n" + call[end:]
    start, stop = last
    part = call[start:stop]
    lead = part[:len(part) - len(part.lstrip())]
    trail = part[len(part.rstrip()):]
    return call[:start] + lead + f"status={value}" + trail + call[stop:]


def _approver(qid: str):
    """A `_write` builder: approve the page only if, as it is now, it is still this QID's and still open."""
    def build(old):
        span = _find_call(old) if old is not None else None
        if span is None:
            return old
        params = _fields(old[span[0]:span[1]])
        # an unknown status is shown as open by the template, and so it is open here
        if params.get("qid") != qid or params.get("status", "").lower() in KEPT_STATUSES:
            return old  # changed since it was read (declined by hand, say): leave it alone
        return _approve(old, span)
    return build


def _approve(old: str, span) -> str:
    return old[:span[0]] + _set_status_param(old[span[0]:span[1]], "approved") + old[span[1]:]


def _reconcile_approved(queue, en, declined: list) -> None:
    """Read a hand-set decline or creation on an approved row's page back; never recount or reopen it."""
    for row in queue.by_status("approved"):
        if not row.page or row.calibration:
            continue
        try:
            params = _page_call(en.revision(row.page))
        except SyncError as e:
            print(f"votes: {row.page} not read, skipped: {e}", file=sys.stderr)
            continue
        if params is None or params.get("qid") != row.qid:
            print(f"votes: {row.page} has no {{{{SV proposal}}}} call of {row.qid}, left alone", file=sys.stderr)
            continue
        if params.get("status", "").lower() in ("declined", "created"):
            _read_back(queue, row, row.page, params)
            if params["status"].lower() == "declined":
                declined.append(row.qid)


def _saved_status(en, title, qid) -> str | None:
    """The effective status of `qid`'s call on the saved page, or None when the page has no call of it."""
    params = _page_call(en.revision(title))
    if params is None or params.get("qid") != qid:
        return None
    return params.get("status", "").lower()


def sync_votes(queue, en, cfg) -> dict:
    """Count votes on open proposals, promote those at the threshold, read declines back (on approved rows
    too), rewrite the ranking."""
    approved, declined, voting = [], [], []
    _reconcile_approved(queue, en, declined)
    for row in queue.by_status("open"):
        if not row.page or row.calibration:
            continue
        try:
            params = _page_call(en.revision(row.page))
        except SyncError as e:
            print(f"votes: {row.page} not read, skipped: {e}", file=sys.stderr)
            continue
        if params is None:
            print(f"votes: {row.page} has no {{{{SV proposal}}}} call, left alone", file=sys.stderr)
            continue
        if params.get("qid") != row.qid:
            print(f"votes: {row.page} belongs to another QID, left alone", file=sys.stderr)
            continue
        status = params.get("status", "").lower()
        if status in KEPT_STATUSES:
            _read_back(queue, row, row.page, params)
            if status == "declined":
                declined.append(row.qid)
        else:
            voting.append(row)
    counts = count_votes(en, [r.page for r in voting]) if voting else {}
    for row in voting:
        n = counts[row.page]
        queue.set_votes(row.qid, n)
        if n < cfg["vote_threshold"]:
            continue
        try:
            if not _write(en, row.page, _approver(row.qid)):
                continue
            # the edit may have been merged with a concurrent one: record what the saved page says
            saved = _saved_status(en, row.page, row.qid)
        except SyncError as e:
            # an edit already saved stays; the row stays open and the saved page is read again next run
            print(f"votes: {row.page} not read, approval not recorded: {e}", file=sys.stderr)
            continue
        if saved == "approved":
            queue.set_status(row.qid, "approved")
            approved.append(row.qid)
        else:
            print(f"votes: {row.page} not approved: the saved page has status {saved!r}", file=sys.stderr)
    _write(en, RANKING_TITLE, lambda old: ranking(queue))
    return {"approved": approved, "declined": declined}


NOTE_CHARS = 400  # of each dossier note in the approved report


def approved_report(queue) -> str:
    """One block per approved row: what the article writer needs to start, the dossier notes included."""
    blocks = []
    for row in sorted(queue.by_status("approved"), key=lambda r: r.qid):
        data = row.data.get(5) or {}
        urls = []
        for note in (row.data.get(4) or {}).get("notes", []):
            if note["url"] not in urls:
                urls.append(note["url"])
        lines = [f"{_display(row.page) if row.page else row.qid} ({row.qid})",
                 f"  first wiki: {data.get('first_wiki', '')}",
                 f"  connection: {data.get('connection', {}).get('choice', '')}",
                 f"  field: {data.get('field', {}).get('choice', '')}", "  themes:"]
        for theme in data.get("themes", []):
            ev = theme.get("evidence") or {}
            lines.append(f"    - {theme['key']} ({theme['p']:.2f}, {theme['status']}): "
                         f"{ev.get('excerpt') or ''} {ev.get('url') or ''}".rstrip())
        lines.append("  outline: " + " ; ".join(data.get("outline", [])))
        lines.append("  sources:" + "".join(f"\n    - {u}" for u in urls))
        lines.append("  notes:")
        for note in (row.data.get(4) or {}).get("notes", []):
            text = " ".join(note["text"][:NOTE_CHARS].split())
            lines.append(f"    - [{note['heading']}] ({note['lang']}) {note['url']}\n      {text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")
