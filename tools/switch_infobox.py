"""python -m tools.switch_infobox [--wiki en,it] [--page TITLE]… [--accept-loss TITLE]… [--apply]
       [--import-log FILE]

Switches the connected articles from their Wikipedia infobox to a bare {{SV infobox}}, after moving onto
the article's item every fact the old infobox shows in an SV row that the item lacks (config/
infobox_import.json maps each template's parameters to SV rows; config/infobox.json gives each row's
property and kind). Values are converted only when unambiguous:

  item   each wikilink, through the linked page's SeminaVerbi sitelink, else its Wikipedia title on
         Wikidata (a disambiguation or list page is refused); a name without a link is never resolved
  time   {{Film date}}, {{Start date}} and the like, ISO dates, "17 November 1999", "17 novembre 1999",
         "November 17, 1999", "November 1999", a bare year
  image  a file a repository of the wiki holds: with its description page on Commons a statement, else
         (the farm's shared store, local to en/it.wikipedia) it stays on the call as image=; also when refused
  title  the original title; one equal to the item's label or the page title is the caption already

A row the item already has is not written; a row the shown layout lacks is still written (the fact is
the item's). For a one-value row the first parameter that converts wins; a differing further value is
reported, not written. A file no repository holds is dropped and reported. A page is switched only when every mapped row with a value is
covered (or the author accepts the loss with --accept-loss TITLE, "it:TITLE" when the title is on both
wikis); a page with two Wikipedia infoboxes is left to the author. it.wiki's {{Bio}} becomes {{SV bio}}
with its free text (note=, rest=, ref=; name= when Nome Cognome is not the item's label), after its facts
(places, dates, occupations, sex, citizenship) are moved to the item; the old and the new opening
sentences, both rendered, must state the same facts both ways (link texts, dates, predicate words; what
the new one adds where Bio had no parameter is reported as an addition, anything else blocks). A page with
{{Bio}} and no infobox gets {{SV infobox}} too. {{SV categories}} goes away (its sortkey= moves to
{{SV infobox}}). Pages already calling {{SV infobox}} for their own item are left alone; a section's
{{SV infobox|item=…}} renders another work and does not count.

The dry run prints per page the statements to write, the unresolved rows, the future infobox rendered
with action=parse (converted values passed as row parameters) and, for Bio pages, both sentences.
Values resolving to a Wikidata item with no local copy need importing: the dry run lists them; --apply
without --import-log prints the EnrichItems command and stops (exit 3); with it, the local IDs come from
its IMPORT lines. --apply writes the statements, then saves each page as tools.file_items does (base
revision; the preview keeps every tree category; the saved revision verified).
Credentials (with --apply): SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import calendar
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

import mwparserfromhell

from sync.categories import load, nodes
from sync.pages import LANGS
from sync.wiki import SyncError, WikiClient
from sync.wikidata import WikidataClient
from tools.connect_items import SUBKINDS, _template_name, claim_ids
from tools.file_items import guarded_save, layout_for
from tools.imports import enrich_command, import_command, local_copy, page_key, parse_import_log

ROOT = Path(__file__).resolve().parent.parent
TAG = "seminaverbi-mcp"
MAIN_PAGE_ITEM = "Q4"
GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
SV_INFOBOXES = {"SV infobox", "SV infobox work", "SV infobox person"}
SV_CATEGORIES = "SV categories"
LIST_HELPERS = {"Plainlist", "Plain list", "Flatlist", "Flat list", "Unbulleted list", "Ubl", "Hlist", "Nowrap"}
REF_TEMPLATES = {"Efn", "Refn", "Sfn", "Sfnp", "Cn", "Citation needed", "Nota", "Nota disambigua"}
# Y|M|D in the first three positional parameters; Film date may carry further dates from the fifth on
DATE_HELPERS = {"Film date", "Start date", "Start date and age", "End date", "Birth date", "Birth date and age",
                "Death date", "Death date and age", "Release date"}
# Wikidata classes a linked Wikipedia page must not be: disambiguation, list, category, template pages
NOT_ARTICLES = {"Q4167410", "Q22808320", "Q13406463", "Q4167836", "Q11266439"}
LINK_PREFIXES = {"file", "image", "immagine", "category", "categoria", "template", "w", "wikipedia", "wikt", "d",
                 "s", "q", "commons", "help", "aiuto", "portal", "portale", "special", "speciale"} | set(LANGS)
MONTHS = {
    "en": ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"],
}
CONNECTORS = re.compile(r"(?<!\w)(?:and|e|ed|y|et|und|&)(?!\w)", re.I)


class Value(NamedTuple):
    datavalues: list       # Wikibase datavalues; an item not yet local is {"type": "wikibase-entityid", "import": QID}
    shown: str             # what the author sees, and the preview's row parameter
    keep: str | None = None  # a file for image= on the call instead of a statement


class Unresolved(NamedTuple):
    reason: str


class Page(NamedTuple):
    lang: str
    title: str
    item: str
    revid: int
    text: str
    label: str | None  # the item's label in the page's language


class BioPlan(NamedTuple):
    old: str        # the {{Bio|…}} call
    call: str       # the {{SV bio|…}} call replacing it
    lacks: set      # what Bio could not state (birth_date, birth_place, death_date, death_place, occupation,
                    # nationality): the new sentence may add these


@dataclass(frozen=True)
class PagePlan:
    lang: str
    title: str
    item: str
    revid: int
    layout: str
    writes: list            # (prop, datavalue, shown)
    unresolved: list        # (row, raw)
    new_text: str | None
    bio: BioPlan | None
    blocked: str | None
    params: dict            # {{SV infobox}} parameters: type, image, sortkey
    preview: dict           # row -> shown, the converted values for the rendered preview
    dropped: list           # (row, raw) the author accepted to lose
    text: str
    covered: list = field(default_factory=list)  # props with a value on the old box that the item has already
    # files the old box names that no repository of the wiki holds: dropped
    missing_images: list = field(default_factory=list)
    # (row, raw): a further value for a one-value row, differing from the one written
    duplicates: list = field(default_factory=list)
    # (row, raw): a value of a one-value row that does not convert, another one did
    unconvertible: list = field(default_factory=list)

    @property
    def imports(self) -> list:
        return list(dict.fromkeys(dv["import"] for _, dv, _ in self.writes if "import" in dv))


def load_mapping(root: Path = ROOT) -> dict:
    def conf(name):
        return json.loads((root / "config" / name).read_text(encoding="utf-8"))
    imp = conf("infobox_import.json")
    return {"templates": imp["templates"], "bio": imp["bio"], "helpers": imp["helpers"],
            "layouts": conf("infobox.json"), "props": conf("wikibase.json"), "types": conf("infobox_types.json")}


def item_dv(qid: str) -> dict:
    return {"type": "wikibase-entityid", "value": {"entity-type": "item", "numeric-id": int(qid[1:]), "id": qid}}


def item_claims(entity: dict) -> dict:
    """prop -> datavalues of the item's non-deprecated statements ("no value"/"unknown value" as None: the
    item has the property all the same)."""
    out = {}
    for prop, statements in (entity or {}).get("claims", {}).items():
        values = [s.get("mainsnak", {}).get("datavalue") for s in statements if s.get("rank") != "deprecated"]
        if values:
            out[prop] = values
    return out


# --- values ---

def _remove(code, node) -> None:
    try:
        code.remove(node)
    except ValueError:  # inside a node removed already
        pass


def clean(raw: str) -> str:
    """`raw` without comments, references and footnote templates."""
    code = mwparserfromhell.parse(raw)
    for node in code.filter_comments(recursive=True):
        _remove(code, node)
    for node in code.filter_tags(recursive=True, matches=lambda t: str(t.tag).strip().lower() == "ref"):
        _remove(code, node)
    for node in code.filter_templates(recursive=True):
        if _template_name(str(node.name)) in REF_TEMPLATES:
            _remove(code, node)
    return str(code).strip()


class Resolver:
    """Turns wikilink targets, file names and nationality adjectives of one wiki into local items / files."""

    def __init__(self, lang: str, repo, wikidata, client):
        self.lang, self.repo, self.wikidata, self.client = lang, repo, wikidata, client
        self._items, self._images, self._countries = {}, {}, {}

    def item(self, title: str):
        """A local item datavalue, an import placeholder, or Unresolved."""
        if title not in self._items:
            self._items[title] = self._item(title)
        return self._items[title]

    def _item(self, title: str):
        local = self.repo.items_for(self.lang, [title]).get(title)
        if local:
            return item_dv(local)
        site = f"{self.lang}wiki"
        wd = self.wikidata.by_sitelink(site, [title]).get(title)
        if not wd:
            return Unresolved(f"[[{title}]]: no SeminaVerbi page with an item, no Wikidata item for {site}")
        return self._local_or_import(wd, f"{site}:{title}")

    def wikipedia(self, site: str, title: str):
        """The item of the Wikipedia page `site`:`title`: local datavalue, import placeholder or Unresolved."""
        key = (site, title)
        if key not in self._items:
            wd = self.wikidata.by_sitelink(site, [title]).get(title)
            self._items[key] = (self._local_or_import(wd, f"{site}:{title}") if wd
                                else Unresolved(f"no Wikidata item for {site}:{title}"))
        return self._items[key]

    def _local_or_import(self, wd: str, where: str):
        entity = self.wikidata.entity(wd)
        if set(claim_ids(entity, "P31")) & NOT_ARTICLES:
            return Unresolved(f"{where} is a disambiguation or list page ({wd})")
        local = local_copy(self.repo, entity)
        return item_dv(local) if local else {"type": "wikibase-entityid", "import": wd}

    def image(self, name: str) -> str | None:
        if name not in self._images:
            self._images[name] = self.client.image_source(name)
        return self._images[name]

    def country(self, adjective: str):
        """The country whose Wikidata demonym (P1549) in this language is `adjective`, if exactly one."""
        if adjective not in self._countries:
            hits = self.wikidata.search(adjective, self.lang)
            self.wikidata.prefetch(hits)
            want = adjective.strip().casefold()
            found = [q for q in hits if any(
                (s.get("mainsnak", {}).get("datavalue") or {}).get("value", {}).get("language") == self.lang
                and s["mainsnak"]["datavalue"]["value"].get("text", "").strip().casefold() == want
                for s in self.wikidata.entity(q).get("claims", {}).get("P1549", []))]
            if len(found) != 1:
                self._countries[adjective] = Unresolved(
                    f"nationality {adjective!r}: {len(found)} Wikidata items with that demonym")
            else:
                self._countries[adjective] = self._local_or_import(found[0], f"demonym {adjective!r}")
        return self._countries[adjective]


def _cap(title: str) -> str:
    title = re.sub(r"\s+", " ", title.replace("_", " ")).strip()
    return title[:1].upper() + title[1:]


def link_target(link) -> tuple[str | None, str] | None:
    """(site, title) a wikilink points to: site None for a page of this wiki, "enwiki"/"itwiki"… for a
    [[wikipedia:Title]] / [[w:it:Title]] link (the wikis' "wikipedia" interwiki is en.wikipedia; a language
    prefix after it goes on to that Wikipedia); None for anything else (files, categories, other projects)."""
    title = _cap(str(link.title).lstrip(" :").split("#")[0])
    if not title:
        return None
    prefix, sep, rest = title.partition(":")
    if not sep or prefix.strip().lower() not in LINK_PREFIXES:
        return None, title
    if prefix.strip().lower() not in ("wikipedia", "w"):
        return None
    lang, sep, inner = rest.partition(":")
    if sep and lang.strip().lower() in LANGS:
        return f"{lang.strip().lower()}wiki", _cap(inner)
    return ("enwiki", _cap(rest)) if rest.strip() else None


def _leftover(raw: str) -> str:
    """The text of `raw` outside its wikilinks, list templates, tags, punctuation and list connectors."""
    code = mwparserfromhell.parse(raw)
    for link in code.filter_wikilinks(recursive=True):
        try:
            code.replace(link, " ")
        except ValueError:  # inside a link replaced already
            pass
    s = str(code)
    names = "|".join(re.escape(n) for n in LIST_HELPERS)
    s = re.sub(r"\{\{\s*(?:%s)\s*\|" % names, " ", s, flags=re.I).replace("}}", " ")
    s = re.sub(r"<[^>]*>", " ", s)
    s = CONNECTORS.sub(" ", s)
    return re.sub(r"[\W_]+", " ", s).strip()


def _convert_items(raw: str, resolver) -> Value | Unresolved:
    code = mwparserfromhell.parse(raw)
    for template in code.filter_templates(recursive=True):
        name = _template_name(str(template.name))
        if name not in LIST_HELPERS:
            return Unresolved(f"{{{{{name}}}}} in the value")
    links = code.filter_wikilinks(recursive=True)
    if not links:
        return Unresolved("no link (a name alone is never resolved)")
    rest = _leftover(raw)
    if rest:
        return Unresolved(f"unlinked text: {rest}")
    dvs = []
    for link in links:
        target = link_target(link)
        if target is None:
            return Unresolved(f"{link} is not an article link")
        site, title = target
        dv = resolver.item(title) if site is None else resolver.wikipedia(site, title)
        if isinstance(dv, Unresolved):
            return dv
        if dv not in dvs:
            dvs.append(dv)
    return Value(dvs, ", ".join(str(link) for link in links))


def _time_value(y: int, m: int | None, d: int | None, shown: str) -> Value | Unresolved:
    if not 1 <= y <= 9999 or (m is not None and not 1 <= m <= 12):
        return Unresolved(f"not a date: {shown}")
    if d is not None and (m is None or not 1 <= d <= calendar.monthrange(y, m)[1]):
        return Unresolved(f"not a date: {shown}")
    precision = 11 if d is not None else 10 if m is not None else 9
    iso = f"{y:04d}-{m or 0:02d}-{d or 0:02d}"
    shown = iso if precision == 11 else iso[:7] if precision == 10 else str(y)
    return Value([{"type": "time", "value": {"time": f"+{iso}T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
                                             "precision": precision, "calendarmodel": GREGORIAN}}], shown)


def _month(name: str) -> int | None:
    name = name.casefold()
    for months in MONTHS.values():
        if name in months:
            return months.index(name) + 1
    return None


def _convert_time(raw: str) -> Value | Unresolved:
    code = mwparserfromhell.parse(raw)
    templates = code.filter_templates(recursive=False)
    if templates:
        template = templates[0]
        name = _template_name(str(template.name))
        if len(templates) > 1 or name not in DATE_HELPERS or str(code).replace(str(template), "").strip():
            return Unresolved(f"not an unambiguous date: {raw}")
        values = [str(p.value).strip() for p in template.params if not p.showkey]
        if name == "Film date" and any(v for v in values[4:]):
            return Unresolved(f"several dates: {raw}")
        ymd = values[:3]
        while ymd and not ymd[-1]:
            ymd.pop()
        if not ymd or not all(v.isdecimal() and v.isascii() for v in ymd):
            return Unresolved(f"not an unambiguous date: {raw}")
        numbers = [int(v) for v in ymd] + [None] * (3 - len(ymd))
        return _time_value(numbers[0], numbers[1], numbers[2], raw)
    text = re.sub(r"\s+", " ", code.strip_code()).strip().replace("º", "").replace("°", "")
    if m := re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", text):
        return _time_value(int(m[1]), int(m[2]), int(m[3]), text)
    if m := re.fullmatch(r"([0-9]{4})-([0-9]{2})", text):
        return _time_value(int(m[1]), int(m[2]), None, text)
    if (m := re.fullmatch(r"([0-9]{1,2}) (\w+) ([0-9]{3,4})", text)) and _month(m[2]):
        return _time_value(int(m[3]), _month(m[2]), int(m[1]), text)
    if (m := re.fullmatch(r"(\w+) ([0-9]{1,2}), ([0-9]{3,4})", text)) and _month(m[1]):
        return _time_value(int(m[3]), _month(m[1]), int(m[2]), text)
    if (m := re.fullmatch(r"(\w+) ([0-9]{3,4})", text)) and _month(m[1]):
        return _time_value(int(m[2]), _month(m[1]), None, text)
    if re.fullmatch(r"[0-9]{3,4}", text):
        return _time_value(int(text), None, None, text)
    return Unresolved(f"not an unambiguous date: {text or raw}")


def _file_name(raw: str) -> str | None:
    code = mwparserfromhell.parse(raw)
    links = code.filter_wikilinks(recursive=False)
    name = str(links[0].title) if links else code.strip_code()
    if links and len(links) > 1 or "{{" in name:
        return None
    name = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    prefix, sep, rest = name.partition(":")
    if sep and prefix.strip().lower() in ("file", "image", "immagine"):
        name = rest.strip()
    return (name[:1].upper() + name[1:]) if name else None


def _convert_image(raw: str, resolver) -> Value | Unresolved:
    name = _file_name(raw)
    if not name:
        return Unresolved(f"not a file name: {raw}")
    source = resolver.image(name)
    if source == "commons":
        return Value([{"type": "string", "value": name}], name)
    if source:
        return Value([], name, keep=name)
    return Unresolved(f"no file {name} in any repository of the wiki")


def title_text(raw: str) -> tuple[str, str | None]:
    """(text, language) of a title value: {{lang|xx|text}} names its language."""
    code = mwparserfromhell.parse(raw)
    templates = code.filter_templates(recursive=False)
    if len(templates) == 1 and _template_name(str(templates[0].name)) == "Lang" and len(templates[0].params) >= 2 \
            and not str(code).replace(str(templates[0]), "").strip():
        t = templates[0]
        return mwparserfromhell.parse(str(t.params[1].value)).strip_code().strip(), str(t.params[0].value).strip()
    if templates:
        return "", None
    return re.sub(r"\s+", " ", code.strip_code()).strip(), None


def convert(row_kind: str, raw_wikitext: str, lang: str | None, resolver) -> Value | Unresolved:
    """One old infobox value as the datavalue(s) of an SV row of kind `row_kind`, or why not. For a title
    (monolingual), `lang` None means its language is unknown."""
    raw = clean(raw_wikitext)
    if not raw:
        return Unresolved("empty")
    if row_kind == "item":
        return _convert_items(raw, resolver)
    if row_kind == "time":
        return _convert_time(raw)
    if row_kind == "image":
        return _convert_image(raw, resolver)
    if row_kind == "monolingual":
        text, language = title_text(raw)
        language = language or lang
        if not text or not language:
            return Unresolved(f"original title in an unknown language: {raw}")
        return Value([{"type": "monolingualtext", "value": {"text": text, "language": language}}], text)
    return Unresolved(f"unknown row kind {row_kind}")


# --- pages ---

def _named(template) -> dict:
    return {str(p.name).strip(): str(p.value) for p in template.params if p.showkey}


def _bare_title(title: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", title) or title


def _templates(text: str):
    return [(t, _template_name(str(t.name))) for t in mwparserfromhell.parse(text).filter_templates(recursive=True)]


def _own_sv_infobox(template, name: str, item: str) -> bool:
    """An {{SV infobox}} for the page's own item `item`: with no `item=`, or with `item=` naming that item
    (case-insensitive); one naming another item renders another work (a section's box)."""
    if name not in SV_INFOBOXES:
        return False
    named = str(template.get("item").value).strip() if template.has("item") else ""
    return not named or named.upper() == (item or "").strip().upper()


def has_sv_infobox(text: str, item: str) -> bool:
    """Whether `text`, the page of local item `item`, calls {{SV infobox}} for its own item."""
    return any(_own_sv_infobox(t, name, item) for t, name in _templates(text))


def _outermost(found: list) -> list:
    """Drop the templates nested inside another one of the list."""
    return [t for t in found if not any(o is not t and str(t) in str(o) and len(str(o)) > len(str(t)) for o in found)]


def call(name: str, params: dict) -> str:
    return "{{" + name + "".join(f"|{k}={v}" for k, v in params.items() if v) + "}}"


def swap(text: str, boxes: set, bio_name: str | None, infobox_call: str, bio_call: str | None) -> str:
    """`text` with its infobox replaced by `infobox_call` ({{Bio}} by `bio_call`, preceded by the infobox
    call when the page has no infobox) and its {{SV categories}} removed with their line."""
    code = mwparserfromhell.parse(text)
    found = code.filter_templates(recursive=True)
    names = {id(t): _template_name(str(t.name)) for t in found}
    box = next(iter(_outermost([t for t in found if names[id(t)] in boxes])), None)
    bio = next((t for t in found if bio_name and names[id(t)] == bio_name), None)
    marker = "\ue000SVCAT\ue000"
    for t in found:
        if names[id(t)] == SV_CATEGORIES:
            code.replace(t, marker)
    if box is not None:
        code.replace(box, infobox_call)
    if bio is not None:
        code.replace(bio, bio_call if box is not None else f"{infobox_call}\n{bio_call}")
    out = str(code)
    while marker in out:
        i = out.index(marker)
        start, end = i, i + len(marker)
        if out[end:end + 1] == "\n":
            end += 1
        elif start > 0 and out[start - 1] == "\n":
            start -= 1
        out = out[:start] + out[end:]
    return out


def _is_refs_only(value: str) -> bool:
    return "<ref" in value.lower() and not clean(value)


def plan_bio(bio, bio_conf: dict, label: str | None) -> tuple[list, list, BioPlan]:
    """A {{Bio}} call -> ((row, param, raw) facts converted like infobox rows, (key, param, raw) facts with
    no infobox row: sex and citizenship, the {{SV bio}} plan)."""
    extra = []
    args = {k: clean(v) for k, v in _named(bio).items()}
    free = {"note": [], "rest": [], "ref": []}
    for p, v in _named(bio).items():
        slot = bio_conf["free"].get(p)
        if slot and v.strip():
            free["ref" if _is_refs_only(v) else slot].append(v.strip())
    bio_raws = []
    for key, params in _bio_params(bio_conf["facts"]).items():
        if key in ("birth_date", "death_date"):
            day = next((args[p] for p in params if p.startswith("GiornoMese") and args.get(p)), "")
            year = next((args[p] for p in params if p.startswith("Anno") and args.get(p)), "")
            if year:
                date = f"{day} {year}".strip()
                bio_raws.append((key, "+".join(p for p in params if args.get(p)), date))
            elif day:
                bio_raws.append((key, "+".join(p for p in params if args.get(p)), day))
        elif key in ("birth_place", "death_place"):
            base = next((p for p in params if not p.endswith(("Link", "Alt"))), None)
            link = next((args[p] for p in params if p.endswith("Link") and args.get(p)), "") or args.get(base, "")
            shown = next((args[p] for p in params if p.endswith("Alt") and args.get(p)), "") or args.get(base, "")
            if link:
                bio_raws.append((key, base, f"[[{link}|{shown}]]" if shown and shown != link else f"[[{link}]]"))
        elif key == "occupation":
            for p in params:
                if args.get(p):
                    bio_raws.append((key, p, f"[[{args[p]}]]"))
        elif key == "image":
            p = next((p for p in params if args.get(p)), None)
            if p:
                bio_raws.append((key, p, args[p]))
        elif key == "sex_or_gender":
            p = next((p for p in params if args.get(p)), None)
            if p:
                extra.append((key, p, args[p]))
        elif key == "nationality":
            # citizenship when Bio names it apart from the nationality, plus a naturalisation
            chosen = [p for p in params if p == "Cittadinanza" and args.get(p)] or \
                     [p for p in params if p == "Nazionalità" and args.get(p)]
            chosen += [p for p in params if p not in ("Cittadinanza", "Nazionalità") and args.get(p)]
            for p in chosen:
                extra.append((key, p, args[p]))
    given = " ".join(args[p] for p in ("Nome", "Cognome") if args.get(p) and bio_conf["facts"].get(p) == "given_name")
    bio_params = {"name": given if given and given != label else ""}
    bio_params.update({slot: " ".join(values) for slot, values in free.items()})
    bio_params["note"] = re.sub(r"^\s*,\s*", "", bio_params["note"])
    stated = {key for key, _, _ in bio_raws} | {key for key, _, _ in extra}
    lacks = {k for k in ("birth_date", "birth_place", "death_date", "death_place", "occupation", "nationality")
             if k not in stated}
    bio_plan = BioPlan(str(bio), call("SV bio", bio_params), lacks)
    return bio_raws, extra, bio_plan


def plan_page(page: Page, item_claims: dict, mapping: dict, resolver=None, accept_loss: bool = False):
    """What switching `page` takes; None when it has no Wikipedia infobox or {{Bio}}, or calls {{SV infobox}}
    already. `item_claims`: prop -> datavalues the item has (item_claims())."""
    lang, props = page.lang, mapping["props"]
    templates = mapping["templates"].get(lang, {})
    bio_conf = mapping["bio"].get(lang)
    bio_name = bio_conf["template"] if bio_conf else None
    found = _templates(page.text)
    if any(_own_sv_infobox(t, name, page.item) for t, name in found):
        return None
    boxes = _outermost([t for t, name in found if name in templates])
    bios = [t for t, name in found if bio_name and name == bio_name]
    if not boxes and not bios:
        return None

    def plan(layout, **kw):
        base = dict(lang=lang, title=page.title, item=page.item, revid=page.revid, layout=layout, writes=[],
                    unresolved=[], new_text=None, bio=None, blocked=None, params={}, preview={}, dropped=[],
                    text=page.text, covered=[], missing_images=[], duplicates=[], unconvertible=[])
        base.update(kw)
        return PagePlan(**base)

    if len(boxes) > 1:
        names = ", ".join(_template_name(str(t.name)) for t in boxes)
        return plan("work", blocked=f"two Wikipedia infoboxes ({names}): for the author")
    if len(bios) > 1:
        return plan("person", blocked=f"{len(bios)} {{{{{bio_name}}}}} calls: for the author")
    # a mapped box inside another (enwiki |module={{Infobox musical artist|embed=yes|…}}): only the outer
    # box's own parameters are read, so the inner box's facts would vanish with the switch
    embedded = [name for t, name in found if name in templates and not any(t is b for b in boxes)]
    if embedded:
        return plan("work", blocked=f"embedded infobox ({', '.join(dict.fromkeys(embedded))}): for the author")
    box, bio = (boxes or [None])[0], (bios or [None])[0]

    # (row, param, raw) in the order the old templates give them
    raws = []
    if box is not None:
        name = _template_name(str(box.name))
        conf = templates[name]
        layout = conf["layout"]
        param, by_value = SUBKINDS.get((lang, name), (None, {}))
        # parameter names in any case: the mapping's keys and editors' spellings differ (Genere / genere)
        named = _named(box)
        by_case = {p.casefold(): row for p, row in conf["params"].items()}
        sub = next((v for p, v in named.items() if param and p.casefold() == param.casefold()), None)
        if sub is not None:
            layout = by_value.get(mwparserfromhell.parse(sub).strip_code().strip().lower(), layout)
        raws += [(by_case[p.casefold()], p, v) for p, v in named.items() if p.casefold() in by_case and v.strip()]
    else:
        layout = "person"
    instance_of = [dv["value"]["id"] for dv in item_claims.get(props["instance_of"], []) if dv]
    item_layout = next((mapping["types"]["types"][q] for q in instance_of if q in mapping["types"]["types"]), None)
    # a known item type is the layout (the old box may be wider: it.wiki's {{Artista musicale}} boxes bands
    # too); an unknown one leaves the old box's layout, given as type= unless it is the default
    box_layout, layout = layout, item_layout or layout
    rows = {r["key"]: r for r in mapping["layouts"][layout]}
    # a fact the old box shows belongs on the item even when the layout shown has no row for it
    box_rows = {r["key"]: r for r in mapping["layouts"][box_layout]}
    person_rows = {r["key"]: r for r in mapping["layouts"]["person"]}

    extra, bio_plan = [], None  # extra: (key, param, raw) Bio facts with no infobox row (sex, citizenship)
    if bio is not None:
        bio_raws, extra, bio_plan = plan_bio(bio, bio_conf, page.label)
        # the infobox's own value for a row comes first
        have = {key for key, _, _ in raws}
        raws += [r for r in bio_raws if r[0] not in have]

    writes, unresolved, preview, params, covered, missing, duplicates, unconvertible = [], [], {}, {}, [], [], [], []

    def add(prop, dvs, shown):
        for dv in dvs:
            if (prop, dv) not in [(p, d) for p, d, _ in writes]:
                writes.append((prop, dv, shown))

    by_row = {}
    for key, param, raw in raws:
        by_row.setdefault(key, []).append((param, raw))
    order = list(dict.fromkeys([*rows, *box_rows, *person_rows]))
    for key in sorted(by_row, key=lambda k: order.index(k) if k in order else len(order)):
        row = rows.get(key) or box_rows.get(key) or person_rows.get(key)
        if row is None or row["prop"] not in props:
            unresolved += [(key, clean(raw)) for _, raw in by_row[key][:1]]
            continue
        prop = props[row["prop"]]
        if prop in item_claims:
            covered.append(prop)
            continue
        single = row["kind"] != "item"
        converted, failed = [], []  # failed: raws of a one-value row that do not convert
        for param, raw in by_row[key]:
            if row["kind"] == "monolingual":
                text, _ = title_text(clean(raw))
                if text and text.casefold() in {(page.label or "").casefold(), _bare_title(page.title).casefold()}:
                    continue  # the caption: the item's label or the page title
                value = convert("monolingual", raw, None if "original" in param.lower() else lang, resolver)
            elif row["kind"] == "image" and (name := _file_name(clean(raw))) and resolver.image(name) is None:
                # a file in no repository the wiki sees shows as a broken link: no fact to keep
                missing.append(name)
                continue
            else:
                value = convert(row["kind"], raw, lang, resolver)
            if isinstance(value, Unresolved):
                if not single:
                    unresolved.append((key, clean(raw)))
                    converted = None
                    break
                if clean(raw) not in failed:
                    failed.append(clean(raw))
                continue
            if single and converted:
                # the first value that converts wins; another one is reported when it differs
                if value.datavalues != converted[0].datavalues or value.keep != converted[0].keep:
                    duplicates.append((key, clean(raw)))
                continue
            converted.append(value)
        if single and not converted:
            unresolved += [(key, raw) for raw in failed]
        elif single:
            # a value that does not convert is reported, not dropped silently
            unconvertible += [(key, raw) for raw in failed]
        if not converted:
            continue
        for value in converted:
            add(prop, value.datavalues, value.shown)
            if value.keep:
                params["image"] = value.keep
        if key in rows:
            preview[key] = ", ".join(v.shown for v in converted)
    for key, param, raw in extra:
        prop = props["sex_or_gender" if key == "sex_or_gender" else "country_of_citizenship"]
        if prop in item_claims:
            covered.append(prop)
            continue
        if key == "sex_or_gender":
            gender = {"M": props["male"], "F": props["female"]}.get(raw.strip().upper())
            value = Value([item_dv(gender)], raw.strip()) if gender else Unresolved(f"sex {raw!r}")
        else:
            value = resolver.country(raw)
            value = value if isinstance(value, Unresolved) else Value([value], raw)
        if isinstance(value, Unresolved):
            unresolved.append((key, raw))
        else:
            add(prop, value.datavalues, value.shown)

    if item_layout is None and layout != layout_for([], mapping["types"]):
        params = {"type": layout, **params}
    sortkey = next((str(t.get("sortkey").value).strip() for t, name in found
                    if name == SV_CATEGORIES and t.has("sortkey") and str(t.get("sortkey").value).strip()), None)
    if sortkey:
        params["sortkey"] = sortkey
    blocked, dropped = None, []
    if unresolved and accept_loss:
        dropped = unresolved
    elif unresolved:
        blocked = "unresolved: " + "; ".join(f"{key} = {raw}" for key, raw in unresolved)
    new_text = swap(page.text, set(templates), bio_name, call("SV infobox", params), bio_plan and bio_plan.call)
    return plan(layout, writes=writes, unresolved=unresolved, new_text=new_text, bio=bio_plan, blocked=blocked,
                params=params, preview=preview, dropped=dropped, covered=list(dict.fromkeys(covered)),
                missing_images=list(dict.fromkeys(missing)), duplicates=duplicates,
                unconvertible=unconvertible)


def _bio_params(facts: dict) -> dict:
    out = {}
    for param, key in facts.items():
        out.setdefault(key, []).append(param)
    return out


def preview_text(plan: PagePlan, mapping: dict) -> str:
    """The new text with the converted values passed to {{SV infobox}} as row parameters."""
    bio_name = (mapping["bio"].get(plan.lang) or {}).get("template")
    params = {**plan.params, **plan.preview}
    return swap(plan.text, set(mapping["templates"].get(plan.lang, {})), bio_name, call("SV infobox", params),
                plan.bio and plan.bio.call)


# --- rendering ---

def _text(fragment: str) -> str:
    fragment = re.sub(r"<(style|script)\b.*?</\1>|<sup\b[^>]*\breference\b.*?</sup>", "", fragment, flags=re.S)
    fragment = re.sub(r"<(?:br|/p|/li|/tr|/div)\b[^>]*>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", fragment))).strip()


