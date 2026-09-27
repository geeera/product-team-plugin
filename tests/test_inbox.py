import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import inbox  # noqa: E402


def issue(n, labels=(), kind=None):
    return {"number": n, "title": f"Issue {n}", "url": f"https://x/{n}", "labels": list(labels), "kind": kind}


class RenderTest(unittest.TestCase):
    def test_groups_items_by_the_action_the_owner_takes(self):
        body = inbox.render([
            issue(3, ["design:awaiting-approval"]),
            issue(4, kind="question"),
            issue(5, ["needs:local"]),
            issue(6, ["needs:owner"], kind="question"),
            issue(7, ["team:demo"], kind="question"),
            issue(8, kind="feature"),
        ], updated="now")
        self.assertIn("**5 things need you.**", body)
        order = [body.index(h) for h in ("Release decision", "Designs to approve", "Questions", "Only you can do", "Needs your machine")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("#8 ", body)

    def test_pause_is_listed_first(self):
        body = inbox.render([issue(4, kind="question")], paused_url="https://x/log")
        self.assertLess(body.index("Team is paused"), body.index("Questions"))

    def test_empty_inbox_says_so(self):
        body = inbox.render([])
        self.assertIn("**0 things need you.**", body)
        self.assertIn("Nothing needs you right now.", body)

    def test_singular_wording(self):
        self.assertIn("**1 thing needs you.**", inbox.render([issue(4, kind="question")]))

    def test_body_carries_the_marker(self):
        self.assertTrue(inbox.render([]).startswith(inbox.MARKER))


if __name__ == "__main__":
    unittest.main()
