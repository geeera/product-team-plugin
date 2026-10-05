"""Owner requests to the PM (geeera/team-console ADR 0005): the marker grammar and who a request counts from.

The attack the provenance rule closes: an agent holding the owner's credential (same-account mode, or an owner token
in an app-mode session) posts `<!-- pt-owner-request … -->` as the owner's login and moves work between sprints in
the owner's name. Only the console's app sets `performed_via_github_app`, so owner authorship alone never counts.
"""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import commands, ownerrequests, project  # noqa: E402

FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "owner-requests.json").read_text(encoding="utf-8"))
OWNER, BOT, STRANGER = "geeera", "acme-team[bot]", "collaborator"
APP = "team-console-stage"
TEAM = {OWNER, BOT}
NEXT = '<!-- pt-owner-request {"kind":"sprint","target":"next","v":1} -->\nPlease take this into the next sprint.\n'
CURRENT = '<!-- pt-owner-request {"kind":"sprint","target":"current","v":1} -->\nTake this now.\n'


def at(minute: int) -> str:
    return f"2026-10-05T10:{minute:02d}:00Z"


def comment(cid, login, body, minute, app=APP, edited_minute=None):
    return {"id": cid, "user": {"login": login}, "body": body, "created_at": at(minute),
            "updated_at": at(edited_minute if edited_minute is not None else minute),
            "html_url": f"https://github.com/o/r/issues/7#issuecomment-{cid}",
            "performed_via_github_app": {"slug": app, "name": app} if app else None}


def handled(cid, request_id, minute, result="applied", login=BOT, **kw):
    return comment(cid, login, ownerrequests.handled_comment(request_id, result, "fits the sprint", "u"), minute,
                   app=None, **kw)


def evaluate(comments, history=None, slugs=(APP,), acts=False):
    return ownerrequests.evaluate(comments, OWNER, history, slugs, TEAM, acts)


class MarkerFixturesTest(unittest.TestCase):
    def test_request_fixtures(self):
        for case in FIXTURES["request"]:
            with self.subTest(why=case.get("why", case["body"][:60])):
                self.assertEqual(ownerrequests.request_marker_of(case["body"]), case["expect"])

    def test_handled_fixtures(self):
        for case in FIXTURES["handled"]:
            with self.subTest(why=case.get("why", case["body"][:60])):
                self.assertEqual(ownerrequests.handled_marker_of(case["body"]), case["expect"])

    def test_each_parser_refuses_the_other_marker(self):
        # `<!-- pt-owner-request` is a prefix of `<!-- pt-owner-request-handled`: a prefix match would confuse them.
        for case in FIXTURES["request"]:
            if case["expect"]:
                self.assertIsNone(ownerrequests.handled_marker_of(case["body"]))
        for case in FIXTURES["handled"]:
            if case["expect"]:
                self.assertIsNone(ownerrequests.request_marker_of(case["body"]))

    def test_a_request_is_never_an_owner_command(self):
        for case in FIXTURES["request"]:
            if case["expect"]:
                self.assertTrue(commands.is_team_note(case["body"]))
                self.assertEqual(commands.command_lines(case["body"] + "/approve\n", same_account=True), [])

    def test_handled_comment_round_trips_and_writes_the_canonical_bytes(self):
        body = ownerrequests.handled_comment(4567890123, "declined", "the sprint\nis full", "https://x/1", "ru")
        lines = body.split("\n")
        self.assertEqual(lines[0], '<!-- pt-owner-request-handled {"comment_id":4567890123,"result":"declined","v":1} -->')
        self.assertEqual(lines[1], "**PM note**: твоя просьба отклонена. https://x/1")
        self.assertEqual(lines[3], "the sprint is full")  # one line: no reason can start a second marker or command
        self.assertEqual(ownerrequests.handled_marker_of(body), {"comment_id": 4567890123, "result": "declined"})
        self.assertIsNone(ownerrequests.request_marker_of(body))

    def test_a_declined_request_needs_a_reason_and_the_marker_refuses_bad_values(self):
        with self.assertRaises(ValueError):
            ownerrequests.handled_comment(5, "declined", "  ", "u")
        for comment_id, result in ((0, "applied"), (True, "applied"), ("5", "applied"), (5, "done")):
            with self.subTest(comment_id=comment_id, result=result), self.assertRaises(ValueError):
                ownerrequests.handled_marker(comment_id, result)


