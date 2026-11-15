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
            for name in ("wikibase", "infobox", "bible")}
    data["i18n"] = {lang: json.loads((root / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
                    for lang in LANGS}
    return "-- Generated from config/ and i18n/ by the sync script. Do not edit on the wiki.\nreturn " + _lua(data) + "\n"
