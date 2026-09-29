"""Who counts as the owner speaking: same-account mode vs the agents acting as a GitHub App."""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import importlib.machinery
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import commands, demo, owner  # noqa: E402


def load(name):
    loader = importlib.machinery.SourceFileLoader(f"{name}_cli", str(ROOT / "scripts" / name))
    spec = importlib.util.spec_from_loader(f"{name}_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


backlog = load("backlog")
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

    def test_team_decision_reversal_and_demo_decisions_need_the_owners_login(self):
        decided = comment(BOT, owner.decision_comment("use X"), at="2026-09-29T09:00:00Z")
        self.assertEqual(owner.reversed_by_owner([decided, comment(BOT, "/reject why")], OWNER, NO_EDITS, [BOT]), {})
        self.assertEqual(owner.reversed_by_owner([decided, comment(OWNER, "/reject why")], OWNER, NO_EDITS, [BOT])["text"], "why")
        block = '/demo-decisions\n```json\n{"decisions": {"release": {"decision": "go"}}}\n```'
        self.assertIsNone(demo.decisions_from_comments([comment(BOT, block)], OWNER, NO_EDITS))
        self.assertIsNotNone(demo.decisions_from_comments([comment(OWNER, block)], OWNER, NO_EDITS))


class AnswerCliTest(unittest.TestCase):
    def run_cli(self, argv, app_mode, owner_token="owner-pat", agents_login=BOT):
        posted = []

        def api(path, method="GET", fields=None, auth=None):
            if path == "repos/o/r/issues/7" and method == "GET":
                return {"state": "open", "labels": [{"name": "kind:question"}, {"name": "owner:scope"}]}
            if path == "repos/o/r/issues/7/comments" and method == "POST":
                posted.append({"body": fields["body"], "auth": auth})
                return {"html_url": "https://x/c"}
            raise AssertionError(f"{method} {path}")

        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["backlog", *argv]), mock.patch.object(backlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(backlog.gh, "api", side_effect=api), \
             mock.patch.object(backlog.gh, "app_mode", return_value=app_mode), \
             mock.patch.object(backlog.gh, "owner_token", **{
                 "side_effect" if isinstance(owner_token, Exception) else "return_value": owner_token}), \
             mock.patch.object(backlog.gh, "api_list", return_value=[comment(BOT, "/approve"), comment(OWNER, "/reject no")]), \
             mock.patch.object(backlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(backlog.provenance, "fetch", return_value=NO_EDITS), \
             mock.patch.object(backlog.runlogissue, "acted_on", return_value=([], "")), \
             mock.patch.object(backlog.gh, "token_login", return_value=agents_login), \
             mock.patch.object(backlog.gh, "acts_as_owner", return_value=agents_login == OWNER), \
             mock.patch.object(sys, "stdout", stdout):
            backlog.main()
        return posted, stdout.getvalue()

    def test_answer_is_posted_with_the_owners_token(self):
        posted, _ = self.run_cli(["answer", "7", "--command", "approve", "--owner-said", "да"], app_mode=True)
        self.assertTrue(posted[0]["auth"] == "owner-pat", "token mismatch")
        self.assertTrue(posted[0]["body"].startswith("/approve"))

    def test_same_account_answer_uses_the_agents_own_token(self):
        posted, _ = self.run_cli(["answer", "7", "--command", "approve", "--owner-said", "да"], app_mode=False,
                                 owner_token=None)
        self.assertTrue(posted[0]["auth"] is None, "expected no token")

    def test_answer_without_the_owners_token_in_app_mode_fails_cleanly(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(["answer", "7", "--command", "approve", "--owner-said", "да"], app_mode=True,
                         owner_token=backlog.gh.GhError("needs geeera's own token"))
        self.assertIn("own token", str(caught.exception))

    def test_answers_in_app_mode_are_the_owners_only_and_not_flagged(self):
        _, text = self.run_cli(["answers", "7"], app_mode=True)
        data = json.loads(text)
        self.assertFalse(data["same_account"])
        self.assertEqual(data["agents"], BOT)
        self.assertEqual([c["command"] for c in data["commands"]], ["reject"])

    def test_answers_in_same_account_mode_are_flagged(self):
        _, text = self.run_cli(["answers", "7"], app_mode=False, agents_login=OWNER)
        self.assertTrue(json.loads(text)["same_account"])


class RunLogAuthorsTest(unittest.TestCase):
    ISSUE = {"user": {"login": OWNER}}
    COMMENTS = [comment(OWNER, "old run"), comment(BOT, "new run"), comment("stranger", "forged run")]

    def test_a_log_opened_by_the_owner_accepts_the_team_bot_in_app_mode(self):
        with mock.patch.object(runlog.gh, "app_mode", return_value=True), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT):
            bodies = [c["body"] for c in runlog.team_comments(self.ISSUE, self.COMMENTS, NO_EDITS)]
        self.assertEqual(bodies, ["old run", "new run"])

    def test_same_account_mode_keeps_only_the_log_author(self):
        with mock.patch.object(runlog.gh, "app_mode", return_value=False):
            bodies = [c["body"] for c in runlog.team_comments(self.ISSUE, self.COMMENTS, NO_EDITS)]
        self.assertEqual(bodies, ["old run"])


if __name__ == "__main__":
    unittest.main()
