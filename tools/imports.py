"""Shared by tools.connect_items and tools.switch_infobox: the server-side Wikidata import (EnrichItems
--import) and the author's per-page decisions given on the command line."""
from __future__ import annotations

import re

from sync.pages import LANGS

WIKIPEDIA = [f"{lang}wiki" for lang in LANGS]
IMPORTED = re.compile(r"^IMPORT (Q\d+) = (Q\d+) \((?:mapped|created)\)\s*$", re.M)
IMPORT_FAILED = re.compile(r"^IMPORT (Q\d+) FAILED (.*?)\s*$", re.M)


def parse_import_log(text: str) -> tuple[dict, dict]:
    """EnrichItems --import output -> ({wd: local}, {wd: failure reason})."""
    return ({m.group(1): m.group(2) for m in IMPORTED.finditer(text)},
            {m.group(1): m.group(2) for m in IMPORT_FAILED.finditer(text)})


def import_command(wds: list, tools_dir: str) -> str:
    return (f"php maintenance/run.php {tools_dir}/EnrichItems.php --wiki=en "
            + " ".join(f"--import {q}" for q in wds))


def enrich_command(items: list, tools_dir: str) -> str:
    """EnrichItems --item: the items get the Wikidata statements they lack (Wikidata is the first source)."""
    return (f"php maintenance/run.php {tools_dir}/EnrichItems.php --wiki=en "
            + " ".join(f"--item {q}" for q in items))


def local_copy(repo, entity: dict) -> str | None:
    """The local item mirroring a Wikidata item: the one carrying one of its Wikipedia sitelinks."""
    for site in WIKIPEDIA:
        title = entity.get("sitelinks", {}).get(site, {}).get("title")
        if title:
            found = repo.items_for(site, [title])
            if found.get(title):
                return found[title]
    return None


def page_key(key: str, pages, what: str = "page") -> tuple[str, str]:
    """A CLI title, bare or written "lang:TITLE", -> (lang, title) among `pages` ((lang, title) pairs).
    ValueError when it names none of them, or a bare title found on several wikis."""
    pages = set(pages)
    lang, sep, title = key.partition(":")
    if sep and lang in LANGS:
        if (lang, title) not in pages:
            raise ValueError(f"{key!r} is not {what}")
        return lang, title
    langs = [lang for lang, t in sorted(pages) if t == key]
    if not langs:
        raise ValueError(f"{key!r} is not {what}")
    if len(langs) > 1:
        raise ValueError(f"{key!r} is {what} on both {' and '.join(langs)}; write it as LANG:TITLE")
    return langs[0], key
