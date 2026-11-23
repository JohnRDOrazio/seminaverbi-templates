"""Where a republished article says it first appeared, and the name an article is cited under.

An outlet may republish one author's articles from another (config: `author`, `republishes`). A post counts as that
outlet's only when it says so itself, with the issue's date: a paragraph that is nothing but the credit ("L'Osservatore
Romano dell'8 novembre 2025"), or a credit under © or "Copyright" ("(©L'Osservatore Romano - 13 gennaio 2011)").
Naming the newspaper in the text is not a credit.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre")
_MONTH = "|".join(MONTHS)
# an issue's date: "8 novembre 2025", "21-22 ottobre 2019", "30 aprile-1 maggio 2019", "16/17.1.2006"
_DATE = (rf"(?:(?P<day>\d{{1,2}})°?(?:\s+(?P<month>{_MONTH}))?(?:\s*[-–/]\s*\d{{1,2}}°?)?\s+(?P<last>{_MONTH})\s+"
         r"(?P<year>\d{4})|(?P<nday>\d{1,2})(?:[/-]\d{1,2})?\.(?P<nmonth>\d{1,2})\.(?P<nyear>\d{4}))")
_MARK = r"(?P<mark>(?:©|\bcopyright\b)\s*(?:©\s*)?)"


def _pattern(name: str) -> re.Pattern:
    words = r"\s+".join(re.escape(w).replace("'", "['’]") for w in name.replace("’", "'").split())
    return re.compile(rf"{_MARK}?{words}\s*[,–—-]?\s*(?:del\s+|dell['’]\s*)?{_DATE}", re.IGNORECASE)


def _date(m: re.Match) -> str | None:
    g = m.groupdict()
    try:
        if g["nday"]:
            return date(int(g["nyear"]), int(g["nmonth"]), int(g["nday"])).isoformat()
        month = MONTHS.index((g["month"] or g["last"]).lower()) + 1
        return date(int(g["year"]), month, int(g["day"])).isoformat()
    except ValueError:
        return None


def credit_date(paragraphs: list[str], name: str) -> str | None:
    """The (first) day of the issue of `name` the article says it appeared in; None when it does not say, or names
    several issues."""
    pattern, found = _pattern(name), set()
    for paragraph in paragraphs:
        for m in pattern.finditer(paragraph):
            before, after = paragraph[:m.start()], paragraph[m.end():]
            alone = not re.search(r"\w", before + after)
            photo = re.search(r"\b(?:foto|photo)\W*$", before, re.IGNORECASE)
            if (alone or (m["mark"] and not photo)) and _date(m):
                found.add(_date(m))
    return found.pop() if len(found) == 1 else None


def outlet_name(art, outlets: dict[str, str]) -> str:
    """The name the article is cited under: its own attribution when it has one, else its outlet's."""
    return art.attribution or outlets.get(art.outlet, art.outlet)
