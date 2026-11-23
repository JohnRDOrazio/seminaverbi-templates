import json
import re

from conftest import ROOT
from tools.skilldocs import render_categories, render_templates

SKILL = ROOT / "skills" / "seminaverbi-editor"


def flat(text):
    """Wrapped lines and runs of spaces as single spaces, so a needle can span a line break."""
    return re.sub(r"\s+", " ", text)


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def template_files():
    return {p.name[: -len(".wikitext")]: p.read_text(encoding="utf-8")
            for p in (ROOT / "templates").glob("*.wikitext")}


def reads_placeholder(body, param):
    """True when the wikitext reads {{{param}}} or {{{param|default}}}, not a longer name such as {{{10}}} for 1."""
    return re.search(r"\{\{\{\s*" + re.escape(param) + r"\s*(\||\}\}\})", body) is not None


def test_reads_placeholder_needs_the_whole_name():
    assert reads_placeholder("{{{1}}}", "1") and reads_placeholder("{{{1|x}}}", "1")
    assert reads_placeholder("{{{ 1 }}}", "1")
    assert not reads_placeholder("{{{10}}}", "1") and not reads_placeholder("{{{titles}}}", "title")


def test_every_template_documented():
    documented = set(load("config/templates.json"))
    assert documented == set(template_files()) - {"SV managed"}


def test_documented_params_are_read():
    docs = load("config/templates.json")
    for name, text in template_files().items():
        if name not in docs:
            continue
        invoke = re.search(r"#invoke:SV/(\w+)\|", text)
        if invoke is None:
            # a plain wikitext template: each documented parameter is a {{{param}}} placeholder in the
            # transcluded part (its <noinclude> documentation does not count)
            body = re.sub(r"<noinclude>.*?</noinclude>", "", text, flags=re.S)
            for param in docs[name]["params"]:
                assert reads_placeholder(body, param), f"{name}: {param} is documented but the template never reads it"
            continue
        module = invoke.group(1)
        source = (ROOT / "modules" / "SV" / f"{module}.lua").read_text(encoding="utf-8")
        for param in docs[name]["params"]:
            placeholder = re.match(r"^(\w*?)(N|LANG)(_\w+)?$", param)
            if param.isdigit():
                reads = [f"args[{param}]"]
            elif placeholder:
                # a family of parameters (theme1, theme2, …) is read through a concatenated key
                prefix, _, suffix = placeholder.groups()
                reads = [f"args['{prefix}' .. " if not suffix else f"'{suffix}']"]
            else:
                reads = [f"args.{param}", f"args['{param}']"]
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

    for node in list(walk(data["fields"])) + list(walk(data["themes"])):
        for name in node["names"].values():
            assert name in text, name


def test_skill_mentions_jev_routine():
    from tools import jev
    text = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    for needle in ("`.venv/bin/python -m tools.jev classify <file>` returns one field node and an outcome",
                   "`.venv/bin/python -m tools.jev themes <file>` gives a probability per leaf theme",
                   "-m tools.jev place <node-key> <candidate-parent-key>... --record",
                   f"probability ≥ {jev.CHOICE_PROBABILITY:.2f} and confidence ≥ {jev.CHOICE_CONFIDENCE:.2f}",
                   f"≥ {jev.THEME_APPLY:.2f} `applied`: add it",
                   f"{jev.THEME_ASK:.2f}–{jev.THEME_APPLY:.2f} `author`",
                   f"< {jev.THEME_ASK:.2f}: never add it",
                   "Never let a Jev answer override the author"):
        assert needle in text, needle
    standard = flat((SKILL / "article-standard.md").read_text(encoding="utf-8"))
    assert "Each work has its own page" in standard and "one field category per page" in standard


def test_skill_files_on_the_item():
    text = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    for needle in ("`SV field` once", "`SV Gospel theme` once per theme",
                   "Write them with `wikibase-add-statement` on the repository wiki",
                   "outputs the categories on every language's page",
                   "add a line to `docs/content-tasks.md`"):
        assert needle in text, needle


def test_template_categories_are_allowed():
    for text in ((SKILL / "SKILL.md").read_text(encoding="utf-8"), render_categories(ROOT)):
        assert "maintenance and tracking categories" in text


ITEM_PARAM = "a Wikibase item ID to use instead of the page's own item"


def test_skill_uses_sv_bio_and_bare_infobox():
    text = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    for needle in ("open the article with `{{SV bio}}` on every wiki",
                   "Write the infobox as bare `{{SV infobox}}`",
                   "**Facts belong on the item, never a Wikipedia infobox**",
                   "is imported from Wikidata on the server with `server/EnrichItems.php --import Q…`",
                   "`--wd TITLE=QID`", '`--create "TITLE=description"`', "`TITLE::description`",
                   "`{{SV infobox|image=File name.jpg}}`"):
        assert needle in text, needle
    readme = flat((ROOT / "server" / "README.md").read_text(encoding="utf-8"))
    assert "## Connecting a new article" in readme and "`--dry-run` with `--import` writes nothing" in readme


def test_new_article_sequence_works_with_connect_items():
    skill = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    readme = flat((ROOT / "server" / "README.md").read_text(encoding="utf-8"))
    for text in (skill, readme):
        # connect_items only sees pages that exist: the page is saved first, then linked, one article at a time
        assert "connected to its item before" not in text and "Only then write the page" not in text
        assert "`--only TITLE`" in text and "only that article" in text


def test_infobox_check_allows_the_image_exception():
    skill = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    assert "the article calls bare `{{SV infobox}}` or `{{SV categories}}`," not in skill
    assert "`{{SV infobox|image=…}}` for the non-free image exception" in skill


def test_infobox_item_parameter_is_worded_alike_in_skill_and_templates_docs():
    skill = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    assert f"`item` ({ITEM_PARAM}," in skill
    templates = (SKILL / "templates.md").read_text(encoding="utf-8")
    assert templates.count(f"| `item` | {ITEM_PARAM} (needed when the page is not connected to one) |") >= 3
    assert "when the page is not connected to one |" not in templates  # the old, narrower wording


def test_skill_points_to_the_cleanup_tool():
    import json as _json
    skill = flat((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    for needle in ("`.venv/bin/python -m tools.cleanup_articles --wiki <code> --page TITLE`",
                   "runeditor.py tools.cleanup_articles … --apply", "`config/cleanup.json`"):
        assert needle in skill, needle
    conf = _json.loads((ROOT / "config" / "cleanup.json").read_text(encoding="utf-8"))
    # the targets are the SV templates' English names, as templates.md asks
    docs = load("config/templates.json")
    assert conf["bible_quote"]["target"] in docs and conf["references"]["target"] in docs
