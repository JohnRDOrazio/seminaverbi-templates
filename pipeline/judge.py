"""Jev questions for the pipeline, each cached in the queue by a hash of its wording and its input."""
import hashlib
import json
from pathlib import Path

from typesafe_sdk import Choice, Noul

from sync.categories import leaf_themes, load
from tools import jev

ROOT = Path(__file__).resolve().parent.parent

PLAUSIBLE = ("Does the work described in `text` plausibly show the influence of the Gospel, or a seed of the "
             "Word (a moral or philosophical truth that the Gospel brings to fulfilment)?")
CONNECTION = ("`text` is a dossier on one work. How does the work relate to the Christian faith and the Bible? "
              "Judge the work itself, not its marketing.")
CONNECTIONS = {
    "direct": "direct Christian or biblical influence",
    "seeds": "implicit seeds of the Word (moral or philosophical truth)",
    "entertainment": "biblical imagery used for entertainment",
    "none": "no real connection",
}


def qhash(name: str, instructions: str, state_text: str) -> str:
    return hashlib.sha256("\0".join((name, instructions, state_text)).encode("utf-8")).hexdigest()


class Judge:
    """`data` is the category tree (sync.categories.load), read from the repository when not given."""

    def __init__(self, client, queue, data=None):
        self.client, self.queue, self._data = client, queue, data

    @property
    def data(self):
        if self._data is None:
            self._data = load(ROOT)
        return self._data

    def _cached(self, row, name, instructions, state_text, ask) -> dict:
        """The cached answer, else ask() -> dict, stored. An exception from ask() stores nothing."""
        h = qhash(name, instructions, state_text)
        hit = self.queue.jev_get(row.qid, h)
        if hit is not None:
            return hit
        answer = ask()
        self.queue.jev_put(row.qid, h, name, answer)
        return answer

    def _choice(self, key, question, state) -> dict:
        a = self.client.system_one(state=state, questions={key: question}, model=jev.MODEL).choices[key]
        return {"choice": a.choice, "probabilities": dict(a.probabilities), "confidence": a.confidence}

    def plausible(self, row, synopsis: str) -> float:
        def ask():
            a = self.client.system_one(state={"text": synopsis}, questions={"plausible": Noul(instructions=PLAUSIBLE)},
                                       model=jev.MODEL)
            return {"p": float(a.nouls["plausible"].noul)}
        return self._cached(row, "plausible", PLAUSIBLE, synopsis, ask)["p"]

    def connection(self, row, dossier_text: str) -> dict:
        q = Choice(instructions=CONNECTION, criteria={k: {"name": v} for k, v in CONNECTIONS.items()})
        return self._cached(row, "connection", CONNECTION, dossier_text,
                            lambda: self._choice("connection", q, {"text": dossier_text}))

    def field(self, row, dossier_text: str) -> dict:
        return self._cached(row, "field", f"tools.jev.classify {jev.MODEL}", dossier_text,
                            lambda: jev.classify(self.client, dossier_text, self.data)._asdict())

    def themes(self, row, dossier_text: str) -> dict:
        def ask():
            result = jev.themes(self.client, dossier_text, self.data)
            return {key: {"p": p, "status": status} for key, (p, status) in result.items()}
        return self._cached(row, "themes", f"tools.jev.themes {jev.MODEL}", dossier_text, ask)

    def evidence(self, row, theme_key: str, paragraphs: list[str]) -> int:
        """Index of the paragraph that best supports the theme (its name and definition are looked up by key)."""
        theme = next((t for t in leaf_themes(self.data) if t.key == theme_key), None)
        name = theme.names["en"] if theme else theme_key
        definition = theme.definition.get("en", "") if theme else ""
        instructions = (f"Which paragraph of the dossier best supports that the work genuinely explores the "
                        f"theme of {name} ({definition})?")
        q = Choice(instructions=instructions, criteria={f"p{i}": {"name": p} for i, p in enumerate(paragraphs)})
        answer = self._cached(row, "evidence", instructions, json.dumps(paragraphs, ensure_ascii=False),
                              lambda: {"index": int(self._choice("evidence", q, {"theme": name})["choice"][1:])})
        return answer["index"]
