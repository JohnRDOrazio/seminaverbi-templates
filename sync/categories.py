"""The SeminaVerbi category tree (config/categories.json): fields of the arts and sciences, and Gospel themes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple


class Node(NamedTuple):
    key: str
    names: dict
    parent_key: str | None
    description: dict


def load(root: Path) -> dict:
    return json.loads((Path(root) / "config" / "categories.json").read_text(encoding="utf-8"))


def nodes(data: dict) -> list[Node]:
    """Root first, then the fields depth-first in file order, then the themes root and the themes."""
    out = [Node("root", data["root"]["names"], None, data["root"].get("description", {}))]

    def walk(items, parent):
        for item in items:
            out.append(Node(item["key"], item["names"], parent, item.get("description", {})))
            walk(item.get("children", []), item["key"])

    walk(data["fields"], "root")
    out.append(Node("themes-root", data["themes_root"]["names"], "root", data["themes_root"].get("description", {})))
    walk(data["themes"], "themes-root")
    return out