def infobox_rows(rendered: str) -> list[str]:
    """The rendered {{SV infobox}} as "label: value" lines."""
    m = re.search(r'<table class="sv-infobox[^"]*">(.*?)</table>', rendered, re.S)
    if not m:
        return []
    body, out = m.group(1), []
    caption = re.search(r"<caption>(.*?)</caption>", body, re.S)
    if caption:
        out.append(f"caption: {_text(caption.group(1))}")
    for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        if "sv-infobox-image" in tr:
            f = re.search(r'href="[^"]*?File:([^"?&]+)"', tr)
            out.append(f"image: {unquote(f.group(1)).replace('_', ' ') if f else '?'}")
            continue
        th, td = re.search(r"<th>(.*?)</th>", tr, re.S), re.search(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if th and td:
            out.append(f"{_text(th.group(1))}: {_text(td.group(1))}")
        elif td:
            out.append(f"title: {_text(td.group(1))}")
    return out


def _paragraph(rendered: str) -> str:
    """The first paragraph's HTML, without references and styles."""
    m = re.search(r"<p>(.*?)</p>", rendered, re.S)
    p = m.group(1) if m else rendered
    return re.sub(r"<(style|script)\b.*?</\1>|<sup\b[^>]*\breference\b.*?</sup>", "", p, flags=re.S)


def sentence(rendered: str) -> str:
    """The first paragraph of rendered HTML as plain text."""
    return _text(_paragraph(rendered))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("º", "").replace("°", "")).strip().casefold()


