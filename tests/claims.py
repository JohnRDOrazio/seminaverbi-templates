"""Wikibase statement builders shared by the module tests (Module:SV/Infobox, Module:SV/Bio)."""


def _statement(datavalue, rank=None):
    statement = {"mainsnak": {"snaktype": "value", "datavalue": datavalue}}
    if rank:
        statement["rank"] = rank
    return statement


def item(qid, rank=None):
    return _statement({"type": "wikibase-entityid", "value": {"id": qid}}, rank)


def time(iso, precision, rank=None):
    return _statement({"type": "time", "value": {"time": iso, "precision": precision}}, rank)


def text(value, rank=None):
    return _statement({"type": "string", "value": value}, rank)


def mono(t, lang, rank=None):
    return _statement({"type": "monolingualtext", "value": {"text": t, "language": lang}}, rank)


def novalue():
    return {"mainsnak": {"snaktype": "novalue"}}


def somevalue():
    return {"mainsnak": {"snaktype": "somevalue"}}
