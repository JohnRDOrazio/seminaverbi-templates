"""Jev questions for press articles. Jev reads at most 150 words per question (the callers pass the output of
extract.window/opening); answers are cached in the queue's jev table under the article's key, so a reworded
question or a new model is asked afresh."""
import hashlib

from typesafe_sdk import Choice, Noul

from pipeline.judge import CachingClient
from tools import jev

MAX_WORDS = 150

CULTURE = ("`text` is the title and opening of an article in a Catholic newspaper or magazine. Is the article "
           "mainly about a work of culture (a film, television series, book, poem, piece of music, album, "
           "painting, sculpture, building or play) or about an artist, rather than news, liturgy, politics or "
           "Church affairs?")
PICK = ("`text` is a passage of an article that names `mention`. Which of these Wikidata items does the passage "
        "mean?")
SUBSTANTIVE = ("`text` is a passage of an article that names `mention`. Does the article discuss this work or "
               "person substantively (what it says, means or shows), rather than naming it in passing?")


def article_key(url: str) -> str:
    return "press:" + hashlib.sha1(url.encode("utf-8")).hexdigest()


def _bounded(text: str) -> str:
    if len(text.split()) > MAX_WORDS:
        raise ValueError(f"Jev reads at most {MAX_WORDS} words per question")
    return text


class PressJudge:
    def __init__(self, client, queue):
        self.client, self.queue = client, queue

    def _ask(self, url, key, question, state):
        return CachingClient(self.client, self.queue, article_key(url)).system_one(
            state=state, questions={key: question}, model=jev.MODEL)

    def culture(self, url: str, text: str) -> float:
        state = {"text": _bounded(text)}
        return float(self._ask(url, "culture", Noul(instructions=CULTURE), state).nouls["culture"].noul)

    def pick(self, url: str, passage: str, label: str,
             options: dict[str, str]) -> tuple[str | None, float, float]:
        """The QID Jev picks among `options` ({qid: "<label> — <description>"}), or None for none of them,
        with its probability and confidence."""
        state = {"text": _bounded(passage), "mention": label}
        criteria = {qid: {"name": name} for qid, name in options.items()}
        criteria["none"] = {"name": "None of these"}
        a = self._ask(url, "pick", Choice(instructions=PICK, criteria=criteria), state).choices["pick"]
        return (None if a.choice == "none" else a.choice, a.probabilities[a.choice], a.confidence)

    def substantive(self, url: str, label: str, passage: str) -> float:
        state = {"text": _bounded(passage), "mention": label}
        return float(self._ask(url, "substantive", Noul(instructions=SUBSTANTIVE), state).nouls["substantive"].noul)
