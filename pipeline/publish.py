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
_CALL = re.compile(r"\{\{\s*SV[ _]proposal\s*(?=[|}])", re.IGNORECASE)
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
        queue.set_page(row.qid, row.page)
    return row.page


def _escape(value) -> str:
    value = " ".join(str(value).split())
    value = value.replace("{{", "&#123;&#123;").replace("}}", "&#125;&#125;")
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
    if status not in KEPT_STATUSES:
        return new_call
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
        return call + "\n" + old_text
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
    if text == old:
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


def publish(queue, en, cfg) -> int:
    """Write the proposal page of every passing, non-calibration, non-declined row, then the ranking page."""
    written = 0
    for row in queue.with_outcome(5, "pass"):
        if row.calibration or row.status == "declined":
            continue
        title = page_title(row, queue)
        call = proposal_call(row, row.data[5])
        if _write(en, title, lambda old: replace_call(old, call)):
            written += 1
        current = en.revision(title)
        found = _find_call(current[1]) if current else None
        status = dict(_split(current[1][found[0]:found[1]])).get("status", "").lower() if found else ""
        if status in KEPT_STATUSES:
            fields = {"reason": dict(_split(current[1][found[0]:found[1]])).get("reason", "")} \
                if status == "declined" else {}
            if row.status != status:
                queue.set_status(row.qid, status, page=title, **fields)
        elif row.status is None:
            queue.set_status(row.qid, "open", page=title)
    if _write(en, RANKING_TITLE, lambda old: ranking(queue)):
        written += 1
    return written