# ASCII digits only: \d takes any script's digits, which int() would read
DATE = re.compile(r"[0-9]{1,2} (?:%s) [0-9]{3,4}|(?<!\w)[0-9]{3,4}(?!\w)"
                  % "|".join(m for ms in MONTHS.values() for m in ms), re.I)
# words of a predicate that state no fact: copulas, articles, conjunctions (all six languages)
PREDICATE_STOP = {"stato", "stata", "una", "uno", "del", "della", "dei", "degli", "delle", "per", "and", "the",
                  "was", "born", "died", "est", "était", "fut", "une", "des", "ist", "war", "ein", "eine", "der",
                  "die", "das", "und", "foi", "fue", "uma", "los", "las"}


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("º", "").replace("°", "")).strip()


def _facts(rendered: str) -> tuple[str, list, str, set]:
    """(plain text, facts, life-span text, link texts) of a rendered opening sentence. Facts: the link
    texts, the date strings and the words after the life span (occupations, nationality)."""
    p = _paragraph(rendered)
    text = _clean_text(_text(p))
    links = [_clean_text(_text(x)) for x in re.findall(r"<a\b[^>]*>(.*?)</a>", p, re.S)]
    facts = [f for f in dict.fromkeys(links + [_clean_text(d) for d in DATE.findall(text)]) if f]
    span, predicate = "", text
    for m in re.finditer(r"\(([^()]*)\)", text):
        if any(_states(m.group(1), f) for f in facts):
            span, predicate = m.group(1), text[m.end():]
            break
    words = [w for w in re.findall(r"[^\W\d_]{3,}", predicate) if w.casefold() not in PREDICATE_STOP]
    facts += [w for w in words if not any(w.casefold() in _norm(f).split() for f in facts)]
    return text, list(dict.fromkeys(facts)), span, set(links)


