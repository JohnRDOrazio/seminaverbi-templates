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
_MASK = re.compile(r"<!--.*?-->|<nowiki>.*?</nowiki>", re.DOTALL | re.IGNORECASE)


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
    value = " ".join(str(value).split())
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
    urls = []
    for note in (row.data.get(4) or {}).get("notes", []):
        url = note["url"].replace(" ", "%20")
        if url not in urls:
            urls.append(url)
    status = row.status if row.status in KEPT_STATUSES else "open"
    p += [("outline", " ; ".join(result["outline"])), ("sources", " ".join(urls)),
          ("first_wiki", result["first_wiki"]), ("status", status)]
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
    """Named parameters of a call, raw values (stripped), split on top-level pipes."""
    body = call[2:-2]
    parts, depth, cur, i = [], 0, "", 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1; cur += two; i += 2
        elif two in ("}}", "]]"):
            depth = max(depth - 1, 0); cur += two; i += 2
        elif body[i] == "|" and depth == 0:
            parts.append(cur); cur = ""; i += 1
        else:
            cur += body[i]; i += 1
    parts.append(cur)
    out = []
    for part in parts[1:]:
        name, eq, value = part.partition("=")
        if eq:
            out.append((name.strip(), value.strip()))
    return out


def _carry(old_call: str, new_call: str) -> str:
    old, new = dict(_split(old_call)), _split(new_call)
    status = old.get("status", "").strip().lower()
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
    lines.append("== Open ==")
    for domain in sorted({r.domain for r in opened}) or []:
        group = sorted((r for r in opened if r.domain == domain), key=lambda r: (-(r.votes or 0), r.page))
        lines += [f"=== {domain} ==="] + listing(group, votes=True)
    if not opened:
        lines += listing([])
    for status in KEPT_STATUSES:
        lines += [f"== {status.capitalize()} =="] + listing(sorted((r for r in rows if r.status == status),
                                                                key=lambda r: r.page))
    lines.append("== Translation suggestions ==")
    suggestions = queue.with_outcome(1, "translation")
    if not suggestions:
        lines.append("''None.''")
    for r in suggestions:
        info = r.data.get(1, {})
        name = r.labels.get("en") or next(iter(r.labels.values()), r.qid)
        lines.append(f"* [https://www.wikidata.org/wiki/{r.qid} {name}] – on: {', '.join(info.get('have', []))};"
                     f" missing: {', '.join(info.get('missing', []))}")
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
    return dict(_split(current[1][span[0]:span[1]])) if span else None


def _read_back(queue, row, title, current) -> bool:
    """Copy a hand-set or advanced status (declined, approved, created) from the page into the queue."""
    params = _page_call(current) or {}
    status = params.get("status", "").lower()
    if status not in KEPT_STATUSES:
        return False
    fields = {"reason": params.get("reason", "")} if status == "declined" else {}
    if row.status != status:
        queue.set_status(row.qid, status, page=title, **fields)
    return True


def publish(queue, en, cfg) -> int:
    """Write the proposal page of every passing, non-calibration, non-declined row, then the ranking page."""
    written = 0
    for row in queue.with_outcome(5, "pass"):
        if row.calibration or row.status == "declined":
            continue
        title = page_title(row, queue)
        call = proposal_call(row, row.data[5])
        existing = en.revision(title)
        if existing:
            span = _find_call(existing[1])
            if span is None:
                print(f"publish: {title} has no {{{{SV proposal}}}} call, left alone", file=sys.stderr)
                continue
            if dict(_split(existing[1][span[0]:span[1]])).get("qid") != row.qid:
                print(f"publish: {title} belongs to another QID, left alone", file=sys.stderr)
                continue
        if _write(en, title, lambda old: replace_call(old, call)):
            written += 1
        current = en.revision(title)
        if not _read_back(queue, row, title, current):
            if row.status is None:
                queue.set_status(row.qid, "open", page=title)
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
    """`call` with only its `status` parameter replaced; everything else stays byte-identical."""
    body_start, end = 2, len(call) - 2
    depth, i, start = 0, body_start, body_start
    while i <= end:
        two = call[i:i + 2]
        if i < end and two in ("{{", "[["):
            depth += 1; i += 2
        elif i < end and two in ("}}", "]]"):
            depth = max(depth - 1, 0); i += 2
        elif i == end or (call[i] == "|" and depth == 0):
            part = call[start:i]
            name, eq, _ = part.partition("=")
            if eq and name.strip().lower() == "status" and start > body_start:
                lead = part[:len(part) - len(part.lstrip())]
                trail = part[len(part.rstrip()):]
                return call[:start] + lead + f"status={value}" + trail + call[i:]
            start = i + 1
            i += 1
        else:
            i += 1
    return call[:end].rstrip("\n") + f"\n|status={value}\n" + call[end:]


def _approver(qid: str):
    """A `_write` builder: approve the page only if, as it is now, it is still this QID's and still open."""
    def build(old):
        span = _find_call(old) if old is not None else None
        if span is None:
            return old
        params = dict(_split(old[span[0]:span[1]]))
        if params.get("qid") != qid or params.get("status", "").lower() not in ("", "open"):
            return old  # changed since it was read (declined by hand, say): leave it alone
        return _approve(old, span)
    return build


def _approve(old: str, span) -> str:
    return old[:span[0]] + _set_status_param(old[span[0]:span[1]], "approved") + old[span[1]:]


def sync_votes(queue, en, cfg) -> dict:
    """Count votes on open proposals, promote those at the threshold, read declines back, rewrite the ranking."""
    approved, declined, voting = [], [], []
    for row in queue.by_status("open"):
        if not row.page or row.calibration:
            continue
        current = en.revision(row.page)
        params = _page_call(current)
        if params is None:
            print(f"votes: {row.page} has no {{{{SV proposal}}}} call, left alone", file=sys.stderr)
            continue
        if params.get("qid") != row.qid:
            print(f"votes: {row.page} belongs to another QID, left alone", file=sys.stderr)
            continue
        status = params.get("status", "").lower()
        if status in KEPT_STATUSES:
            _read_back(queue, row, row.page, current)
            if status == "declined":
                declined.append(row.qid)
        else:
            voting.append(row)
    counts = count_votes(en, [r.page for r in voting]) if voting else {}
    for row in voting:
        n = counts[row.page]
        queue.set_votes(row.qid, n)
        if n >= cfg["vote_threshold"] and _write(en, row.page, _approver(row.qid)):
            queue.set_status(row.qid, "approved")
            approved.append(row.qid)
    _write(en, RANKING_TITLE, lambda old: ranking(queue))
    return {"approved": approved, "declined": declined}


def approved_report(queue) -> str:
    """One block per approved row: what the article writer needs to start."""
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
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")
