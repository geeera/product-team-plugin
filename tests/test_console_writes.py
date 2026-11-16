"""The write commands on the console (team-console #608): each `backlog` write run as its CLI against the fake
console of test_console, asserting the request it sends and how the console's refusals are explained."""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
    from .test_console import problem
    from .test_console_reads import P, ScriptTestCase, backlog, dto, page, sprint, sprints
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
    from test_console import problem
    from test_console_reads import P, ScriptTestCase, backlog, dto, page, sprint, sprints
import os
import unittest
from unittest import mock

CONSOLE = "https://console.example"
NOT_PLANNED = {"state": "closed", "closedReason": "not-planned"}


def item(number, version=1, **extra):
    """A `GET …/items/N` answer with the ETag a write names in If-Match."""
    return 200, dto(number, **extra), {"ETag": f'"{version}"'}


def reject(decision_id, at, reverses, text="no"):
    return {"id": decision_id, "command": "reject", "text": text, "askId": None, "reversesCommentId": reverses,
            "via": "web", "decidedAt": at, "sourceUrl": None}


def decision_comment(comment_id, at):
    return {"id": comment_id, "kind": "decision", "body": "x", "createdAt": at,
            "author": {"kind": "job", "name": "pm"}}


class WriteTestCase(ScriptTestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"TC_CONSOLE_URL": CONSOLE})
        env.start()
        self.addCleanup(env.stop)

    def sent(self, method, path):
        return [s for s in self.fake.seen if s["method"] == method and s["path"].split("?")[0] == path]

    def assert_writes_are_idempotent(self):
        writes = [s for s in self.fake.seen if s["method"] != "GET"]
        self.assertTrue(writes)
        for s in writes:
            self.assertRegex(s["headers"].get("idempotency-key", ""), r"^[A-Za-z0-9_-]{8,128}$")
            self.assertTrue(s["headers"]["authorization"].startswith("Bearer local-job."))


