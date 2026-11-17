import json
import re

from conftest import ROOT
from tools.skilldocs import render_categories, render_templates

SKILL = ROOT / "skills" / "seminaverbi-editor"


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def template_files():
    return {p.name[: -len(".wikitext")]: p.read_text(encoding="utf-8")
            for p in (ROOT / "templates").glob("*.wikitext")}


def test_every_template_documented():
    documented = set(load("config/templates.json"))
    assert documented == set(template_files()) - {"SV managed"}


def test_documented_params_are_read():
    docs = load("config/templates.json")
    for name, text in template_files().items():
        if name not in docs:
            continue
        module = re.search(r"#invoke:SV/(\w+)\|", text).group(1)
        source = (ROOT / "modules" / "SV" / f"{module}.lua").read_text(encoding="utf-8")
        for param in docs[name]["params"]:
            reads = [f"args[{param}]"] if param.isdigit() else [f"args.{param}", f"args['{param}']"]
            assert any(r in source for r in reads), f"{name}: {param} is documented but {module}.lua never reads it"


def test_generated_docs_current():
    for fname, render in (("templates.md", render_templates), ("categories.md", render_categories)):
        committed = (SKILL / fname).read_text(encoding="utf-8")
        assert committed == render(ROOT), f"{fname} is stale: run python -m tools.skilldocs"


def test_templates_md_lists_aliases_and_bible_defaults():
    text = render_templates(ROOT)
    for lang, aliases in load("config/aliases.json").items():
        for alias in aliases:
            assert alias.split(":", 1)[1] in text, alias
    for lang, version in load("config/bible.json")["defaults"].items():
        assert f"| {lang} | {version}" in text


def test_categories_md_has_every_name():
    text = render_categories(ROOT)
    data = load("config/categories.json")

    def walk(items):
        for item in items:
            yield item
            yield from walk(item.get("children", []))

    for node in list(walk(data["fields"])) + data["themes"]:
        for name in node["names"].values():
            assert name in text, name
