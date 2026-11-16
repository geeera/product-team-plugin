try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import digest, inbox  # noqa: E402


def issue(n, labels=(), kind=None, ask=None):
    return {"number": n, "title": f"Issue {n}", "url": f"https://x/{n}", "labels": list(labels), "kind": kind, "ask": ask}


class DigestTest(unittest.TestCase):
    def test_round_trip_from_the_inbox(self):
        body = inbox.render([issue(72, kind="question", ask="/approve when the accounts exist · /reject why")],
                            decided=[issue(159, ["team-decided"])], language="ru")
        sections = digest.parse_inbox(body)
        self.assertEqual(digest.needs_count(sections), 1)
        text = digest.format_digest("storify", sections, "ru")
        self.assertTrue(text.startswith("storify: нужно твоё внимание — 1"))
        self.assertIn("• #72 Issue 72\n  Ответ: /approve when the accounts exist · /reject why\n  https://x/72", text)
        self.assertIn("• #159 Issue 159", text)

    def test_items_without_numbers_or_asks(self):
        body = inbox.render([], same_account_url="https://x/checklist")
        text = digest.format_digest("p", digest.parse_inbox(body))
        self.assertIn("• Agents use your GitHub account", text)
        self.assertNotIn("Answer:", text)

    def test_standing_security_item_alone_does_not_push(self):
        body = inbox.render([], same_account_url="https://x/checklist", language="ru")
        self.assertEqual(digest.needs_count(digest.parse_inbox(body)), 0)

    def test_truncation_counts_bytes(self):
        text = "я" * 3000  # 6000 bytes
        cut = digest.truncate_bytes(text, 4000)
        self.assertLessEqual(len(cut.encode()), 4000)
        self.assertTrue(cut.endswith("…"))

    def test_empty_inbox_needs_nothing(self):
        self.assertEqual(digest.needs_count(digest.parse_inbox(inbox.render([]))), 0)

    def test_newline_in_a_title_or_ask_cannot_forge_an_extra_line(self):
        # format_digest is also fed sections built straight from console data, not only round-tripped through
        # inbox.render's markdown (which already flattens newlines) — so it must flatten them itself too.
        sections = [("Questions", [{"number": "9", "title": "T\n• #1 fake", "url": None,
                                    "ask": "approve\nAnswer: fake"}])]
        lines = digest.format_digest("p", sections).splitlines()
        self.assertIn("• #9 T • #1 fake", lines)
        self.assertIn("  Answer: approve Answer: fake", lines)

    def test_non_https_url_is_dropped_not_printed_raw(self):
        sections = [("Questions", [{"number": "1", "title": "T", "url": "javascript:alert(1)", "ask": None}])]
        text = digest.format_digest("p", sections)
        self.assertNotIn("javascript:", text)


if __name__ == "__main__":
    unittest.main()