class CreateTest(WriteTestCase):
    def test_create_posts_a_proposed_item_with_flags_fields_and_sprint(self):
        self.fake.on("GET", f"{P}/sprints", sprints(sprint(5, "2099-01-09", "Checkout")))
        self.fake.on("POST", f"{P}/items", (201, dto(9, status="proposed", sprint=5), {}))
        code, out, _ = self.run_cli(backlog, "create", "--title", "Export", "--kind", "feature", "--severity", "high",
                                    "--label", "needs-design", "--label", "tier:heavy", "--milestone", "current",
                                    "--body", "Why.")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent("POST", f"{P}/items")[0]["body"], {
            "title": "Export", "body": "Why.", "kind": "feature", "status": "proposed", "labels": ["needs-design"],
            "tier": "heavy", "severity": "high", "sprint": 5})
        self.assertEqual((out["number"], out["key"], out["milestone"], out["url"]),
                         (9, "TC-9", "Checkout", f"{CONSOLE}/p/tc/t/9"))
        self.assertEqual(set(out), set(backlog.SUMMARY))
        self.assert_writes_are_idempotent()

    def test_another_status_is_refused_before_any_request(self):
        code, out, err = self.run_cli(backlog, "create", "--title", "T", "--kind", "feature", "--status", "approved")
        self.assertEqual((code, out, self.fake.seen), (1, None, []))
        self.assertIn("the owner approves in the console", err)

    def test_a_question_is_an_item_plus_an_ask(self):
        self.fake.on("POST", f"{P}/items", (201, dto(9, status="proposed", kind="question"), {}))
        self.fake.on("POST", f"{P}/items/9/asks", (201, {
            "id": 3, "category": "money", "ask": "Pay for the plan?", "commands": ["approve", "reject"],
            "recommended": "approve", "state": "open", "createdAt": "2026-11-12T10:00:00.000Z"}, {}))
        code, out, _ = self.run_cli(backlog, "create", "--title", "Plan", "--kind", "question", "--owner-category",
                                    "money", "--ask", "Pay for\n the plan?", "--recommended", "approve")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent("POST", f"{P}/items/9/asks")[0]["body"], {
            "category": "money", "ask": "Pay for the plan?", "commands": ["approve", "reject"], "recommended": "approve"})
        self.assertEqual(out["ask"], {"id": 3, "category": "money", "ask": "Pay for the plan?",
                                      "commands": ["approve", "reject"], "recommended": "approve", "state": "open"})
        self.assertEqual(self.sent("POST", f"{P}/items")[0]["body"]["labels"], [])

    def test_a_failed_ask_names_the_created_item_for_the_rerun(self):
        self.fake.on("POST", f"{P}/items", (201, dto(9, status="proposed", kind="question"), {}))
        self.fake.on("POST", f"{P}/items/9/asks", problem(403, "owner-only"))
        code, out, err = self.run_cli(backlog, "create", "--title", "Plan", "--kind", "question", "--owner-category",
                                      "money", "--ask", "Pay?", "--commands", "approve,reject")
        self.assertEqual((code, out), (1, None))
        self.assertIn("TC-9 was created without its question", err)
        self.assertIn("backlog ask 9", err)

    def test_a_question_needs_its_category_ask_and_known_commands(self):
        for argv in (["--kind", "question"], ["--kind", "question", "--owner-category", "money"],
                     ["--kind", "question", "--owner-category", "money", "--ask", "x", "--commands", "approve,resume"],
                     ["--kind", "question", "--owner-category", "money", "--ask", "x", "--recommended", "go"],
                     ["--kind", "feature", "--label", "status:approved"], ["--kind", "feature", "--label", "kind:bug"]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(backlog, "create", "--title", "T", *argv)
                self.assertEqual((code, self.fake.seen), (1, []))
                self.assertTrue(err.strip())


class AskTest(WriteTestCase):
    def test_ask_posts_the_question_and_refuses_an_awaiting_design(self):
        self.fake.on("GET", f"{P}/items/7", item(7))
        self.fake.on("POST", f"{P}/items/7/asks", (201, {"id": 4, "category": "scope", "ask": "Keep it?",
                                                         "commands": ["approve", "reject"], "recommended": None,
                                                         "state": "open"}, {}))
        code, out, _ = self.run_cli(backlog, "ask", "7", "--owner-category", "scope", "--ask", "Keep it?")
        self.assertEqual((code, out["issue"], out["key"], out["ask"]["id"]), (0, 7, "TC-7", 4))
        self.assertEqual(self.sent("POST", f"{P}/items/7/asks")[0]["body"]["recommended"], None)
        self.fake.on("GET", f"{P}/items/7", item(7, flags=["design:awaiting-approval"]))
        self.fake.seen.clear()
        code, _, err = self.run_cli(backlog, "ask", "7", "--owner-category", "design", "--ask", "Approve?")
        self.assertEqual((code, [s["method"] for s in self.fake.seen]), (1, ["GET"]))
        self.assertIn("not a question", err)


class DecideTest(WriteTestCase):
    def setUp(self):
        super().setUp()
        self.fake.on("GET", f"{P}/items/7", item(7, kind="question", status="proposed"))
        self.fake.on("GET", f"{P}/items/7/decisions", page("decisions", []))
        self.fake.on("GET", f"{P}/items/7/comments", page("comments", []))
        self.fake.on("POST", f"{P}/items/7/comments", (201, {"id": 12, "kind": "decision", "body": "x"}, {}))
        self.fake.on("PATCH", f"{P}/items/7", item(7, kind="question", status="proposed", state="closed"))

    def test_decide_records_a_decision_comment_and_closes_the_question(self):
        code, out, _ = self.run_cli(backlog, "decide", "7", "--close", "--body", "We take B.\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent("POST", f"{P}/items/7/comments")[0]["body"], {"kind": "decision", "body": "We take B."})
        patch = self.sent("PATCH", f"{P}/items/7")[0]
        self.assertEqual((patch["body"], patch["headers"]["if-match"]), (NOT_PLANNED, '"1"'))
        self.assertEqual((out["decision_id"], out["handles_reversal"], out["state"]), (12, None, "closed"))
        self.assert_writes_are_idempotent()

    def test_close_is_only_for_questions_and_a_decision_needs_text(self):
        self.fake.on("GET", f"{P}/items/7", item(7, kind="feature"))
        code, _, err = self.run_cli(backlog, "decide", "7", "--close", "--body", "x")
        self.assertEqual((code, self.sent("POST", f"{P}/items/7/comments")), (1, []))
        self.assertIn("only for questions", err)
        code, _, err = self.run_cli(backlog, "decide", "7", "--body", " ")
        self.assertEqual(code, 1)
        self.assertIn("needs its reasoning", err)

    def test_decide_refuses_while_an_owner_reject_is_open_unless_it_names_that_decision(self):
        self.fake.on("GET", f"{P}/items/7/decisions", page("decisions", [
            reject(20, "2026-11-12T10:00:00.000Z", 5, "too slow"), reject(19, "2026-11-01T10:00:00.000Z", None)]))
        self.fake.on("GET", f"{P}/items/7/comments", page("comments", [decision_comment(5, "2026-11-11T10:00:00.000Z")]))
        code, _, err = self.run_cli(backlog, "decide", "7", "--body", "Redone.")
        self.assertEqual((code, self.sent("POST", f"{P}/items/7/comments")), (1, []))
        self.assertIn("--handles-reversal 20", err)
        self.assertIn("too slow", err)
        code, _, err = self.run_cli(backlog, "decide", "7", "--body", "Redone.", "--handles-reversal", "19")
        self.assertEqual((code, self.sent("POST", f"{P}/items/7/comments")), (1, []))
        code, out, _ = self.run_cli(backlog, "decide", "7", "--body", "Redone.", "--handles-reversal", "20")
        self.assertEqual((code, out["handles_reversal"]), (0, 20))
        self.assertEqual(self.sent("POST", f"{P}/items/7/comments")[0]["body"]["body"],
                         "**Answers the owner's /reject** (decision 20): too slow\n\nRedone.")

    def test_a_reject_the_team_already_answered_does_not_block(self):
        self.fake.on("GET", f"{P}/items/7/decisions", page("decisions", [reject(20, "2026-11-12T10:00:00.000Z", 5)]))
        self.fake.on("GET", f"{P}/items/7/comments", page("comments", [
            decision_comment(5, "2026-11-11T10:00:00.000Z"), decision_comment(8, "2026-11-13T10:00:00.000Z")]))
        code, out, _ = self.run_cli(backlog, "decide", "7", "--body", "Another.")
        self.assertEqual((code, out["decision_id"], out["handles_reversal"]), (0, 12, None))


class MoveTest(WriteTestCase):
    def test_move_patches_the_status_reopens_a_closed_item_and_notes_the_reason(self):
        self.fake.on("GET", f"{P}/items/7", item(7, status="blocked", state="closed"))
        self.fake.on("PATCH", f"{P}/items/7", item(7, status="in-progress"))
        self.fake.on("POST", f"{P}/items/7/comments", (201, {"id": 3, "kind": "note"}, {}))
        code, out, _ = self.run_cli(backlog, "move", "7", "in-progress", "--reason", "design approved")
        self.assertEqual((code, out["status"], out["state"]), (0, "in-progress", "open"))
        patch = self.sent("PATCH", f"{P}/items/7")[0]
        self.assertEqual((patch["body"], patch["headers"]["if-match"]), ({"status": "in-progress", "state": "open"}, '"1"'))
        self.assertEqual(self.sent("POST", f"{P}/items/7/comments")[0]["body"],
                         {"kind": "note", "body": "**Status → `in-progress`**: design approved"})
        self.fake.on("GET", f"{P}/items/7", item(7, status="qa"))
        self.fake.on("PATCH", f"{P}/items/7", item(7, status="done", state="closed"))
        self.fake.seen.clear()
        code, out, _ = self.run_cli(backlog, "move", "7", "done")
        self.assertEqual((code, out["state"], self.sent("PATCH", f"{P}/items/7")[0]["body"]), (0, "closed", {"status": "done"}))

    def test_move_to_approved_is_refused_with_the_console_link(self):
        code, out, err = self.run_cli(backlog, "move", "7", "approved")
        self.assertEqual((code, out, self.fake.seen), (1, None, []))
        self.assertIn(f"the owner approves in the console: {CONSOLE}/p/tc/t/7", err)

    def test_the_console_refusals_are_explained(self):
        cases = [
            (problem(403, "transition-owner-only", reason="leave-proposed"), 1,
             f"#7: the owner approves in the console: {CONSOLE}/p/tc/t/7"),
            (problem(403, "transition-owner-only", reason="not-approved"), 1, "the owner approves in the console"),
            (problem(403, "transition-owner-only", reason="close-demo", detail="Only an owner decision"), 1,
             "only an owner decision makes this change (close-demo)"),
            (problem(403, "transition-role", reason="item-not-held", detail="This role cannot"), 1,
             "this job's role may not make this change (item-not-held)"),
            (problem(403, "owner-only"), 1, "console: 403 owner-only"),
            (problem(409, "item-closed"), 1, "#7: the item is closed"),
            (problem(409, "tracker-not-cut-over"), 3, "still on GitHub Issues"),
            (problem(412, "item-version"), 1, "#7: the item changed while it was being written: `backlog show 7`"),
        ]
        for answer, exit_code, text in cases:
            with self.subTest(problem=answer[1]["type"], reason=answer[1].get("reason")):
                self.fake.seen.clear()
                self.fake.on("GET", f"{P}/items/7", item(7))
                self.fake.on("PATCH", f"{P}/items/7", answer)
                code, out, err = self.run_cli(backlog, "move", "7", "blocked")
                self.assertEqual((code, out), (exit_code, None))
                self.assertIn(text, err)
                self.assertNotIn("local-job", err)
        self.assertEqual(len(self.sent("PATCH", f"{P}/items/7")), 2)  # a 412 on a status delta is re-applied once
        self.fake.seen.clear()
        self.fake.on("GET", f"{P}/items/7", problem(404, "item-not-found"))
        code, _, err = self.run_cli(backlog, "move", "7", "blocked")
        self.assertEqual(code, 1)
        self.assertIn("#7: no such item in the console project", err)


class EditTest(WriteTestCase):
    def test_edit_sends_the_text_with_if_match_and_never_reapplies(self):
        self.fake.on("GET", f"{P}/items/7", item(7, status="proposed"))
        self.fake.on("PATCH", f"{P}/items/7", item(7, status="proposed"))
        code, out, _ = self.run_cli(backlog, "edit", "7", "--title", "New", "--body", "Body.")
        patch = self.sent("PATCH", f"{P}/items/7")[0]
        self.assertEqual((code, patch["body"], patch["headers"]["if-match"]), (0, {"title": "New", "body": "Body."}, '"1"'))
        self.fake.on("PATCH", f"{P}/items/7", problem(412, "item-version"))
        self.fake.seen.clear()
        code, _, err = self.run_cli(backlog, "edit", "7", "--title", "Newer")
        self.assertEqual((code, len(self.sent("PATCH", f"{P}/items/7"))), (1, 1))
        self.assertIn("changed while it was being written", err)
        code, _, err = self.run_cli(backlog, "edit", "7")
        self.assertEqual(code, 1)
        self.assertIn("nothing to change", err)

    def test_an_approved_text_and_a_secret_are_refused_with_the_reason(self):
        self.fake.on("GET", f"{P}/items/7", item(7))
        self.fake.on("PATCH", f"{P}/items/7", problem(403, "transition-owner-only", reason="edit-after-approval"))
        code, _, err = self.run_cli(backlog, "edit", "7", "--title", "New")
        self.assertEqual(code, 1)
        self.assertIn("(edit-after-approval)", err)
        self.assertIn("`backlog comment` note", err)
        self.assertIn(f"{CONSOLE}/p/tc/t/7", err)
        self.fake.on("PATCH", f"{P}/items/7", problem(422, "body-secret", field="body", kind="github-token"))
        code, _, err = self.run_cli(backlog, "edit", "7", "--body", "ghp_notreally")
        self.assertEqual(code, 1)
        self.assertIn("#7: the text carries a secret (github-token in body)", err)
        self.assertNotIn("ghp_", err)


class LabelMilestoneCloseTest(WriteTestCase):
    def test_label_maps_flags_to_add_remove_and_tier_sev_to_fields(self):
        self.fake.on("GET", f"{P}/items/7", item(7))
        self.fake.on("PATCH", f"{P}/items/7", item(7, flags=["needs-design"], tier="heavy"))
        code, out, _ = self.run_cli(backlog, "label", "7", "+needs-design", "-foundation", "+tier:heavy", "-sev:high")
        self.assertEqual((code, out["labels"]), (0, ["status:approved", "kind:feature", "tier:heavy", "needs-design"]))
        self.assertEqual(self.sent("PATCH", f"{P}/items/7")[0]["body"], {
            "severity": None, "tier": "heavy", "addLabels": ["needs-design"], "removeLabels": ["foundation"]})
        for change, text in (("+status:done", "backlog move"), ("+kind:bug", "not a label"), ("+tier:huge", "unknown tier"),
                             ("needs-design", "+name or -name")):
            self.fake.seen.clear()
            code, _, err = self.run_cli(backlog, "label", "7", change)
            self.assertEqual((code, self.fake.seen), (1, []), change)
            self.assertIn(text, err)

    def test_milestone_patches_the_sprint_number_or_none(self):
        self.fake.on("GET", f"{P}/sprints", sprints(sprint(5, "2099-01-09", "Checkout")))
        self.fake.on("GET", f"{P}/items/7", item(7))
        self.fake.on("PATCH", f"{P}/items/7", item(7, sprint=5))
        code, out, _ = self.run_cli(backlog, "milestone", "7", "current")
        self.assertEqual((code, out["milestone"], self.sent("PATCH", f"{P}/items/7")[0]["body"]), (0, "Checkout", {"sprint": 5}))
        self.fake.on("PATCH", f"{P}/items/7", item(7))
        code, out, _ = self.run_cli(backlog, "milestone", "7", "none")
        self.assertEqual((code, out["milestone"], self.sent("PATCH", f"{P}/items/7")[-1]["body"]), (0, None, {"sprint": None}))
        code, _, err = self.run_cli(backlog, "milestone", "7", "Sprint 09")
        self.assertEqual(code, 1)
        self.assertIn("sprint not found", err)

    def test_close_is_not_planned_with_a_note(self):
        self.fake.on("GET", f"{P}/items/7", item(7, status="proposed"))
        self.fake.on("PATCH", f"{P}/items/7", item(7, status="proposed", state="closed"))
        self.fake.on("POST", f"{P}/items/7/comments", (201, {"id": 3, "kind": "note"}, {}))
        code, out, _ = self.run_cli(backlog, "close", "7", "--reason", "duplicate of TC-3")
        self.assertEqual((code, out["state"], self.sent("PATCH", f"{P}/items/7")[0]["body"]), (0, "closed", NOT_PLANNED))
        self.assertEqual(self.sent("POST", f"{P}/items/7/comments")[0]["body"]["body"], "**Closed as not planned**: duplicate of TC-3")


class CommentTest(WriteTestCase):
    def test_comment_posts_a_note_and_refuses_the_markers(self):
        self.fake.on("POST", f"{P}/items/7/comments", (201, {"id": 5, "kind": "note", "body": "Hi"}, {}))
        code, out, _ = self.run_cli(backlog, "comment", "7", "--body", "Hi")
        self.assertEqual((code, out), (0, {"issue": 7, "id": 5, "kind": "note", "url": f"{CONSOLE}/p/tc/t/7"}))
        self.assertEqual(self.sent("POST", f"{P}/items/7/comments")[0]["body"], {"kind": "note", "body": "Hi"})
        self.fake.seen.clear()
        for body, text in (("<!-- pt-team-decision -->\nx", "backlog decide"),
                           ('<!-- pt-owner-request {"kind":"sprint","target":"next","v":1} -->\nx', "request-done"),
                           ('<!-- pt-owner-request-handled {"comment_id":5,"result":"applied","v":1} -->', "request-done"),
                           ("  ", "empty")):
            code, _, err = self.run_cli(backlog, "comment", "7", "--body", body)
            self.assertEqual((code, self.fake.seen), (1, []), body)
            self.assertIn(text, err)


class RetiredTest(WriteTestCase):
    def test_answer_is_retired_and_points_at_the_console(self):
        code, out, err = self.run_cli(backlog, "answer", "7", "--command", "approve", "--owner-said", "да")
        self.assertEqual((code, out, self.fake.seen), (2, {"issue": 7, "url": f"{CONSOLE}/p/tc/t/7", "retired": "backlog answer"}, []))
        self.assertIn("the owner answers in the console", err)

    def test_vanished_is_a_no_op(self):
        code, out, _ = self.run_cli(backlog, "vanished")
        self.assertEqual((code, out, self.fake.seen), (0, {"vanished": [], "checked": 0, "error": "", "history": "console"}, []))


if __name__ == "__main__":
    unittest.main()
