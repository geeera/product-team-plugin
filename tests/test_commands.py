try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import commands  # noqa: E402


def comment(login, body, at="2026-09-26T10:00:00Z", cid=1):
    return {"user": {"login": login}, "body": body, "created_at": at, "id": cid}


class ParseTest(unittest.TestCase):
    def test_parses_owner_commands_with_text(self):
        found = commands.parse([comment("Geeera", "Looks good\n/reject: colours too loud")], "geeera")
        self.assertEqual(found[0]["command"], "reject")
        self.assertEqual(found[0]["text"], "colours too loud")

    def test_ignores_commands_from_others(self):
        self.assertEqual(commands.parse([comment("someone", "/approve")], "geeera"), [])

    def test_no_go_is_not_read_as_go(self):
        found = commands.parse([comment("geeera", "/no-go blocker #12 still open")], "geeera")
        self.assertEqual([f["command"] for f in found], ["no-go"])

    def test_command_must_start_a_line(self):
        self.assertEqual(commands.parse([comment("geeera", "please do not /approve yet")], "geeera"), [])

    def test_latest_respects_since(self):
        found = commands.parse([
            comment("geeera", "/approve", "2026-09-26T08:00:00Z", 1),
            comment("geeera", "/reject nope", "2026-09-26T09:00:00Z", 2),
        ], "geeera")
        self.assertEqual(commands.latest(found, ["approve", "reject"])["command"], "reject")
        self.assertEqual(commands.latest(found, ["approve", "reject"], since="2026-09-26T09:30:00Z"), {})


if __name__ == "__main__":
    unittest.main()