def _states(text: str, fact: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(_norm(fact)) + r"(?!\w)", _norm(text)) is not None


def compare_sentences(old_html: str, new_html: str, lacks: set) -> tuple[list, list, list]:
    """(facts of the old sentence the new one lacks, facts of the new sentence the old one lacks that Bio
    could have stated, facts the new one adds that Bio could not state). Facts: link texts, date strings
    and predicate words, each looked for in the other sentence's text; a fact whose place in the sentence
    cannot be told is never an addition."""
    old_text, old_facts, _, _ = _facts(old_html)
    new_text, new_facts, span, links = _facts(new_html)
    missing = [f for f in old_facts if not _states(new_text, f)]
    extra, additions = [], []
    parts = re.split(r"\s[–-]\s", span) if span else []
    for f in new_facts:
        if _states(old_text, f):
            continue
        part = next((i for i, piece in enumerate(parts) if _states(piece, f)), None)
        if part is not None:
            end = ("birth", "death")[part] if len(parts) == 2 else "birth" if len(parts) == 1 else None
            kind = "date" if DATE.fullmatch(f) else "place" if f in links else None
            ok = end is not None and kind is not None and f"{end}_{kind}" in lacks
        else:
            ok = {"occupation", "nationality"} <= lacks
        (additions if ok else extra).append(f)
    return missing, extra, additions


