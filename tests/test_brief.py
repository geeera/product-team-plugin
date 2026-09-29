import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import brief, owner  # noqa: E402


def issue(n, labels=(), kind=None, closed_at=None, updated_at=None, body=""):
    return {"number": n, "title": f"Issue {n}", "url": f"https://x/{n}", "labels": list(labels), "kind": kind,
            "closed_at": closed_at, "updated_at": updated_at, "body": body}


class BriefTest(unittest.TestCase):
    def test_only_changes_since_the_last_briefing(self):
        since = "2026-09-28T10:00:00Z"
        closed = [issue(1, ["status:done"], closed_at="2026-09-29T08:00:00Z"),
                  issue(2, ["status:done"], closed_at="2026-09-27T08:00:00Z"),
                  issue(3, [], closed_at="2026-09-29T08:00:00Z")]  # closed as not planned
        runs = [{"at": "2026-09-29T01:00:00Z", "state": "failed"}, {"at": "2026-09-27T01:00:00Z", "state": "failed"}]
        result = brief.summary(since, closed, [], [], runs, [], None, False)
        self.assertEqual([s["number"] for s in result["shipped"]], [1])
        self.assertEqual(result["runs"], {"total": 1, "failed": 1})

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
    def test_answer_is_a_command_the_team_reads(self):
        body = brief.answer_comment("approve", "")
        self.assertTrue(body.startswith("/approve\n"))
        self.assertIn("team chat", body)

    def test_reasons_are_required_for_reject_no_go_and_override(self):
        for command in ("reject", "no-go", "override"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                brief.answer_comment(command, "  ")
        self.assertTrue(brief.answer_comment("reject", "too expensive").startswith("/reject too expensive"))

    def test_unknown_answers_are_refused(self):
        with self.assertRaises(ValueError):
            brief.answer_comment("merge", "")


if __name__ == "__main__":
    unittest.main()
