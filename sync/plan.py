"""Decide, page by page, what the sync should do. Pure: no network."""
from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class PageState:
    content: str
    last_user: str


@dataclass(frozen=True)
class Action:
    title: str
    kind: str  # create | update | skip-unchanged | skip-foreign
    diff: str = ""


def _same(a: str, b: str) -> bool:
    return a.rstrip() == b.rstrip()


def _diff(title: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.rstrip().splitlines(True), new.rstrip().splitlines(True),
        fromfile=f"{title} (wiki)", tofile=f"{title} (repo)",
    ))


def plan(desired: dict[str, str], current: dict[str, PageState | None], bot_user: str,
         force: set[str]) -> list[Action]:
    actions = []
    for title in sorted(desired):
        new = desired[title]
        state = current.get(title)
        if state is None:
            actions.append(Action(title, "create", _diff(title, "", new)))
        elif _same(state.content, new):
            actions.append(Action(title, "skip-unchanged"))
        elif state.last_user != bot_user and title not in force:
            actions.append(Action(title, "skip-foreign", _diff(title, state.content, new)))
        else:
            actions.append(Action(title, "update", _diff(title, state.content, new)))
    return actions
