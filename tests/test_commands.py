try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import commands  # noqa: E402


NO_EDITS = {"issue": None, "comments": {}}  # GitHub knows of no edits to any of these comments


def comment(login, body, at="2026-09-26T10:00:00Z", cid=1):
    return {"user": {"login": login}, "body": body, "created_at": at, "updated_at": at, "id": cid}


def names(body):
    return [f["command"] for f in commands.parse([comment("geeera", body)], "geeera", NO_EDITS)]


class QuotedCommandTest(unittest.TestCase):
    """Only the owner's own prose counts: text quoted, fenced or hidden in a comment is not a decision.

    From QA in geeera/team-console: in same-account mode agent notes posted as the owner quoted `/approve`.
    """

    def test_a_command_on_its_own_line_counts_with_its_text(self):
        found = commands.parse([comment("geeera", "Thanks!\n\n  /reject: use `Inter` instead")], "geeera", NO_EDITS)
        self.assertEqual([(f["command"], f["text"]) for f in found], [("reject", "use `Inter` instead")])

    def test_fenced_code_blocks_do_not_count(self):
        self.assertEqual(names("Answer with:\n```\n/approve\n```\n"), [])
        self.assertEqual(names("~~~text\n/go\n~~~"), [])
        self.assertEqual(names("````md\n```\n/go\n```\n````"), [])  # a longer fence holds shorter ones
        self.assertEqual(names("```\n/approve\n```\n/reject too loud"), ["reject"])

    def test_an_unclosed_fence_hides_the_rest(self):
        self.assertEqual(names("```\n/approve"), [])

    def test_inline_code_does_not_count(self):
        self.assertEqual(names("`/approve`"), [])
        self.assertEqual(names("``/go``, or `/no-go why`"), [])
        self.assertEqual(names("Reply `/approve\nor /reject`"), [])  # a code span may wrap a line

    def test_blockquotes_do_not_count(self):
        self.assertEqual(names("> /approve\n\nI agree with the above"), [])
        self.assertEqual(names("   > /go"), [])
        self.assertEqual(names("> quoted\n\n/go"), ["go"])

    def test_html_comments_do_not_count(self):
        self.assertEqual(names("<!--\n/approve\n-->"), [])
        self.assertEqual(names("<!-- hidden --> /approve"), [])
        self.assertEqual(names("<!-- never closed\n/go"), [])
        self.assertEqual(names("<!-- note -->\n/go"), ["go"])

    def test_indented_code_does_not_count(self):
        self.assertEqual(names("    /approve"), [])
        self.assertEqual(names("\t/approve"), [])

    def test_a_command_must_be_the_first_token(self):
        self.assertEqual(names("- /approve"), [])
        self.assertEqual(names("**/approve**"), [])
        self.assertEqual(names("I /approve"), [])

    def test_team_notes_do_not_count_even_when_posted_as_the_owner(self):
        for body in ("**Architect note** — layout\n/approve is not needed here",
                     "**Architect note:** split the API\n\n/approve",
                     "**PM grooming** (sprint 3)\n/go",
                     "## **UX spec**\n/approve",
                     "**Security** threat model\n/approve",
                     "<!-- pt-team-decision -->\n**Decided by the team**\n/approve",
                     "<!-- pt-run id=1 slot=slot-pm state=started -->\n/resume"):
            with self.subTest(body=body[:30]):
                self.assertEqual(names(body), [])
                self.assertEqual(commands.rejected([comment("geeera", body)], "geeera", None), [])

    def test_a_bold_word_that_is_not_a_role_header_does_not_hide_a_command(self):
        self.assertEqual(names("**Great work.**\n/approve"), ["approve"])

    def test_answers_the_team_chat_writes_still_count(self):
        body = "/approve \n\n_Answered by the owner in the team chat: «ok, go ahead»_\n"
        self.assertEqual(names(body), ["approve"])


class ParseTest(unittest.TestCase):
    def test_parses_owner_commands_with_text(self):
        found = commands.parse([comment("Geeera", "Looks good\n/reject: colours too loud")], "geeera", NO_EDITS)
        self.assertEqual(found[0]["command"], "reject")
        self.assertEqual(found[0]["text"], "colours too loud")

    def test_ignores_commands_from_others(self):
        self.assertEqual(commands.parse([comment("someone", "/approve")], "geeera", NO_EDITS), [])

    def test_no_go_is_not_read_as_go(self):
        found = commands.parse([comment("geeera", "/no-go blocker #12 still open")], "geeera", NO_EDITS)
        self.assertEqual([f["command"] for f in found], ["no-go"])

    def test_command_must_start_a_line(self):
        self.assertEqual(commands.parse([comment("geeera", "please do not /approve yet")], "geeera", NO_EDITS), [])

    def test_a_command_with_trailing_words_that_are_not_a_command_is_not_read(self):
        self.assertEqual(names("/go-live is tomorrow\n/approved it yesterday"), [])

    def test_latest_respects_since(self):
        found = commands.parse([
            comment("geeera", "/approve", "2026-09-26T08:00:00Z", 1),
            comment("geeera", "/reject nope", "2026-09-26T09:00:00Z", 2),
        ], "geeera", NO_EDITS)
        self.assertEqual(commands.latest(found, ["approve", "reject"])["command"], "reject")
        self.assertEqual(commands.latest(found, ["approve", "reject"], since="2026-09-26T09:30:00Z"), {})


if __name__ == "__main__":
    unittest.main()
