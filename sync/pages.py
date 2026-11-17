"""What the repository says each wiki should contain."""
from __future__ import annotations

import json
from pathlib import Path

LANGS = ["en", "it", "es", "fr", "de", "pt"]


def _lua(value, indent=""):
    if isinstance(value, dict):
        inner = indent + "\t"
        items = [f"{inner}[{_lua(k)}] = {_lua(value[k], inner)}," for k in sorted(value)]
        return "{\n" + "\n".join(items) + "\n" + indent + "}" if items else "{}"
    if isinstance(value, list):
        inner = indent + "\t"
        items = [f"{inner}{_lua(v, inner)}," for v in value]
        return "{\n" + "\n".join(items) + "\n" + indent + "}" if items else "{}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def compile_data(root: Path) -> str:
    """Lua source of Module:SV/Data, built from config/*.json and i18n/*.json."""
    root = Path(root)
    data = {name: json.loads((root / "config" / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("wikibase", "infobox", "bible", "categories")}
    data["i18n"] = {lang: json.loads((root / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
                    for lang in LANGS}
    return "-- Generated from config/ and i18n/ by the sync script. Do not edit on the wiki.\nreturn " + _lua(data) + "\n"


REPO_URL = "https://github.com/JohnRDOrazio/seminaverbi-templates"
NOTICE = f"This page is managed in git and overwritten by the sync: edit it at {REPO_URL}"
MANAGED = "{{SV managed}}"


def _template(name: str, text: str) -> str:
    if name == "SV managed" or MANAGED in text:
        return text
    if "<noinclude>" in text:
        return text.replace("<noinclude>", "<noinclude>" + MANAGED + "\n", 1)
    return text + "<noinclude>" + MANAGED + "</noinclude>"


def desired_pages(root: Path, lang: str, sha: str) -> dict[str, str]:
    """Title -> content for one wiki. Identical on every wiki except the alias redirects.

    The sha is accepted for symmetry with the edit summary but never written into content,
    so syncing an unchanged repository twice edits nothing.
    """
    root = Path(root)
    pages: dict[str, str] = {}
    for path in sorted((root / "modules").rglob("*.lua")):
        title = "Module:" + path.relative_to(root / "modules").with_suffix("").as_posix()
        pages[title] = f"-- {NOTICE}\n" + path.read_text(encoding="utf-8")
    pages["Module:SV/Data"] = compile_data(root)
    for path in sorted((root / "templates").rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root / "templates").as_posix()
        # read only the files that become pages: anything else (.DS_Store, images) may not be UTF-8
        if path.suffix == ".wikitext":
            name = rel[: -len(".wikitext")]
            pages["Template:" + name] = _template(name, path.read_text(encoding="utf-8"))
        elif path.suffix == ".css":
            pages["Template:" + rel] = f"/* {NOTICE} */\n" + path.read_text(encoding="utf-8")
    aliases = json.loads((root / "config" / "aliases.json").read_text(encoding="utf-8"))
    for alias, target in sorted(aliases.get(lang, {}).items()):
        pages[alias] = f"#REDIRECT [[{target}]]"
    return pages