class BioCheck(NamedTuple):
    old: str
    new: str
    missing: list    # facts the old sentence states and the new one does not
    extra: list      # facts the new sentence states, the old one does not, though Bio could have
    additions: list  # facts the new sentence adds that Bio had no parameter for
    absent: bool     # {{SV bio}} is not on the wiki yet


def check_bio(client, plan: PagePlan) -> BioCheck:
    old_html = client.render(plan.title, plan.bio.old)
    rendered = client.render(plan.title, plan.bio.call)
    absent = re.search(r'href="[^"]*SV_bio[^"]*redlink=1', rendered) is not None
    missing, extra, additions = compare_sentences(old_html, rendered, plan.bio.lacks)
    return BioCheck(sentence(old_html), sentence(rendered), missing, extra, additions, absent)


# --- execution ---

def _summary(lang: str) -> str:
    return json.loads((ROOT / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))["switch_summary"]


def accepted(specs: list, pages) -> set:
    """--accept-loss titles -> {(lang, title)} among the connected pages."""
    return {page_key(spec, pages, "a connected page") for spec in specs}


def gather(clients: dict, repo) -> list:
    """(lang, title, item) of every connected article but the main page."""
    out = []
    for lang, client in clients.items():
        titles = client.articles()
        linked = repo.items_for(lang, titles)
        out += [(lang, t, linked[t]) for t in titles if linked.get(t) and linked[t] != MAIN_PAGE_ITEM]
    return out


