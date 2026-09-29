try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import inbox  # noqa: E402


def issue(n, labels=(), kind=None, ask=None):
    return {"number": n, "title": f"Issue {n}", "url": f"https://x/{n}", "labels": list(labels), "kind": kind, "ask": ask}


class RenderTest(unittest.TestCase):
    def test_groups_items_by_the_action_the_owner_takes(self):
        body = inbox.render([
            issue(3, ["design:awaiting-approval"]),
            issue(4, kind="question"),
            issue(5, ["needs:local"]),
            issue(6, ["needs:owner"], kind="question"),
            issue(7, ["team:demo"], kind="question"),
            issue(8, kind="feature"),
            issue(9, ["needs:owner"], kind="chore"),
        ], updated="now")
        self.assertIn("**6 things need you.**", body)
        order = [body.index(h) for h in ("Release decision", "Designs to approve", "Questions", "Only you can do", "Needs your machine")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("#8 ", body)

    def test_each_question_shows_what_to_answer(self):
        body = inbox.render([issue(4, kind="question", ask="/approve to use R2 (recommended) · /reject why")])
        self.assertIn("#4 [Issue 4](https://x/4) — /approve to use R2 (recommended) · /reject why", body)

    def test_team_decisions_are_listed_for_information_only(self):
        body = inbox.render([], decided=[issue(12, ["team-decided"])])
        self.assertIn("Decided by the team (FYI)", body)
        self.assertIn("**0 things need you.**", body)

    def test_owner_language(self):
        body = inbox.render([issue(4, kind="question")], language="ru")
        self.assertIn("### Вопросы (1)", body)
        self.assertIn("Нужно твоё внимание: 1.", body)

    def test_same_account_mode_is_a_standing_item(self):
        body = inbox.render([issue(4, kind="question")], same_account_url="https://x/checklist")
        self.assertLess(body.index("Security setup"), body.index("Questions"))
        self.assertIn("**2 things need you.**", body)

    def test_pause_is_listed_before_questions(self):
        body = inbox.render([issue(4, kind="question")], paused_url="https://x/log")
        self.assertLess(body.index("Team is paused"), body.index("Questions"))

    def test_empty_inbox_says_so(self):
        body = inbox.render([])
        self.assertIn("Nothing needs you right now.", body)

    def test_singular_wording(self):
        self.assertIn("**1 thing needs you.**", inbox.render([issue(4, kind="question")]))

    def test_body_carries_the_marker(self):
        self.assertTrue(inbox.render([]).startswith(inbox.MARKER))


if __name__ == "__main__":
    unittest.main()
