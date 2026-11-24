"""python -m tools.properties [--apply]

Ensures the two properties that file SeminaVerbi articles exist on the repository wiki: SV field (the
article's one field in the category tree) and SV Gospel theme (its Gospel themes). An existing property
with the same English label is reused. With --apply the missing ones are created and the IDs written to
config/wikibase.json (keys sv_field, sv_theme); without it nothing is written.
Credentials: SV_EDITOR_USER / SV_EDITOR_PASSWORD (a bot password).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sync.wiki import SyncError, WikiClient, repo_client

ROOT = Path(__file__).resolve().parent.parent
WIKIBASE = ROOT / "config" / "wikibase.json"
TAG = "seminaverbi-mcp"
PROPERTIES = {
    "sv_field": {
        "labels": {"en": "SV field", "it": "campo SV", "es": "campo SV", "fr": "domaine SV",
                   "de": "SV-Fachgebiet", "pt": "campo SV"},
        "descriptions": {
            "en": "the field of the arts and sciences a Semina Verbi article belongs to (one tree category)",
            "it": "il campo delle arti e delle scienze di una voce di Semina Verbi (una categoria dell'albero)",
            "es": "el campo de las artes y las ciencias de un artículo de Semina Verbi (una categoría del árbol)",
            "fr": "le domaine des arts et des sciences d'un article de Semina Verbi (une catégorie de l'arbre)",
            "de": "das Fachgebiet der Künste und Wissenschaften eines Semina-Verbi-Artikels (eine Kategorie des Baums)",
            "pt": "o campo das artes e das ciências de um artigo de Semina Verbi (uma categoria da árvore)",
        },
    },
    "sv_theme": {
        "labels": {"en": "SV Gospel theme", "it": "tema evangelico SV", "es": "tema evangélico SV",
                   "fr": "thème évangélique SV", "de": "SV-Evangeliumsthema", "pt": "tema evangélico SV"},
        "descriptions": {
            "en": "a Gospel theme a Semina Verbi article's subject genuinely explores (a theme category)",
            "it": "un tema evangelico che il soggetto di una voce di Semina Verbi esplora davvero (una categoria dei temi)",
            "es": "un tema evangélico que el tema de un artículo de Semina Verbi explora de verdad (una categoría de temas)",
            "fr": "un thème évangélique que le sujet d'un article de Semina Verbi explore vraiment (une catégorie de thèmes)",
            "de": "ein Evangeliumsthema, das der Gegenstand eines Semina-Verbi-Artikels wirklich behandelt (eine Themenkategorie)",
            "pt": "um tema evangélico que o assunto de um artigo de Semina Verbi explora de facto (uma categoria de temas)",
        },
    },
}


def ensure(repo, apply: bool, path: Path = WIKIBASE) -> dict[str, str]:
    """key -> property ID for the properties that exist (or were created when `apply`)."""
    ids = {}
    for key, spec in PROPERTIES.items():
        pid = repo.find_property(spec["labels"]["en"])
        if pid is None and apply:
            pid = repo.new_property(spec["labels"], spec["descriptions"], "wikibase-item", tags=TAG)
        if pid:
            ids[key] = pid
    if apply and ids:
        config = json.loads(path.read_text(encoding="utf-8"))
        config.update(ids)
        path.write_text(json.dumps(dict(sorted(config.items())), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return ids


def main(argv=None, repo=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.properties")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if repo is None:
            repo = repo_client(factory=WikiClient)
            if args.apply:
                repo.login(os.environ["SV_EDITOR_USER"], os.environ["SV_EDITOR_PASSWORD"])
        ids = ensure(repo, args.apply)
    except (SyncError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for key, spec in PROPERTIES.items():
        label = spec["labels"]["en"]
        print(f"{key} ({label}): {ids.get(key) or ('missing' if not args.apply else 'FAILED')}")
    return 0 if len(ids) == len(PROPERTIES) or not args.apply else 1


if __name__ == "__main__":
    sys.exit(main())