def _with_imports(dv: dict, mapped: dict) -> dict:
    return item_dv(mapped[dv["import"]]) if "import" in dv else dv


def run(repo, clients: dict, wikidata, mapping: dict, tree: dict, apply: bool, accept=(), pages=None,
        import_log: str | None = None, out=print, tools_dir: str = "<tools-dir>") -> int:
    """`tree`: lang -> the tree's category names (kept by every save); `pages`: (lang, title, item) to look
    at (default: every connected article)."""
    accept = set(accept)
    pages = gather(clients, repo) if pages is None else pages
    names = {v: k for k, v in mapping["props"].items() if isinstance(v, str) and v.startswith("P")}
    resolvers = {}
    plans, counts = [], {"no infobox": 0, "already SV": 0}
    pending = {}  # item -> {prop: page}: statements a page planned earlier in this run writes
    relies = {}   # (lang, title) -> {prop: page}: rows a page leaves to those statements
    for lang, title, item in pages:
        client = clients[lang]
        rev = client.revision(title)
        if rev is None:
            continue
        if has_sv_infobox(rev[1], item):
            counts["already SV"] += 1
            continue
        entity = repo.entity(item)
        label = entity.get("labels", {}).get(lang, {}).get("value")
        resolver = resolvers.setdefault(lang, Resolver(lang, repo, wikidata, client))
        claims = item_claims(entity)
        coming = {prop: where for prop, where in pending.get(item, {}).items() if prop not in claims}
        plan = plan_page(Page(lang, title, item, rev[0], rev[1], label), {**claims, **{p: [None] for p in coming}},
                         mapping, resolver, accept_loss=(lang, title) in accept)
        if plan is None:
            counts["no infobox"] += 1
            continue
        plans.append(plan)
        relies[lang, title] = {p: coming[p] for p in plan.covered if p in coming}
        if not plan.blocked:
            for prop, _, _ in plan.writes:
                pending.setdefault(item, {}).setdefault(prop, f"{lang}:{title}")
        _report(plan, names, out)
        for prop, where in relies[lang, title].items():
            out(f"  {prop} {names.get(prop, '?')}: written for {where} in this run")
        if not apply and plan.new_text is not None:
            _show_preview(client, plan, mapping, out)

    ready = [p for p in plans if not p.blocked]
    imports = list(dict.fromkeys(q for p in ready for q in p.imports))
    out(f"switchable {len(ready)}; blocked {len(plans) - len(ready)}; no infobox {counts['no infobox']}; "
        f"already on {{{{SV infobox}}}} {counts['already SV']}; imports needed {len(imports)}")
    for p in plans:
        if p.blocked:
            out(f"  blocked: {p.lang}:{p.title}: {p.blocked}")
    if not apply:
        later = [q for p in plans if p.blocked for q in p.imports if q not in imports]
        if later:
            out(f"  blocked pages would also need: {', '.join(dict.fromkeys(later))}")
        enrich = list(dict.fromkeys(p.item for p in plans if p.writes))
        if enrich:
            out("statements above are what the items lack now; Wikidata comes first: enrich them on the server, "
                f"then run again: {enrich_command(enrich, tools_dir)}")
        if imports:
            out(f"import command (run on the server before --apply): {import_command(imports, tools_dir)}")
        out("(dry run: nothing written)")
        return 0
    if imports and import_log is None:
        out("Run the import on the server from the MediaWiki root, then rerun with --import-log FILE (its output):")
        out(import_command(imports, tools_dir))
        return 3
    mapped, import_failed = parse_import_log(import_log or "")
    failed, unsafe, saved, blocked = [], [], 0, 0
    for plan in ready:
        where = f"{plan.lang}:{plan.title}"
        missing = [q for q in plan.imports if q not in mapped]
        if missing:
            why = "; ".join(f"import {q}: {import_failed.get(q, 'not in the import log')}" for q in missing)
            failed.append(where)
            out(f"{where}: FAILED {why}")
            continue
        status = _apply(repo, clients[plan.lang], plan, mapping, tree, mapped, out, relies.get((plan.lang, plan.title)))
        if status == "saved":
            saved += 1
        elif status == "blocked":
            blocked += 1
        elif status == "unsafe":
            unsafe.append(where)
        else:
            failed.append(where)
    out(f"pages switched {saved}; blocked by the sentence check {blocked}; unsafe {len(unsafe)}; "
        f"failed {len(failed)}")
    return 1 if failed or unsafe else 0


