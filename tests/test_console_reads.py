"""The read commands on the console (team-console #607): backlog reads, slot-context and sprint-metrics, each run
as its CLI against the fake console of test_console, which serves the vendored contract fixtures."""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
    from .test_console import ConsoleTestCase, problem
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
    from test_console import ConsoleTestCase, problem
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import console  # noqa: E402

FIXTURES = ROOT / "tests" / "contract" / "fixtures" / "tracker"
P = "/api/v1/projects/tc"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_") + "_cli", str(ROOT / "scripts" / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


backlog, slot_context, sprint_metrics = load("backlog"), load("slot-context"), load("sprint-metrics")


def fixture_body(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["response"]["body"]


ITEM = fixture_body("items.list")["items"][0]


def dto(number, status="approved", kind="feature", sprint=None, flags=(), state="open", tier=None, **extra):
    row = copy.deepcopy(ITEM)
    row.update(key=f"TC-{number}", number=number, status=status, kind=kind, sprint=sprint, flags=list(flags),
               state=state, tier=tier, title=f"Item {number}",
               labels=[f"status:{status}", f"kind:{kind}", *([f"tier:{tier}"] if tier else []), *flags], **extra)
    return row


def page(member, rows):
    return 200, {member: rows, "next": None}, {}


def sprint(number, due, title=None, state="open"):
    return {"number": number, "title": title or f"Sprint {number:02d}", "dueOn": due, "state": state,
            "description": "", "createdAt": "2026-11-01T10:00:00.000Z", "closedAt": None, "openCount": 0}


def sprints(*rows):
    return 200, {"sprints": list(rows), "backlogOpenCount": 0}, {}


class ScriptTestCase(ConsoleTestCase):
    def run_cli(self, module, *argv):
        """(exit code, parsed stdout or None, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", [module.__name__, *argv]), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            try:
                module.main()
            except SystemExit as exc:
                code = exc.code
        if isinstance(code, str):
            stderr.write(code)
            code = 1
        text = stdout.getvalue()
        return code or 0, json.loads(text) if text.strip() else None, stderr.getvalue()

    def paths(self):
        return [urllib.parse.unquote(s["path"]) for s in self.fake.seen]


class AsIssueTest(ScriptTestCase):
    def test_the_items_list_keeps_the_console_order_as_queue(self):
        self.fake.load_fixtures()
        found = console.issues(state="open")
        self.assertEqual([(i["number"], i["key"], i["queue"]) for i in found], [(1, "TC-1", 0), (2, "TC-2", 1)])
        self.assertEqual(found[1], {
            "number": 2, "key": "TC-2", "title": "Dark mode", "url": None,
            "labels": ["status:approved", "kind:feature"], "kind": "feature", "status": "approved",
            "state": "open", "milestone": None, "sprint": None, "closed_at": None,
            "created_at": "2026-11-12T10:00:00.000Z", "updated_at": "2026-11-12T10:00:00.000Z", "ask": None,
            "queue": 1})

    def test_the_milestone_is_the_sprint_title_and_the_url_the_console_page(self):
        with mock.patch.dict(os.environ, {"TC_CONSOLE_URL": "https://console.example"}):
            issue = console.as_issue(dto(7, sprint=3), {3: "Checkout"})
        self.assertEqual((issue["milestone"], issue["url"]), ("Checkout", "https://console.example/p/tc/t/7"))
        self.assertEqual(console.as_issue(dto(7, sprint=3))["milestone"], "Sprint 03")
        self.assertNotIn("queue", console.as_issue(dto(7)))

    def test_an_item_without_number_or_labels_is_refused(self):
        for broken in ({**dto(1), "number": "1"}, {**dto(1), "labels": "kind:bug"}, {**dto(1), "title": None}):
            with self.subTest(broken=broken), self.assertRaises(console.ConsoleError):
                console.as_issue(broken)


class BacklogReadTest(ScriptTestCase):
    def test_list_filters_on_the_console_and_intersects_further_labels_here(self):
        self.fake.on("GET", f"{P}/sprints", sprints(sprint(5, "2099-01-09")))
        self.fake.on("GET", f"{P}/items?state=open&status=approved&label=foundation&sprint=5",
                     page("items", [dto(3, flags=["foundation"], sprint=5),
                                    dto(4, flags=["foundation", "needs-design"], sprint=5)]))
        code, out, _ = self.run_cli(backlog, "list", "--status", "approved", "--label", "foundation",
                                    "--label", "needs-design", "--milestone", "Sprint 05")
        self.assertEqual(code, 0)
        self.assertEqual([(i["number"], i["key"], i["milestone"]) for i in out], [(4, "TC-4", "Sprint 05")])
        self.assertEqual(set(out[0]), set(backlog.SUMMARY))

    def test_show_reads_the_item_its_comments_asks_and_decisions(self):
        self.fake.load_fixtures()
        self.fake.on("GET", f"{P}/items/1/decisions", page("decisions", [{
            "id": 4, "command": "approve", "text": "", "askId": 1, "reversesCommentId": None, "via": "web",
            "decidedAt": "2026-11-12T11:00:00.000Z", "sourceUrl": None}]))
        code, out, _ = self.run_cli(backlog, "show", "1")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.split("?")[0] for p in self.paths()),
                         sorted([f"{P}/items/1", f"{P}/items/1/asks", f"{P}/sprints", f"{P}/items/1/comments",
                                 f"{P}/items/1/decisions"]))
        self.assertEqual((out["number"], out["title"], out["body"]), (1, "Export to CSV", ""))
        self.assertEqual(out["comments"], [{"id": 1, "kind": "comment", "author": "owner", "author_kind": "owner",
                                            "at": "2026-11-12T10:00:00.000Z", "body": "Which format?"}])
        self.assertEqual([(a["category"], a["commands"], a["state"]) for a in out["asks"]],
                         [("scope", ["approve", "reject"], "open")])
        self.assertEqual([(d["command"], d["ask_id"], d["via"]) for d in out["decisions"]], [("approve", 1, "web")])

    def test_answers_are_the_owners_decision_rows_with_nothing_ignored(self):
        rows = [{"id": 2, "command": "done", "text": "created", "askId": None, "reversesCommentId": None,
                 "via": "web", "decidedAt": "2026-11-12T12:00:00.000Z", "sourceUrl": None},
                {"id": 1, "command": "reject", "text": "too broad", "askId": 1, "reversesCommentId": None,
                 "via": "telegram", "decidedAt": "2026-11-12T10:00:00.000Z", "sourceUrl": None}]
        self.fake.on("GET", f"{P}/items/7/decisions", page("decisions", rows))
        code, out, _ = self.run_cli(backlog, "answers", "7")
        self.assertEqual(code, 0)
        self.assertEqual([(c["command"], c["text"], c["via"]) for c in out["commands"]],
                         [("reject", "too broad", "telegram"), ("done", "created", "web")])
        self.assertEqual((out["ignored"], out["same_account"], [d["id"] for d in out["done"]]), ([], False, [2]))

    def test_reversals_are_rejects_of_a_team_decision_no_later_decision_answered(self):
        def reject(item, at, reverses):
            return {"id": item * 10, "command": "reject", "text": f"no {item}", "askId": None,
                    "reversesCommentId": reverses, "via": "web", "decidedAt": at, "sourceUrl": None,
                    "item": {"key": f"TC-{item}", "number": item}}
        self.fake.on("GET", f"{P}/decisions?since=2026-11-01T00:00:00Z&command=reject", page("decisions", [
            reject(2, "2026-11-12T10:00:00.000Z", 5), reject(3, "2026-11-10T10:00:00.000Z", 6),
            reject(4, "2026-11-12T10:00:00.000Z", None)]))
        self.fake.on("GET", f"{P}/sprints", sprints())
        self.fake.on("GET", f"{P}/items/2/comments", page("comments", [
            {"id": 5, "kind": "decision", "body": "x", "createdAt": "2026-11-11T10:00:00.000Z"}]))
        self.fake.on("GET", f"{P}/items/3/comments", page("comments", [
            {"id": 6, "kind": "decision", "body": "x", "createdAt": "2026-11-09T10:00:00.000Z"},
            {"id": 8, "kind": "decision", "body": "redone", "createdAt": "2026-11-11T10:00:00.000Z"}]))
        self.fake.on("GET", f"{P}/items/2", (200, dto(2), {}))
        with mock.patch.object(backlog, "days_ago", return_value=datetime(2026, 11, 1, tzinfo=timezone.utc)):
            code, out, _ = self.run_cli(backlog, "reversals")
        self.assertEqual(code, 0)
        self.assertEqual([(r["number"], r["reason"], r["decision_id"], r["reverses_comment_id"]) for r in out],
                         [(2, "no 2", 20, 5)])
        self.assertNotIn(f"{P}/items/4/comments", self.paths())

    def test_requests_always_name_the_state_and_one_on_a_closed_item_is_ignored(self):
        def request(rid, number, at, req=None):
            return {"id": rid, "request": req or {"kind": "sprint", "target": "next"}, "requestedAt": at,
                    "result": None, "reason": None, "handledAt": None,
                    "item": {"key": f"TC-{number}", "number": number, "title": f"Item {number}", "state": "open"}}
        self.fake.on("GET", f"{P}/requests?state=pending", page("requests", [
            request(1, 1, "2026-11-12T10:00:00.000Z"), request(2, 7, "2026-11-12T10:00:00.000Z"),
            request(3, 2, "2026-10-01T10:00:00.000Z"),
            request(4, 2, "2026-11-12T10:00:00.000Z", {"kind": "priority", "direction": "up"})]))
        self.fake.on("GET", f"{P}/sprints", sprints(sprint(5, "2099-01-09")))
        self.fake.on("GET", f"{P}/items?state=open", page("items", [dto(1, sprint=5), dto(2)]))
        with mock.patch.object(backlog, "days_ago", return_value=datetime(2026, 11, 1, tzinfo=timezone.utc)):
            code, out, _ = self.run_cli(backlog, "requests")
            _, current, _ = self.run_cli(backlog, "requests", "--milestone", "current")
        self.assertEqual(code, 0)
        self.assertEqual([(r["issue"], r["request_id"], r["kind"], r.get("target"), r.get("direction"),
                           r["milestone"]) for r in out["requests"]],
                         [(1, 1, "sprint", "next", None, "Sprint 05"), (2, 4, "priority", None, "up", None)])
        self.assertEqual([(i["issue"], i["reason"]) for i in out["ignored"]], [(7, "the item is closed")])
        self.assertEqual([r["issue"] for r in current["requests"]], [1])
        self.assertTrue(all("state=" in p for p in self.paths() if "/requests" in p))

    def test_next_dispatches_in_the_console_queue_order(self):
        self.fake.on("GET", f"{P}/sprints", sprints(sprint(4, "2026-11-13", state="closed"),
                                                    sprint(5, "2026-11-27", "Checkout")))
        self.fake.on("GET", f"{P}/items?state=open", page("items", [
            dto(9, sprint=5), dto(2, sprint=5), dto(1, sprint=5), dto(3, sprint=4), dto(6, status="proposed")]))
        code, out, _ = self.run_cli(backlog, "next", "--now", "2026-11-17T10:00:00+02:00")
        self.assertEqual(code, 0)
        self.assertEqual((out["context"]["sprint"], out["context"]["mode"]), ("Checkout", "normal"))
        self.assertEqual([d["number"] for d in out["dispatch"]], [9, 2])
        self.assertEqual(out["skipped"], [{"number": 1, "reason": "over the cap for this run"}])

    def test_sprint_current_and_list(self):
        self.fake.on("GET", f"{P}/sprints", sprints(sprint(4, "2020-01-03", state="closed"),
                                                    sprint(5, "2099-01-09", "Checkout")))
        _, current, _ = self.run_cli(backlog, "sprint", "current")
        _, listed, _ = self.run_cli(backlog, "sprint", "list")
        self.assertEqual(current, {"title": "Checkout", "number": 5, "due": "2099-01-09", "url": None})
        self.assertEqual([(s["title"], s["state"], s["due"]) for s in listed],
                         [("Sprint 04", "closed", "2020-01-03"), ("Checkout", "open", "2099-01-09")])

    def test_init_only_checks_the_labels_and_fails_naming_missing_ones(self):
        self.fake.load_fixtures()
        code, out, _ = self.run_cli(backlog, "init")
        self.assertEqual((code, out["missing"], out["created"]), (0, [], []))
        labels = fixture_body("labels.list")["labels"]
        self.fake.on("GET", f"{P}/labels", (200, {"labels": [l for l in labels if l["name"] != "foundation"]}, {}))
        code, out, err = self.run_cli(backlog, "init")
        self.assertEqual((code, out["missing"]), (1, ["foundation"]))
        self.assertIn("the owner adds them", err)
        self.assertEqual({s["method"] for s in self.fake.seen}, {"GET"})

    def test_a_project_still_on_github_exits_3(self):
        self.fake.on("GET", f"{P}/sprints", problem(409, "tracker-not-cut-over"))
        code, out, err = self.run_cli(backlog, "list")
        self.assertEqual((code, out), (3, None))
        self.assertIn("still on GitHub Issues", err)


class SlotContextTest(ScriptTestCase):
    def test_the_current_sprint_comes_from_the_open_sprints(self):
        self.fake.on("GET", f"{P}/sprints?state=open", sprints(sprint(5, "2026-11-19", "Checkout")))
        code, out, _ = self.run_cli(slot_context, "--now", "2026-11-18T10:00:00+02:00")
        self.assertEqual((code, out["sprint"], out["demo_date"], out["mode"]), (0, "Checkout", "2026-11-19", "freeze"))

    def test_an_unreadable_console_points_at_offline(self):
        self.fake.on("GET", f"{P}/sprints?state=open", problem(403, "owner-only"))
        code, out, err = self.run_cli(slot_context)
        self.assertEqual((code, out), (1, None))
        self.assertIn("--offline", err)


class SprintMetricsTest(ScriptTestCase):
    def test_plan_shipped_and_cycle_time_from_items_and_status_events(self):
        self.fake.on("GET", f"{P}/sprints?state=all", sprints(sprint(5, "2026-11-19", "Checkout")))
        self.fake.on("GET", f"{P}/items?sprint=5&state=all", page("items", [
            dto(1, status="done", sprint=5, state="closed", tier="light", closedAt="2026-11-14T00:00:00.000Z"),
            dto(2, status="in-progress", sprint=5), dto(3, kind="question", sprint=5)]))
        self.fake.on("GET", f"{P}/items/1/events", page("events", [
            {"id": 1, "at": "2026-11-12T00:00:00.000Z", "kind": "status", "before": {"status": "approved"},
             "after": {"status": "in-progress"}}]))
        with mock.patch.object(sprint_metrics.gh, "repo", return_value="o/r"), \
                mock.patch.object(sprint_metrics.gh, "api_list", return_value=[]):
            code, out, _ = self.run_cli(sprint_metrics, "--milestone", "Checkout")
        self.assertEqual(code, 0)
        self.assertEqual((out["sprint"], out["demo"], out["planned"], out["shipped"], out["carried_over"]),
                         ("Checkout", "2026-11-19", 2, 1, 1))
        self.assertEqual((out["median_cycle_days"], out["qa"]["source"]), (2.0, "github-prs"))
        self.assertEqual(out["by_tier"]["light"], {"planned": 1, "shipped": 1, "raised": 0})


if __name__ == "__main__":
    unittest.main()
