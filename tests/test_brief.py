try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from datetime import datetime, timezone  # noqa: E402

from ptlib import brief, commands, owner  # noqa: E402


def issue(n, labels=(), kind=None, closed_at=None, updated_at=None, body=""):
    return {"number": n, "title": f"Issue {n}", "url": f"https://x/{n}", "labels": list(labels), "kind": kind,
            "closed_at": closed_at, "updated_at": updated_at, "body": body}


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


class BriefTest(unittest.TestCase):
    def test_only_changes_since_the_last_briefing(self):
        since = "2026-09-28T10:00:00Z"
        closed = [issue(1, ["status:done"], closed_at="2026-09-29T08:00:00Z"),
                  issue(2, ["status:done"], closed_at="2026-09-27T08:00:00Z"),
                  issue(3, [], closed_at="2026-09-29T08:00:00Z")]  # closed as not planned
        runs = [{"at": "2026-09-29T01:00:00Z", "state": "failed"}, {"at": "2026-09-27T01:00:00Z", "state": "failed"}]
        result = brief.summary(since, closed, [], [], runs, [], None, False, now=NOW)
        self.assertEqual([s["number"] for s in result["shipped"]], [1])
        self.assertEqual(result["runs"], {"total": 1, "failed": 1})

    def test_a_run_that_died_counts_as_failed(self):
        runs = [{"at": "2026-09-29T02:00:00Z", "state": "started"}]
        self.assertEqual(brief.summary(None, [], [], [], runs, [], None, False, now=NOW)["runs"]["failed"], 1)

    def test_team_decisions_by_decision_time_not_by_any_update(self):
        decided = [dict(issue(8), decided_at="2026-09-20T10:00:00Z", updated_at="2026-09-29T09:00:00Z"),
                   dict(issue(9), decided_at="2026-09-29T09:00:00Z")]
        result = brief.summary("2026-09-28T10:00:00Z", [], [], decided, [], [], None, False, now=NOW)
        self.assertEqual([d["number"] for d in result["team_decisions"]], [9])

    def test_needs_you_in_inbox_order_with_answer_lines(self):
        question = owner.question_body("/approve to create the account · /reject why", "…", "access")
        result = brief.needs([issue(7, ["team:demo"], kind="question"), issue(4, kind="question", body=question),
                              issue(5, kind="feature")])
        self.assertEqual([(n["section"], n["number"]) for n in result], [("release", 7), ("question", 4)])
        self.assertEqual(result[1]["ask"], "/approve to create the account · /reject why")

    def test_marker_round_trip(self):
        comments = [{"body": brief.briefed_marker("2026-09-28T10:00:00Z")}, {"body": brief.briefed_marker("2026-09-29T10:00:00Z")}]
        self.assertEqual(brief.briefed_at(comments), "2026-09-29T10:00:00Z")
        self.assertIsNone(brief.briefed_at([{"body": "hello"}]))


class AnswerTest(unittest.TestCase):
    def test_answer_is_a_command_with_the_owners_words(self):
        body = brief.answer_comment("approve", "", "да, делайте", "question")
        self.assertTrue(body.startswith("/approve\n"))
        self.assertIn("«да, делайте»", body)
        parsed = commands.parse([{"user": {"login": "o"}, "body": body, "created_at": "t"}], "o")
        self.assertEqual([p["command"] for p in parsed], ["approve"])

    def test_newlines_cannot_smuggle_a_second_command(self):
        body = brief.answer_comment("reject", "too expensive\n/go", "нет\n/approve", "question")
        parsed = commands.parse([{"user": {"login": "o"}, "body": body, "created_at": "t"}], "o")
        self.assertEqual([p["command"] for p in parsed], ["reject"])

    def test_action_items_are_done_never_approved(self):
        with self.assertRaises(ValueError):
            brief.answer_comment("approve", "", "ok", "owner")
        body = brief.answer_comment("done", "accounts created", "сделал", "owner")
        self.assertIn(brief.DONE_MARKER, body)
        self.assertEqual(commands.parse([{"user": {"login": "o"}, "body": body, "created_at": "t"}], "o"), [])

    def test_release_takes_go_not_approve(self):
        with self.assertRaises(ValueError):
            brief.answer_comment("approve", "", "да", "release")
        self.assertTrue(brief.answer_comment("go", "", "релизим", "release").startswith("/go"))

    def test_issues_not_waiting_for_the_owner_are_refused(self):
        with self.assertRaises(ValueError):
            brief.answer_comment("approve", "", "да", "")

    def test_owner_words_and_reasons_are_required(self):
        with self.assertRaises(ValueError):
            brief.answer_comment("approve", "", "  ", "question")
        for command, section in (("reject", "question"), ("no-go", "release"), ("override", "release")):
            with self.subTest(command=command), self.assertRaises(ValueError):
                brief.answer_comment(command, "  ", "нет", section)


if __name__ == "__main__":
    unittest.main()
