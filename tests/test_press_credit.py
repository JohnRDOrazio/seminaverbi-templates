"""Whether a republished article says where it was first published, and the attribution that follows from it."""
import sqlite3

import pytest

from pipeline.press.credit import credit_date, outlet_name
from pipeline.press.enrich import enrichment
from pipeline.press.notes import press_notes
from pipeline.press.stages import Pages, run_filter
from pipeline.press.store import Mention, PressStore
from tests.test_press_entry import FEED, URL, entry, feed
from tests.test_press_fetch import Resp
from tests.test_press_stages import ROBOTS, Judge, client

OR = "L'Osservatore Romano"


@pytest.mark.parametrize("line,date", [  # every form seen on the blog, 2008-2025
    ("L’Osservatore Romano dell’8 novembre 2025", "2025-11-08"),
    ("L'Osservatore Romano del 6 aprile 2024", "2024-04-06"),
    ("©L’Osservatore Romano del 10 novembre 2021", "2021-11-10"),
    ("(©L'Osservatore Romano, 23 luglio 2020)", "2020-07-23"),
    ("(©L'Osservatore Romano, 21-22 ottobre 2019)", "2019-10-21"),
    ("(©L'Osservatore Romano – 7/8 novembre 2011)", "2011-11-07"),
    ("(©L'Osservatore Romano - 13 gennaio 2011)", "2011-01-13"),
    ("( ©L'Osservatore Romano 2 luglio 2017) didascalie in alto: Pantheon Nadar. © RMN", "2017-07-02"),
    ("Copyright L'Osservatore Romano 10 maggio 2006", "2006-05-10"),
    ("Copyright L’Osservatore Romano 18.01.2008", "2008-01-18"),
    ("Copyright L'Osservatore Romano 16/17.1.2006", "2006-01-16"),
    ("saga; e nulla sarà più come prima. (©L'Osservatore Romano – 31 ottobre 2012)", "2012-10-31"),
    ("L'Osservatore Romano del 1° maggio 2019", "2019-05-01"),
    ("©L'Osservatore Romano, 30 aprile-1 maggio 2019", "2019-04-30"),
])
def test_the_forms_posts_use(line, date):
    assert credit_date(["Il film è bello.", line], OR) == date


@pytest.mark.parametrize("text", [
    "d'accordo con Silvia Guidi su «L'Osservatore Romano» del 13 ottobre: «Troppo difficile»",
    "Andrea Monda, direttore dell’Osservatore Romano, ha concluso l'incontro il 24 ottobre 2019",
    "Come scriveva L'Osservatore Romano del 6 aprile 2024, il film è bello.",
    "un implacabile osservatore della società",
    "Su L'Osservatore Romano di oggi troverete altri articoli",
    "L'Osservatore Romano del 31 febbraio 2024",
    "Foto © L'Osservatore Romano, 23 luglio 2020",
    "Avvenire del 6 aprile 2024",
    "L'Osservatore Romano del 6 aprile 2024 ne parlava già",
])
def test_a_mention_of_the_newspaper_is_not_a_credit(text):
    assert credit_date([text], OR) is None


def test_credits_of_different_dates_are_no_credit():
    lines = ["(©L'Osservatore Romano, 23 luglio 2020)"]
    assert credit_date(lines * 3, OR) == "2020-07-23"
    assert credit_date(lines + ["(©L'Osservatore Romano, 24 luglio 2020)"], OR) is None


OUTLETS = [{"id": "osservatore", "name": OR},
           {"id": "o", "name": "Gaetano Vallini, Camera con vista", "author": "Gaetano Vallini",
            "republishes": "osservatore", "article_feed": "https://x.example/feeds/posts/default?path={path}"}]
NAMES = {o["id"]: o["name"] for o in OUTLETS}
CFG = {"max_articles_per_day": 10, "culture_drop": 0.5, "angle_drop": 0.4, "outlets": OUTLETS}


def filtered(content):
    store = PressStore(sqlite3.connect(":memory:"))
    store.add_article(URL, "o", "it", published="2024-04-24")
    run_filter(store, Pages(client({ROBOTS: Resp(""), FEED: Resp(feed(entry(content)))}), OUTLETS), Judge(), CFG)
    store.add_mention(Mention(URL, "Q1", "Film", "work", "film", 0.9, ["passo"], "enrichment"))
    return store


def test_a_credited_post_is_attributed_to_the_newspaper_with_its_date():
    store = filtered("<p>Bello.</p><b>L’Osservatore Romano del 23 aprile 2024</b>")
    art = store.article(URL)
    assert outlet_name(art, NAMES) == "Gaetano Vallini, L'Osservatore Romano"
    assert art.published == "2024-04-23" and art.evidence_outlet == "osservatore"
    (note,) = press_notes(store, "Q1", NAMES)
    assert note["label"] == "Gaetano Vallini, L'Osservatore Romano (2024-04-23): Un film" and note["url"] == URL
    assert f"Gaetano Vallini, L'Osservatore Romano, 2024-04-23, [{URL} Un film]" in enrichment(store, NAMES, None)


def test_an_uncredited_post_is_the_blogs_with_the_posts_date():
    store = filtered("<p>Bello, come scriveva L'Osservatore Romano del 23 aprile 2024, e nuovo.</p>")
    art = store.article(URL)
    assert outlet_name(art, NAMES) == "Gaetano Vallini, Camera con vista"
    assert art.published == "2024-04-24" and art.evidence_outlet == "o"
    (note,) = press_notes(store, "Q1", NAMES)
    assert note["label"] == "Gaetano Vallini, Camera con vista (2024-04-24): Un film"


def test_other_outlets_are_not_searched_for_credits():
    store = PressStore(sqlite3.connect(":memory:"))
    store.add_article("https://x.example/a", "n", "it")
    run_filter(store, Pages(client({ROBOTS: Resp(""), "https://x.example/a": Resp(
        "<p>Bello.</p><p>L’Osservatore Romano del 23 aprile 2024</p>")})), Judge(), CFG)
    art = store.article("https://x.example/a")
    assert art.attribution is None and art.evidence_outlet == "n"
