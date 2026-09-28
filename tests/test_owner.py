import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import owner  # noqa: E402


class PolicyTest(unittest.TestCase):
    def test_owner_categories_only(self):
        with self.assertRaises(ValueError) as caught:
            owner.question_body("/approve to name it com.storify.reader", "…", "technical")
        self.assertIn("backlog decide", str(caught.exception))

    def test_ask_must_name_the_answering_command(self):
        with self.assertRaises(ValueError):
            owner.question_body("Which bucket layout do you prefer?", "…", "access")

    def test_ask_line_comes_first_and_can_be_read_back(self):
        body = owner.question_body("/approve to create the Cloudflare account · /reject why", "Details.", "access")
        self.assertTrue(body.startswith("**Your answer:** /approve to create"))
        self.assertEqual(owner.ask_of(body), "/approve to create the Cloudflare account · /reject why")

    def test_ask_in_the_owner_language(self):
        body = owner.question_body("/go после демо · /no-go причина", "…", "release", "ru")
        self.assertTrue(body.startswith("**Твой ответ:**"))
        self.assertEqual(owner.ask_of(body), "/go после демо · /no-go причина")

    def test_decision_comment_says_how_to_reverse(self):
        self.assertIn("/reject", owner.decision_comment("Bundle id com.geeera.storify"))


class ReversalTest(unittest.TestCase):
    def c(self, login, body, at):
        return {"user": {"login": login}, "body": body, "created_at": at}

    def test_owner_reject_after_the_decision_reverses_it(self):
        comments = [self.c("geeera", owner.decision_comment("Bundle id com.x"), "2026-09-28T10:00:00Z"),
                    self.c("geeera", "/reject use com.geeera.storify", "2026-09-28T12:00:00Z")]
        self.assertEqual(owner.reversed_by_owner(comments, "geeera")["text"], "use com.geeera.storify")

    def test_reject_before_the_decision_does_not(self):
        comments = [self.c("geeera", "/reject old", "2026-09-27T10:00:00Z"),
                    self.c("geeera", owner.decision_comment("x"), "2026-09-28T10:00:00Z")]
        self.assertEqual(owner.reversed_by_owner(comments, "geeera"), {})

    def test_no_decision_no_reversal(self):
        self.assertEqual(owner.reversed_by_owner([self.c("geeera", "/reject x", "2026-09-28T10:00:00Z")], "geeera"), {})


if __name__ == "__main__":
    unittest.main()
