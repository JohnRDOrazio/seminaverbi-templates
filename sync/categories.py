"""The SeminaVerbi category tree (config/categories.json): fields of the arts and sciences, and Gospel themes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple


class Node(NamedTuple):
    key: str
    names: dict
    parent_key: str | None
    definition: dict  # English required; other languages optional


def load(root: Path) -> dict:
    return json.loads((Path(root) / "config" / "categories.json").read_text(encoding="utf-8"))


def nodes(data: dict) -> list[Node]:
    """Root first, then the fields depth-first in file order, then the themes root and the themes."""
    out = [Node("root", data["root"]["names"], None, data["root"].get("definition", {}))]

    def walk(items, parent):
        for item in items:
            out.append(Node(item["key"], item["names"], parent, item.get("definition", {})))
            walk(item.get("children", []), item["key"])

    walk(data["fields"], "root")
    out.append(Node("themes-root", data["themes_root"]["names"], "root", data["themes_root"].get("definition", {})))
    walk(data["themes"], "themes-root")
    return out


def leaf_themes(data: dict) -> list[Node]:
    """The themes an article can be tagged with: theme nodes without children (never a theme group)."""
    group_keys = {g["key"] for g in data["themes"]}
    parents = {n.parent_key for n in nodes(data)}
    themes_part = nodes(data)[[n.key for n in nodes(data)].index("themes-root") + 1:]
    return [n for n in themes_part if n.key not in group_keys and n.key not in parents]


def field_children(data: dict, key: str | None) -> list[Node]:
    """Children of a field node in file order; key None gives the top-level fields."""
    parent = "root" if key is None else key
    fields_part = nodes(data)[1:[n.key for n in nodes(data)].index("themes-root")]
    return [n for n in fields_part if n.parent_key == parent]
