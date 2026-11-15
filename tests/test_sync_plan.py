import pytest

from sync import __main__ as cli
from sync.plan import PageState, plan
from sync.wiki import SyncError

BOT = "SV templates bot"


def kinds(actions):
    return {a.title: a.kind for a in actions}


def test_plan_create_update_unchanged():
    desired = {"A": "new", "B": "same", "C": "changed"}
    current = {"A": None, "B": PageState("same\n", BOT), "C": PageState("old", BOT)}
    assert kinds(plan(desired, current, BOT, set())) == {"A": "create", "B": "skip-unchanged", "C": "update"}


def test_foreign_edit_skipped():
    current = {"T": PageState("hand edit", "Johnrdorazio")}
    actions = plan({"T": "repo"}, current, BOT, set())
    assert kinds(actions) == {"T": "skip-foreign"}
    assert "hand edit" in actions[0].diff and "repo" in actions[0].diff
    assert kinds(plan({"T": "repo"}, current, BOT, {"T"})) == {"T": "update"}


def test_foreign_but_identical_is_unchanged():
    assert kinds(plan({"T": "x"}, {"T": PageState("x", "Someone")}, BOT, set())) == {"T": "skip-unchanged"}


def test_idempotent():
    desired = {"A": "a", "B": "b\n"}
    current = {t: PageState(c, BOT) for t, c in desired.items()}
    assert set(kinds(plan(desired, current, BOT, set())).values()) == {"skip-unchanged"}


class FakeClient:
    instances = {}
    fail = set()

    def __init__(self, lang, session=None):
        self.lang, self.edits = lang, []
        FakeClient.instances[lang] = self

    def login(self, user, password):
        if self.lang in self.fail:
            raise SyncError("boom")

    def fetch(self, titles):
        return {t: None for t in titles}

    def edit(self, title, text, summary):
        self.edits.append((title, summary))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SV_SYNC_USER", "SV templates bot@sync")
    monkeypatch.setenv("SV_SYNC_PASSWORD", "secret")
    FakeClient.instances, FakeClient.fail = {}, set()


def test_cli_continues_after_wiki_error(env, capsys):
    FakeClient.fail = {"es"}
    code = cli.main(["--wiki", "all"], client_factory=FakeClient, sha="abc1234")
    assert code == 1
    assert FakeClient.instances["en"].edits and FakeClient.instances["pt"].edits
    assert FakeClient.instances["es"].edits == []
    assert "secret" not in capsys.readouterr().out


def test_cli_dry_run_makes_no_edits(env):
    assert cli.main(["--wiki", "en", "--dry-run"], client_factory=FakeClient, sha="abc1234") == 0
    assert FakeClient.instances["en"].edits == []


def test_cli_summary_has_sha(env):
    cli.main(["--wiki", "it"], client_factory=FakeClient, sha="abc1234")
    assert {s for _, s in FakeClient.instances["it"].edits} == {"sync from seminaverbi-templates@abc1234"}


def test_cli_requires_credentials(monkeypatch):
    monkeypatch.delenv("SV_SYNC_USER", raising=False)
    assert cli.main(["--wiki", "en"], client_factory=FakeClient, sha="x") == 2
