"""Who counts as the owner speaking: same-account mode vs the agents acting as a GitHub App."""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import importlib.machinery
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import commands, owner  # noqa: E402


def load(name):
    loader = importlib.machinery.SourceFileLoader(f"{name}_cli", str(ROOT / "scripts" / name))
    spec = importlib.util.spec_from_loader(f"{name}_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


runlog = load("runlog")

OWNER, BOT = "geeera", "acme-team[bot]"
RELAYED = "/approve\n\n_Answered by the owner in the team chat: «да»_\n"


NO_EDITS = {"issue": None, "comments": {}}  # GitHub knows of no edits to any of these comments


def comment(login, body, at="2026-09-29T10:00:00Z"):
    return {"user": {"login": login}, "body": body, "created_at": at, "updated_at": at, "id": 1,
            "html_url": "https://x/1"}


class CommandAttributionTest(unittest.TestCase):
    def test_a_bot_comment_never_counts_as_an_owner_command(self):
        # Even a comment that claims to relay the owner's words: in app mode only the owner's login speaks for them.
        found = commands.parse([comment(BOT, RELAYED), comment(BOT, "/go ship it")], OWNER, NO_EDITS)
        self.assertEqual(found, [])

    def test_the_owners_own_comment_counts(self):
        found = commands.parse([comment(BOT, "/reject no"), comment(OWNER, RELAYED)], OWNER, NO_EDITS)
        self.assertEqual([c["command"] for c in found], ["approve"])

    def test_a_team_decision_reversal_needs_the_owners_login(self):
        decided = comment(BOT, owner.decision_comment("use X"), at="2026-09-29T09:00:00Z")
        self.assertEqual(owner.reversed_by_owner([decided, comment(BOT, "/reject why")], OWNER, NO_EDITS, [BOT]), {})
        self.assertEqual(owner.reversed_by_owner([decided, comment(OWNER, "/reject why")], OWNER, NO_EDITS, [BOT])["text"], "why")


class RunLogAuthorsTest(unittest.TestCase):
    ISSUE = {"user": {"login": OWNER}}
    COMMENTS = [comment(OWNER, "old run"), comment(BOT, "new run"), comment("stranger", "forged run")]

    def test_a_log_opened_by_the_owner_accepts_the_team_bot_in_app_mode(self):
        with mock.patch.object(runlog.gh, "app_mode", return_value=True), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT):
            bodies = [c["body"] for c in runlog.team_comments(self.ISSUE, self.COMMENTS, NO_EDITS)[0]]
        self.assertEqual(bodies, ["old run", "new run"])

    def test_same_account_mode_keeps_only_the_log_author(self):
        with mock.patch.object(runlog.gh, "app_mode", return_value=False):
            bodies = [c["body"] for c in runlog.team_comments(self.ISSUE, self.COMMENTS, NO_EDITS)[0]]
        self.assertEqual(bodies, ["old run"])


if __name__ == "__main__":
    unittest.main()
