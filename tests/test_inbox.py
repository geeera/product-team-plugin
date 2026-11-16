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
        self.assertIn(r"#4 [Issue 4](https://x/4) — /approve to use R2 \(recommended\) · /reject why", body)

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


class LinkSchemeTest(unittest.TestCase):
    """team-console#628: the console's rows are data, so only a web URL becomes a markdown link."""

    def test_https_and_local_http_become_links(self):
        for url in ("https://console.example/p/tc/t/4", "http://localhost:4200/p/tc/t/4", "http://127.0.0.1:8787/x",
                    "http://[::1]:8787/x"):
            with self.subTest(url=url):
                self.assertEqual(inbox.item_line({"number": 4, "title": "T", "url": url}), f"- #4 [T]({url})")

    def test_any_other_url_leaves_the_title_as_plain_text(self):
        for url in ("javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,x", "http://console.example/x",
                    "/p/tc/t/4", "//evil.example/x", "https://x.example/a) [b](javascript:1", "https://a b.example",
                    "https://user:pw@x.example/", "https://[::1/x", "ftp://x.example/", 42):
            with self.subTest(url=url):
                self.assertEqual(inbox.item_line({"number": 4, "title": "T", "url": url, "ask": "/approve"}),
                                 "- #4 T — /approve")


class MarkdownEscapeTest(unittest.TestCase):
    """team-console#661: a title/ask the console supplies must never close the markdown link early and
    open a link of its own."""

    def test_title_cannot_close_the_link_and_open_a_javascript_link(self):
        line = inbox.item_line({"title": "x](javascript:alert(1))", "url": "https://a.b/c"})
        self.assertEqual(line, r"- [x\]\(javascript:alert\(1\)\)](https://a.b/c)")

    def test_title_cannot_spoof_a_second_https_link(self):
        line = inbox.item_line({"title": "x](https://evil.example)", "url": "https://a.b/c"})
        self.assertEqual(line, r"- [x\]\(https://evil.example\)](https://a.b/c)")
        # the only real link markdown can parse out of the line is the trusted url, not evil.example
        self.assertEqual(line.count("]("), 1)

    def test_ask_is_escaped_too(self):
        line = inbox.item_line({"title": "T", "url": "https://a.b/c", "ask": "x](javascript:alert(1))"})
        self.assertEqual(line, r"- [T](https://a.b/c) — x\]\(javascript:alert\(1\)\)")

    def test_escapes_every_markdown_control_character(self):
        line = inbox.item_line({"title": r"\[]()<>*_`", "url": None})
        self.assertEqual(line, r"- \\\[\]\(\)\<\>\*\_\`")

    def test_newlines_cannot_forge_extra_lines(self):
        line = inbox.item_line({"title": "T\n- #999 [fake](https://evil.example)", "url": None})
        self.assertEqual(line, r"- T - #999 \[fake\]\(https://evil.example\)")
        self.assertEqual(line.count("\n"), 0)


if __name__ == "__main__":
    unittest.main()
