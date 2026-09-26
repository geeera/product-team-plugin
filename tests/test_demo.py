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


class DecisionsTest(unittest.TestCase):
    def block(self, decisions):
        return "/demo-decisions\n```json\n" + json.dumps({"sprint": "Sprint 01", "decisions": decisions}) + "\n```"

    def test_takes_latest_owner_block(self):
        comments = [
            {"user": {"login": "geeera"}, "created_at": "2026-10-09T10:00:00Z", "body": self.block({"release": {"decision": "no-go"}})},
            {"user": {"login": "geeera"}, "created_at": "2026-10-09T11:00:00Z", "body": self.block({"release": {"decision": "go"}})},
            {"user": {"login": "intruder"}, "created_at": "2026-10-09T12:00:00Z", "body": self.block({"release": {"decision": "no-go"}})},
        ]
        found = demo.decisions_from_comments(comments, "Geeera")
        self.assertEqual(found["decisions"]["release"]["decision"], "go")

    def test_ignores_malformed_json(self):
        comments = [{"user": {"login": "geeera"}, "created_at": "t", "body": "/demo-decisions\n```json\n{oops\n```"}]
        self.assertIsNone(demo.decisions_from_comments(comments, "geeera"))

    def test_normalises_db_rows(self):
        rows = [{"id": "issue-21", "data": {"item": "issue-21", "decision": "approve", "comment": "yes"}}]
        self.assertEqual(demo.normalise_db_rows(rows)["decisions"]["issue-21"], {"decision": "approve", "comment": "yes"})


if __name__ == "__main__":
    unittest.main()
