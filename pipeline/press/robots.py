"""robots.txt as RFC 9309 reads it (urllib.robotparser knows no wildcards and takes the first matching rule).

The rules are those of our group, all groups with its name combined, else of `*`; none: everything is allowed. A rule
matches the path and query from their start, `*` standing for any run of characters and a final `$` for the end. The
longest matching rule decides, Allow on a tie. Escapes are compared as RFC 9309 2.2.2 says: those of unreserved
characters decoded, other characters than ASCII ones encoded, reserved ones kept apart (`%2F` is not `/`), except in a
query, where the RFC's own example takes `https://` for `https%3A%2F%2F`. A line that cannot be read is ignored.
Crawl-delay, not in the RFC, is read from the same group.
"""
from __future__ import annotations

import math
import re
import string

MAX_BYTES = 500 * 1024  # what the RFC asks to read at least
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_LITERAL = _UNRESERVED | frozenset(":/?#[]@!&'()+,;=%")  # kept as they are; `*` and `$` only mean something in a rule
_IN_QUERY = {"3A": ":", "2F": "/", "3F": "?", "40": "@"}
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")


def _decoded(m: re.Match, query: bool) -> str:
    code = m.group(1).upper()
    char = chr(int(code, 16))
    return char if char in _UNRESERVED else _IN_QUERY.get(code, "%" + code) if query else "%" + code


def _normal(text: str, special: str = "") -> str:
    """A path, or a piece of a rule, as compared; `special`: characters left alone."""
    out = "".join(c if c in _LITERAL or c in special else "".join(f"%{b:02X}" for b in c.encode("utf-8", "replace"))
                  for c in text)
    path, mark, query = out.partition("?")
    return _ESCAPE.sub(lambda m: _decoded(m, False), path) + mark + _ESCAPE.sub(lambda m: _decoded(m, True), query)


class _Rule:
    def __init__(self, pattern: str, allow: bool):
        self.allow, self.end = allow, pattern.endswith("$")
        body = _normal(pattern[:-1] if self.end else pattern, "*")
        self.length, self.pieces = len(body) + self.end, body.split("*")

    def matches(self, path: str) -> bool:
        first, *rest = self.pieces
        if not path.startswith(first):
            return False
        at = len(first)
        if not rest:
            return at == len(path) if self.end else True
        last = rest.pop() if self.end else ""
        for piece in rest:
            i = path.find(piece, at)
            if i < 0:
                return False
            at = i + len(piece)
        return path.endswith(last) and len(path) - len(last) >= at


def _lines(text: str):
    """(key, value) of the lines that have one, comments removed."""
    raw = text.encode("utf-8", "replace")
    if len(raw) > MAX_BYTES:  # the line the cap cuts is dropped: half a rule is another rule
        raw = raw[:MAX_BYTES]
        raw = raw[:max(raw.rfind(b"\n"), raw.rfind(b"\r")) + 1]
    for line in re.split(r"\r\n|\r|\n", raw.decode("utf-8", "replace").removeprefix("﻿")):
        key, colon, value = line.split("#", 1)[0].partition(":")
        if colon:
            yield key.strip().lower(), value.strip()


def _names(agent: str) -> list[str]:
    """The names our groups may have, the most specific first: our product token, then its parts before a hyphen."""
    parts = re.match(r"[A-Za-z_-]*", agent).group().lower().split("-")
    return ["-".join(parts[:n]) for n in range(len(parts), 0, -1)] + ["*"]


class Robots:
    """The rules of a robots.txt for the product token that begins `agent`."""

    def __init__(self, text: str, agent: str):
        groups: dict[str, tuple[list[_Rule], list[float]]] = {}
        names: list[str] = []  # whom the next rule is for
        waiting: list[str] = []  # whom the next Crawl-delay is for
        ruled = delayed = False  # the next name begins another group, or only other delays: a Crawl-delay, which the
        # RFC does not know, must not end a group of rules
        for key, value in _lines(text):
            if key == "user-agent":
                if ruled:
                    names, ruled, delayed = [], False, True
                if delayed:
                    waiting, delayed = [], False
                names.append(re.split(r"[\s/]", value, maxsplit=1)[0].lower())
                waiting.append(names[-1])
                groups.setdefault(names[-1], ([], []))
            elif key in ("allow", "disallow"):
                ruled = True
                if value.startswith(("/", "*")):
                    rule = _Rule(value, key == "allow")
                    for name in names:
                        groups[name][0].append(rule)
            elif key == "crawl-delay":
                delayed = True
                try:
                    delay = float(value)
                except ValueError:
                    continue
                for name in waiting:
                    groups[name][1].append(delay if math.isfinite(delay) and delay > 0 else 0)
        self.rules, delays = next((groups[n] for n in _names(agent) if n in groups), ([], []))
        self.crawl_delay = max(delays, default=0)

    def allowed(self, path: str) -> bool:
        """Whether the path (with its query) may be asked for."""
        path = _normal(path or "/")
        if path == "/robots.txt":
            return True
        found = max(((r.length, r.allow) for r in self.rules if r.matches(path)), default=(0, True))
        return found[1]