def _apply(repo, client, plan: PagePlan, mapping: dict, tree: dict, mapped: dict, out, relies=None) -> str:
    where = f"{plan.lang}:{plan.title}"
    image_prop = mapping["props"]["image"]
    params = dict(plan.params)
    # what the item has now: an earlier page of this run may have written a row, or failed to
    current = item_claims(repo.entity(plan.item))
    lacking = sorted({w for p, w in (relies or {}).items() if p not in current})
    if lacking:
        out(f"{where}: FAILED relies on {', '.join(lacking)} for statements that were not written")
        return "failed"
    present = {prop for prop, _, _ in plan.writes if prop in current}
    for prop, dv, shown in plan.writes:
        if prop in present:
            continue
        try:
            repo.add_claim(plan.item, prop, _with_imports(dv, mapped), tags=TAG, summary=_summary("en"))
        except SyncError as exc:
            if prop == image_prop:
                # Commons-only property refusing the file: it stays on the call
                params["image"] = shown
                out(f"{where}: image {shown} refused as a statement ({exc}); kept as image=")
                continue
            out(f"{where}: FAILED writing {prop} = {shown}: {exc}")
            return "failed"
    text = plan.new_text
    if params != plan.params:
        bio_name = (mapping["bio"].get(plan.lang) or {}).get("template")
        text = swap(plan.text, set(mapping["templates"].get(plan.lang, {})), bio_name,
                    call("SV infobox", {k: params[k] for k in ("type", "image", "sortkey") if k in params}),
                    plan.bio and plan.bio.call)
    if plan.bio:
        try:
            check = check_bio(client, plan)
        except SyncError as exc:
            out(f"{where}: FAILED rendering the opening sentences: {exc}")
            return "failed"
        if check.absent:
            out(f"{where}: BLOCKED {{{{SV bio}}}} is not on {plan.lang} yet (deploy the templates first)")
            return "blocked"
        if check.missing or check.extra:
            out(f"{where}: BLOCKED the opening sentences differ: {_differences(check)}")
            out(f"  old: {check.old}")
            out(f"  new: {check.new}")
            return "blocked"
        if check.additions:
            out(f"{where}: the new opening sentence adds {', '.join(check.additions)} (Bio had no parameter)")
    keep = tree.get(plan.lang, set()) & client.page_categories(plan.title)
    status = guarded_save(client, plan.title, text, plan.revid, keep, _summary(plan.lang), out)
    if status == "saved":
        out(f"{where}: switched")
    return str(status)


