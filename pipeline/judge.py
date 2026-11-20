"""Jev questions for the pipeline. Every Jev request is cached in the queue on its own, keyed by a hash of the
model, the serialized questions (instructions, options) and the state sent: a reworded question or a new model is
asked afresh, and when one request of several fails (a level of tools.jev.classify, say) the paid ones are kept.
The thresholds are applied in code to the cached answers, so changing one costs no request."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from typesafe_sdk import Choice, Noul

from pipeline.domains import DOMAINS, film
from sync.categories import leaf_themes, load
from tools import jev

ROOT = Path(__file__).resolve().parent.parent

# Jev's wording per domain lives in the domain module (QUESTIONS); film's stays importable under these names
PLAUSIBLE = film.QUESTIONS["plausible"]
CONNECTION = film.QUESTIONS["connection"]
CONNECTIONS = film.QUESTIONS["connections"]


def qhash(name: str, instructions: str, state_text: str) -> str:
    return hashlib.sha256("\0".join((name, instructions, state_text)).encode("utf-8")).hexdigest()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _question(q) -> dict:
    """What a typesafe_sdk question sends: its type, instructions and options."""
    return {"type": q.type, "instructions": q.instructions, "criteria": q.criteria}


def question_text(model: str, questions: dict) -> str:
    """The serialized wording of one request (model and questions), stored with its answer and hashed."""
    return _dump({"model": model, "questions": {k: _question(q) for k, q in questions.items()}})


class CachingClient:
    """A Jev client for one queue row: each successful system_one request is stored in the queue's jev cache
    and answered from it when the same request (model, questions, state) is made again."""

    def __init__(self, client, queue, qid):
        self.client, self.queue, self.qid = client, queue, qid

    def system_one(self, state, questions, model=None):
        wording = question_text(model, questions)
        h = qhash("system_one", wording, _dump(state))
        stored = self.queue.jev_get(self.qid, h)
        if stored is None:
            a = self.client.system_one(state=state, questions=questions, model=model)
            stored = {
                "choices": {k: {"choice": c.choice, "probabilities": dict(c.probabilities),
                                "confidence": c.confidence} for k, c in (getattr(a, "choices", None) or {}).items()},
                "nouls": {k: float(n.noul) for k, n in (getattr(a, "nouls", None) or {}).items()},
            }
            self.queue.jev_put(self.qid, h, wording, stored)  # a raised request stores nothing
        return SimpleNamespace(
            choices={k: SimpleNamespace(**c) for k, c in stored["choices"].items()},
            nouls={k: SimpleNamespace(noul=p) for k, p in stored["nouls"].items()})


class Judge:
    """`data` is the category tree (sync.categories.load), read from the repository when not given."""

    def __init__(self, client, queue, data=None):
        self.client, self.queue, self._data = client, queue, data

    @property
    def data(self):
        if self._data is None:
            self._data = load(ROOT)
        return self._data

    def _client(self, row) -> CachingClient:
        return CachingClient(self.client, self.queue, row.qid)

    def _choice(self, row, key, question, state) -> dict:
        a = self._client(row).system_one(state=state, questions={key: question}, model=jev.MODEL).choices[key]
        return {"choice": a.choice, "probabilities": dict(a.probabilities), "confidence": a.confidence}

    @staticmethod
    def _questions(row) -> dict:
        """The wording of the row's domain; film's for a domain without one."""
        return getattr(DOMAINS.get(row.domain, film), "QUESTIONS", film.QUESTIONS)

    def plausible(self, row, synopsis: str) -> float:
        wording = self._questions(row)["plausible"]
        a = self._client(row).system_one(state={"text": synopsis},
                                         questions={"plausible": Noul(instructions=wording)}, model=jev.MODEL)
        return float(a.nouls["plausible"].noul)

    def connection(self, row, dossier_text: str) -> dict:
        wording = self._questions(row)
        q = Choice(instructions=wording["connection"],
                   criteria={k: {"name": v} for k, v in wording["connections"].items()})
        return self._choice(row, "connection", q, {"text": dossier_text})

    def field(self, row, dossier_text: str) -> dict:
        """tools.jev.classify through the cache: each level it asks is cached as it succeeds."""
        return jev.classify(self._client(row), dossier_text, self.data)._asdict()

    def themes(self, row, dossier_text: str) -> dict:
        result = jev.themes(self._client(row), dossier_text, self.data)
        return {key: {"p": p, "status": status} for key, (p, status) in result.items()}

    def evidence(self, row, theme_key: str, paragraphs: list[str]) -> int:
        """Index of the paragraph that best supports the theme (its name and definition are looked up by key)."""
        theme = next((t for t in leaf_themes(self.data) if t.key == theme_key), None)
        name = theme.names["en"] if theme else theme_key
        definition = theme.definition.get("en", "") if theme else ""
        instructions = (f"Which paragraph of the dossier best supports that the work genuinely explores the "
                        f"theme of {name} ({definition})?")
        q = Choice(instructions=instructions, criteria={f"p{i}": {"name": p} for i, p in enumerate(paragraphs)})
        return int(self._choice(row, "evidence", q, {"theme": name})["choice"][1:])
