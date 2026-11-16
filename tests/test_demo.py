try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import demo  # noqa: E402


DATA = {
    "product": "p", "repo": "o/r", "sprint": "Sprint 01", "demo_date": "2026-10-09",
    "demo_issue": {"number": 1, "url": "u"}, "release": {"recommendation": "go", "blockers": []},
    "shipped": [{"id": "issue-2", "number": 2, "title": "</script><b>x</b>"}], "proposals": [], "findings": [],
}


def extract(html):
    return json.loads(html.split(demo.START, 1)[1].split(demo.END, 1)[0])


class FillTest(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "templates" / "demo-page.html").read_text(encoding="utf-8")

    def test_replaces_example_data_and_clears_example_flag(self):
        html = demo.fill(self.template, DATA)
        data = extract(html)
        self.assertFalse(data["example"])
        self.assertEqual(data["sprint"], "Sprint 01")

    def test_escapes_closing_script_tags(self):
        html = demo.fill(self.template, DATA)
        payload = html.split(demo.START, 1)[1].split(demo.END, 1)[0]
        self.assertNotIn("</script", payload)
        self.assertEqual(extract(html)["shipped"][0]["title"], "</script><b>x</b>")

    def test_rejects_incomplete_data(self):
        with self.assertRaises(ValueError):
            demo.fill(self.template, {"product": "p"})


def row(number, command, at, text=""):
    """A row of the console's `GET …/decisions` feed (decisions.list fixture shape)."""
    return {"id": 1, "command": command, "text": text, "askId": None, "reversesCommentId": None, "via": "web",
            "decidedAt": at, "sourceUrl": None, "item": {"key": f"TC-{number}", "number": number}}


class DecisionsTest(unittest.TestCase):
    def test_the_newest_release_and_proposal_decisions_win(self):
        rows = [row(5, "no-go", "2026-11-12T10:00:00.000Z", "blocker"), row(5, "go", "2026-11-12T11:00:00.000Z"),
                row(5, "approve", "2026-11-12T12:00:00.000Z"),  # not a release command: ignored on the demo item
                row(21, "approve", "2026-11-12T10:00:00.000Z"), row(21, "reject", "2026-11-12T10:30:00.000Z", "later"),
                row(22, "done", "2026-11-12T10:00:00.000Z"), {"command": "go", "item": None}]
        found = demo.decisions_from_rows(5, rows, "https://console.example/p/tc/t/5")
        self.assertEqual(found, {
            "release": {"command": "go", "text": "", "at": "2026-11-12T11:00:00.000Z"},
            "decisions": {"release": {"decision": "go", "comment": ""},
                          "issue-21": {"decision": "reject", "comment": "later", "key": "TC-21"}},
            "source": "https://console.example/p/tc/t/5"})

    def test_no_decision_is_null(self):
        self.assertIsNone(demo.decisions_from_rows(5, [row(22, "done", "2026-11-12T10:00:00.000Z")], "console"))
        found = demo.decisions_from_rows(5, [row(21, "approve", "2026-11-12T10:00:00.000Z")], "console")
        self.assertIsNone(found["release"])

    def test_normalises_db_rows(self):
        rows = [{"id": "issue-21", "data": {"item": "issue-21", "decision": "approve", "comment": "yes"}}]
        self.assertEqual(demo.normalise_db_rows(rows)["decisions"]["issue-21"], {"decision": "approve", "comment": "yes"})


if __name__ == "__main__":
    unittest.main()