def _report(plan: PagePlan, names: dict, out) -> None:
    state = f"BLOCKED ({plan.blocked})" if plan.blocked else "switchable"
    out(f"{plan.lang}:{plan.title} [{plan.item}] {plan.layout}: {state}")
    for prop, dv, shown in plan.writes:
        note = f" (import {dv['import']})" if "import" in dv else ""
        out(f"  write {prop} {names.get(prop, '?')} = {shown}{note}")
    if plan.params.get("image"):
        out(f"  image kept on the call: {plan.params['image']}")
    for key, raw in plan.duplicates:
        out(f"  duplicate value for {key}, not written: {raw}")
    for key, raw in plan.unconvertible:
        out(f"  value for {key} not convertible, not written: {raw}")
    for name in plan.missing_images:
        out(f"  image {name}: missing in every repository, dropped")
    for key, raw in plan.dropped:
        out(f"  dropped {key}: {raw}")
    if not plan.dropped:
        for key, raw in plan.unresolved:
            out(f"  unresolved {key}: {raw}")
    if plan.new_text is not None:
        out(f"  infobox call: {call('SV infobox', plan.params)}")
    if plan.bio:
        out(f"  bio call: {plan.bio.call}")


def _show_preview(client, plan: PagePlan, mapping: dict, out) -> None:
    try:
        rows = infobox_rows(client.render(plan.title, preview_text(plan, mapping)))
    except SyncError as exc:
        out(f"  preview failed: {exc}")
        rows = None
    if rows is not None:
        out("  future infobox:" + ("" if rows else " (none rendered)"))
        for row in rows:
            out(f"    {row}")
    if plan.bio:
        try:
            check = check_bio(client, plan)
        except SyncError as exc:
            out(f"  sentences failed: {exc}")
            return
        out(f"  old sentence: {check.old}")
        out(f"  new sentence: {check.new}")
        if check.absent:
            out(f"  sentence check: not possible, {{{{SV bio}}}} is not on {plan.lang} yet "
                "(deploy the templates first)")
            return
        bio_props = {mapping["props"][k] for k in ("birth_date", "birth_place", "death_date", "death_place",
                                                     "occupation", "country_of_citizenship", "sex_or_gender")}
        pending = [w for w in plan.writes if w[0] in bio_props]
        if check.additions:
            out(f"  sentence check: additions Bio had no parameter for: {', '.join(check.additions)}")
        if not check.missing and not check.extra:
            out("  sentence check: same facts")
        elif pending:
            out(f"  sentence check: provisional, {len(pending)} statements not on the item yet; {_differences(check)}")
        else:
            out(f"  sentence check: MISMATCH, {_differences(check)}")


def _differences(check: BioCheck) -> str:
    parts = []
    if check.missing:
        parts.append(f"the new sentence does not state {', '.join(check.missing)}")
    if check.extra:
        parts.append(f"the old sentence does not state {', '.join(check.extra)}")
    return "; ".join(parts)


def tree_names(data) -> dict:
    return {lang: {n.names[lang] for n in nodes(data) if n.names.get(lang)} for lang in LANGS}


def main(argv=None, repo=None, clients=None, wikidata=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.switch_infobox")
    parser.add_argument("--wiki", default=",".join(LANGS))
    parser.add_argument("--page", action="append", default=[], metavar="TITLE",
                        help="only this page (repeatable; LANG:TITLE)")
    parser.add_argument("--accept-loss", action="append", default=[], metavar="TITLE",
                        help="switch this page with its unresolved rows dropped (repeatable; LANG:TITLE)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--import-log", metavar="FILE", help="output of the EnrichItems --import command")
    parser.add_argument("--tools-dir", default=os.environ.get("SV_TOOLS_DIR", "<tools-dir>"),
                        help="server directory holding EnrichItems.php (for the printed command)")
    args = parser.parse_args(argv)
    langs = args.wiki.split(",")
    unknown = [lang for lang in langs if lang not in LANGS]
    if unknown:
        parser.error(f"unknown wiki {', '.join(unknown)}; choose from {', '.join(LANGS)}")
    import_log = None
    if args.import_log:
        try:
            import_log = Path(args.import_log).read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"--import-log: {exc}")
    user, password = os.environ.get("SV_EDITOR_USER"), os.environ.get("SV_EDITOR_PASSWORD")
    if clients is None and args.apply and not (user and password):
        print("ERROR: --apply needs SV_EDITOR_USER and SV_EDITOR_PASSWORD (a bot password)", file=sys.stderr)
        return 1
    try:
        if clients is None:
            clients = {lang: WikiClient(lang) for lang in langs}
            repo = clients.get("en") or WikiClient("en")
            if args.apply:
                for client in {id(c): c for c in [*clients.values(), repo]}.values():
                    client.login(user, password)
        wikidata = wikidata or WikidataClient()
        pages = gather(clients, repo)
        pairs = [(lang, title) for lang, title, _ in pages]
        try:
            accept = accepted(args.accept_loss, pairs)
            only = {page_key(spec, pairs, "a connected page") for spec in args.page}
        except ValueError as exc:
            parser.error(str(exc))
        if only:
            pages = [p for p in pages if (p[0], p[1]) in only]
        return run(repo, clients, wikidata, load_mapping(), tree_names(load(ROOT)), args.apply, accept, pages,
                   import_log, out, args.tools_dir)
    except (SyncError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
