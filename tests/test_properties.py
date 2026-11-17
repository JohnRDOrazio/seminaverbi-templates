import json

from tools import properties


class FakeRepo:
    def __init__(self, existing):
        self.existing, self.created = dict(existing), []

    def find_property(self, label_en):
        return self.existing.get(label_en)

    def new_property(self, labels, descriptions, datatype, tags=None):
        pid = f"P{900 + len(self.created)}"
        self.created.append((labels, descriptions, datatype, tags))
        return pid


def test_properties_have_six_languages():
    for spec in properties.PROPERTIES.values():
        assert set(spec["labels"]) == set(spec["descriptions"]) == {"en", "it", "es", "fr", "de", "pt"}
    assert properties.PROPERTIES["sv_field"]["labels"]["en"] == "SV field"
    assert properties.PROPERTIES["sv_theme"]["labels"]["en"] == "SV Gospel theme"


def test_ensure_reuses_existing(tmp_path):
    path = tmp_path / "wikibase.json"
    path.write_text(json.dumps({"genre": "P344"}))
    repo = FakeRepo({"SV field": "P50"})
    ids = properties.ensure(repo, apply=True, path=path)
    assert ids == {"sv_field": "P50", "sv_theme": "P900"}
    assert [c[2] for c in repo.created] == ["wikibase-item"] and repo.created[0][3] == "seminaverbi-mcp"
    assert json.loads(path.read_text()) == {"genre": "P344", "sv_field": "P50", "sv_theme": "P900"}


def test_ensure_dry_run_creates_nothing(tmp_path):
    path = tmp_path / "wikibase.json"
    path.write_text("{}")
    repo = FakeRepo({})
    assert properties.ensure(repo, apply=False, path=path) == {}
    assert repo.created == [] and path.read_text() == "{}"