class ProvenanceTest(unittest.TestCase):
    def reasons(self, found):
        return [e["reason"] for e in found["ignored"]]

    def test_an_unedited_console_request_by_the_owner_is_pending(self):
        found = evaluate([comment(10, OWNER, NEXT, 1)])
        self.assertEqual(found["pending"], {"kind": "sprint", "target": "next", "comment_id": 10, "at": at(1),
                                            "url": "https://github.com/o/r/issues/7#issuecomment-10", "via": APP})
        self.assertEqual(found["ignored"], [])

    def test_an_owner_marker_without_the_app_field_is_ignored(self):
        # The owner's gh token, a PAT, or an agent holding either: GitHub names no app on the comment.
        found = evaluate([comment(10, OWNER, NEXT, 1, app=None)])
        self.assertIsNone(found["pending"])
        self.assertIn("performed_via_github_app: none", self.reasons(found)[0])

    def test_another_app_or_a_slug_in_another_case_is_ignored(self):
        for slug in ("acme-team", "Team-Console-Stage", "team-console-stage-2"):
            with self.subTest(slug=slug):
                found = evaluate([comment(10, OWNER, NEXT, 1, app=slug)])
                self.assertIsNone(found["pending"])
                self.assertIn("not posted by the team console", self.reasons(found)[0])

    def test_no_console_app_configured_means_no_request_counts(self):
        found = evaluate([comment(10, OWNER, NEXT, 1)], slugs=())
        self.assertIsNone(found["pending"])
        self.assertIn("console_app_slugs is not set", self.reasons(found)[0])

    def test_never_honoured_while_an_agent_can_write_as_the_owner(self):
        found = evaluate([comment(10, OWNER, NEXT, 1)], acts=True)
        self.assertIsNone(found["pending"])
        self.assertIn("acts_as_owner", self.reasons(found)[0])

    def test_someone_else_posting_through_the_console_app_is_ignored(self):
        found = evaluate([comment(10, STRANGER, NEXT, 1), comment(11, BOT, NEXT, 2)])
        self.assertIsNone(found["pending"])
        self.assertEqual(len(found["ignored"]), 2)
        self.assertTrue(all("not written by the owner" in r for r in self.reasons(found)))

    def test_an_edited_request_is_ignored_in_rest_only_mode(self):
        found = evaluate([comment(10, OWNER, NEXT, 1, edited_minute=9)], history={"error": "GraphQL blocked"})
        self.assertIsNone(found["pending"])
        self.assertIn("counts only as first written", self.reasons(found)[0])

    def test_an_owner_edit_does_not_count_either(self):
        # Owner commands survive the owner's own edits; a request does not: the editor may be an agent as the owner.
        history = {"issue": None, "comments": {"10": {"body": NEXT, "author": OWNER, "edited": True,
                                                       "edited_at": at(5), "editors": [OWNER], "complete": True}}}
        found = evaluate([comment(10, OWNER, NEXT, 1, edited_minute=5)], history=history)
        self.assertIsNone(found["pending"])
        self.assertIn("edited by geeera", self.reasons(found)[0])

    def test_the_newest_honoured_request_replaces_older_ones(self):
        found = evaluate([comment(10, OWNER, NEXT, 1), comment(11, OWNER, CURRENT, 2),
                          comment(12, OWNER, NEXT, 3, app=None)])
        self.assertEqual((found["pending"]["comment_id"], found["pending"]["target"]), (11, "current"))
        self.assertEqual([e["comment_id"] for e in found["ignored"]], [12])


class HandledMarkerTest(unittest.TestCase):
    def test_a_team_answer_after_the_request_handles_it(self):
        found = evaluate([comment(10, OWNER, NEXT, 1), handled(20, 10, 2)])
        self.assertIsNone(found["pending"])
        self.assertEqual([(h["comment_id"], h["result"]) for h in found["handled"]], [(10, "applied")])

    def test_answers_that_do_not_count(self):
        cases = {
            "by a collaborator": handled(20, 10, 2, login=STRANGER),
            "edited": handled(20, 10, 2, edited_minute=8),
            "before the request": handled(20, 10, 0),
            "for another request": handled(20, 99, 2),
            "quoted in prose": comment(20, BOT, "**Team note**: we will post\n" + ownerrequests.handled_marker(10, "applied"),
                                       2, app=None),
            "malformed": comment(20, BOT, '<!-- pt-owner-request-handled {"comment_id":"10","result":"applied","v":1} -->',
                                 2, app=None),
        }
        for why, answer in cases.items():
            with self.subTest(why=why):
                found = evaluate([comment(10, OWNER, NEXT, 1), answer], history={"error": "rest-only"})
                self.assertEqual(found["pending"]["comment_id"], 10)
                self.assertEqual(found["handled"], [])

    def test_a_new_request_after_a_handled_one_is_pending_again(self):
        found = evaluate([comment(10, OWNER, NEXT, 1), handled(20, 10, 2, result="declined"),
                          comment(11, OWNER, CURRENT, 3)])
        self.assertEqual(found["pending"]["comment_id"], 11)
        self.assertEqual([(h["comment_id"], h["result"]) for h in found["handled"]], [(10, "declined")])


class ConsoleAppSlugsTest(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.yml"
            path.write_text(text, encoding="utf-8")
            return project.console_app_slugs(str(path))

    def test_lists_are_read(self):
        self.assertEqual(self.read("team:\n  plugin_ref: stable\n"), [])
        self.assertEqual(self.read("team:\n  console_app_slugs: []\n"), [])
        self.assertEqual(self.read("team:\n  console_app_slugs: [team-console-prod, 'team-console-stage']\n"),
                         ["team-console-prod", "team-console-stage"])
        self.assertEqual(self.read("team:\n  console_app_slugs:\n    - team-console-dev  # dev\n"),
                         ["team-console-dev"])
        self.assertEqual(project.console_app_slugs("/nonexistent/project.yml"), [])

    def test_a_bot_login_or_an_unreadable_list_is_refused(self):
        for text in ("team:\n  console_app_slugs: ['team-console-prod[bot]']\n",
                     "team:\n  console_app_slugs: [Team-Console]\n", "team:\n  console_app_slugs:\n- x\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text)


if __name__ == "__main__":
    unittest.main()
