"""The owner-facing scripts and the PR gate on the console (team-console #609): `inbox`, `digest`, `demo-page
decisions`, `pr security-check` / `pr gate` and `pr link-pr`, each run against the fake console of test_console."""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
    from .test_console import as_job, problem, stand_in
    from .test_console_reads import P, ScriptTestCase, dto, load
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
    from test_console import as_job, problem, stand_in
    from test_console_reads import P, ScriptTestCase, dto, load
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

inbox, digest, demo_page, pr = load("inbox"), load("digest"), load("demo-page"), load("pr")
CONSOLE = "https://console.example"
PULL = {"number": 57, "html_url": "https://github.com/acme/tc/pull/57", "title": "Login", "body": "",
        "head": {"ref": "feature/tc-1-login"}, "labels": []}


def inbox_row(number, section, ask=None):
    return {"section": section, "number": number, "key": f"TC-{number}", "askId": None, "title": f"Item {number}",
            "url": None, "ask": ask, "authorTrusted": True, "category": None, "recommendation": None, "context": None}


def inbox_dto(*rows, setup=False, paused=False):
    return 200, {"items": list(rows), "setup": setup, "setupUrl": "https://x/checklist" if setup else None,
                 "paused": paused, "pausedUrl": "https://x/log" if paused else None}, {}


def text_of(_case, module, *argv):
    """(exit code, raw stdout, stderr) of a script that prints text, not JSON."""
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", [module.__name__, *argv]), redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            module.main()
        except SystemExit as exc:
            code = exc.code
    if isinstance(code, str):
        stderr.write(code)
        code = 1
    return code or 0, stdout.getvalue(), stderr.getvalue()


class InboxTest(ScriptTestCase):
    def test_show_renders_the_console_inbox_with_console_links(self):
        self.fake.load_fixtures()
        with mock.patch.dict(os.environ, {"TC_CONSOLE_URL": CONSOLE}):
            code, body, _ = text_of(self, inbox, "show")
        self.assertEqual(code, 0)
        self.assertIn("**1 thing needs you.**", body)
        self.assertIn("_Answer each one on its item in the console_", body)
        self.assertIn(f"- #1 [Ship the beta?]({CONSOLE}/p/tc/t/1) — Ship the beta to the first ten users?", body)
        self.assertEqual(self.paths(), [f"{P}/inbox"])

    def test_without_a_console_url_an_item_is_listed_unlinked(self):
        self.fake.on("GET", f"{P}/inbox", inbox_dto(inbox_row(4, "design"), setup=True, paused=True))
        code, body, _ = text_of(self, inbox, "show")
        self.assertEqual(code, 0)
        self.assertIn("- #4 Item 4\n", body)
        order = [body.index(h) for h in ("Security setup", "Team is paused", "Designs to approve")]
        self.assertEqual(order, sorted(order))
        self.assertIn("[Run log](https://x/log)", body)

    def test_an_unknown_section_fails_instead_of_dropping_the_item(self):
        self.fake.on("GET", f"{P}/inbox", inbox_dto(inbox_row(4, "billing")))
        code, body, err = text_of(self, inbox, "show")
        self.assertEqual((code, body), (1, ""))
        self.assertIn("cannot read", err)

    def test_update_is_a_no_op_without_any_request(self):
        code, out, _ = self.run_cli(inbox, "update")
        self.assertEqual((code, out, self.fake.seen),
                         (0, {"updated": False, "reason": "the console renders Needs you from rows"}, []))


class DigestTest(ScriptTestCase):
    def test_show_lists_what_needs_the_owner(self):
        self.fake.on("GET", f"{P}/inbox", inbox_dto(inbox_row(1, "question", "Ship it?"), setup=True))
        with mock.patch.dict(os.environ, {"TC_CONSOLE_URL": CONSOLE}):
            code, text, _ = text_of(self, digest, "show")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("tc: 1 need you"))
        self.assertIn(f"• #1 Item 1\n  Answer: Ship it?\n  {CONSOLE}/p/tc/t/1", text)

    def test_send_skips_when_only_standing_items_are_open(self):
        self.fake.on("GET", f"{P}/inbox", inbox_dto(setup=True))
        code, out, _ = self.run_cli(digest, "send")
        self.assertEqual((code, out), (0, {"sent": False, "reason": "nothing needs the owner"}))

    def test_a_refused_read_exits_with_the_console_error(self):
        self.fake.on("GET", f"{P}/inbox", problem(409, "tracker-not-cut-over"))
        code, _, err = self.run_cli(digest, "show")
        self.assertEqual(code, 3)
        self.assertIn("still on GitHub Issues", err)


class DemoDecisionsTest(ScriptTestCase):
    def test_reads_the_feed_since_the_demo_item_was_opened(self):
        self.fake.on("GET", f"{P}/items/5", (200, dto(5, createdAt="2026-11-01T09:30:00.000Z"), {}))
        rows = [{"id": 1, "command": "go", "text": "ship", "askId": 2, "reversesCommentId": None, "via": "web",
                 "decidedAt": "2026-11-12T10:00:00.000Z", "sourceUrl": None, "item": {"key": "TC-5", "number": 5}},
                {"id": 2, "command": "approve", "text": "", "askId": None, "reversesCommentId": None, "via": "web",
                 "decidedAt": "2026-11-12T10:00:00.000Z", "sourceUrl": None, "item": {"key": "TC-21", "number": 21}}]
        self.fake.on("GET", f"{P}/decisions?since=2026-11-01T09:30:00Z", (200, {"decisions": rows, "next": None}, {}))
        code, out, _ = self.run_cli(demo_page, "decisions", "5")
        self.assertEqual(code, 0)
        self.assertEqual(out["release"], {"command": "go", "text": "ship", "at": "2026-11-12T10:00:00.000Z"})
        self.assertEqual(out["decisions"]["issue-21"], {"decision": "approve", "comment": "", "key": "TC-21"})
        self.assertEqual(out["source"], "console")


def linked(*rows, number=57, repo="acme/tc", provider="github", next=None):
    """A page of `GET …/prs/N/items` (pr-items.linked fixture shape)."""
    return 200, {"number": number, "provider": provider, "repo": repo, "items": list(rows), "next": next}, {}


class SecurityCheckTest(ScriptTestCase):
    """`pr security-check` decides whether SECURITY is required from the items the console links the PR to
    (team-console#627); every doubt about those links requires it."""

    ITEMS = f"{P}/prs/57/items"

    def check(self, pull=PULL, files=("docs/a.md",), number=57):
        with mock.patch.object(pr.gh, "repo", return_value="acme/tc"), \
                mock.patch.object(pr.gh, "api", return_value=dict(pull, number=number)), \
                mock.patch.object(pr, "changed_files", return_value=list(files)):
            return self.run_cli(pr, "security-check", str(number))

    def test_the_fixture_links_decide(self):
        self.fake.load_fixtures()
        with as_job(stand_in("orchestrator")):
            code, out, _ = self.check()
            self.assertEqual((code, out), (0, {"required": True, "reasons": ["issue labelled security"]}))
            code, out, _ = self.check(number=58)
        self.assertEqual(out, {"required": True, "reasons": [
            "PR #58 is linked to no item in the console (pr link-pr 58): review required"]})

    def test_an_unlabelled_link_needs_none_and_the_prs_text_decides_nothing(self):
        self.fake.on("GET", self.ITEMS, linked(dto(1)))
        pull = dict(PULL, head={"ref": "feature/tc-9-x"}, body="Closes #9, Fixes ST-4 and https://x.example/1")
        code, out, _ = self.check(pull)
        self.assertEqual((code, out), (0, {"required": False, "reasons": []}))
        self.assertEqual([p.split("?")[0] for p in self.paths()], [self.ITEMS])

    def test_a_pr_linked_to_no_item_requires_the_review_whatever_it_names(self):
        self.fake.on("GET", self.ITEMS, linked())
        code, out, _ = self.check(files=["apps/api/auth.ts"])
        self.assertEqual(out["reasons"], ["sensitive path: apps/api/auth.ts",
                                          "PR #57 is linked to no item in the console (pr link-pr 57): review "
                                          "required"])

    def test_every_page_is_read_in_the_consoles_order(self):
        # The cursor is the last item's number in queue order: it may go down from one page to the next.
        self.fake.on("GET", self.ITEMS, linked(dto(9), next=9))
        self.fake.on("GET", f"{self.ITEMS}?after=9", linked(dto(4), next=4))
        self.fake.on("GET", f"{self.ITEMS}?after=4", linked(dto(6, flags=["security"])))
        code, out, _ = self.check()
        self.assertEqual((code, out), (0, {"required": True, "reasons": ["issue labelled security"]}))
        self.assertEqual(len(self.fake.seen), 3)
        self.assertTrue(all("limit=100" in s["path"] for s in self.fake.seen))

    def test_the_gate_fails_closed_on_links_it_cannot_read_in_full(self):
        cases = {
            "not found": [problem(404, "project-not-found")], "refused": [problem(403, "owner-only")],
            "unavailable": [problem(503, "unavailable")], "not json": [(200, b"<html>", {})],
            "no items list": [(200, {"number": 57, "provider": "github", "repo": "acme/tc", "next": None}, {})],
            "another pull request": [linked(dto(1), number=56)], "no repository": [linked(dto(1), repo="")],
            "an item without labels": [linked({**dto(1), "labels": None})],
            "an item without key": [linked({**dto(1), "key": None})],
            "a cursor that is no number": [linked(dto(1), next="2")],
            "a cursor that repeats": [linked(dto(1), next=3), linked(dto(3), next=3)],
            "a failing second page": [linked(dto(1), next=1), problem(503, "unavailable")],
            "pages of two repositories": [linked(dto(1), next=1), linked(dto(2), repo="acme/other")],
        }
        for name, answers in cases.items():
            with self.subTest(name):
                self.fake.routes.clear()
                self.fake.on("GET", self.ITEMS, answers[0])
                for answer in answers[1:]:
                    cursor = answers[0][1]["next"]
                    self.fake.on("GET", f"{self.ITEMS}?after={cursor}", answer)
                code, out, _ = self.check()
                self.assertEqual((code, out["required"]), (0, True))
                self.assertEqual(len(out["reasons"]), 1)
                self.assertRegex(out["reasons"][0],
                                 r"^the items linked to PR #57 could not be read from the console \(.+\): review "
                                 r"required$")

    def test_no_job_environment_requires_the_review_too(self):
        with mock.patch.dict(os.environ, {"TC_JOB_TOKEN": ""}):
            code, out, _ = self.check()
        self.assertEqual(out, {"required": True, "reasons": [
            "the items linked to PR #57 could not be read from the console (config-missing): review required"]})
        self.assertEqual(self.fake.seen, [])

    def test_a_console_project_of_another_repository_requires_the_review(self):
        for answer in (linked(dto(1), repo="acme/elsewhere"), linked(dto(1), provider="gitlab")):
            with self.subTest(repo=answer[1]["repo"], provider=answer[1]["provider"]):
                self.fake.on("GET", self.ITEMS, answer)
                code, out, _ = self.check()
                self.assertEqual((code, out["required"]), (0, True))
                self.assertEqual(out["reasons"], [
                    f"PR #57 is in acme/tc, but the console project's repository is {answer[1]['provider']} "
                    f"{answer[1]['repo']}: review required"])
        self.fake.on("GET", self.ITEMS, linked(dto(1), repo="Acme/TC"))
        self.assertEqual(self.check()[1], {"required": False, "reasons": []})

    def gate(self, reviews):
        with mock.patch.object(pr.gh, "api", return_value=PULL), \
                mock.patch.object(pr, "view", return_value={"head_sha": "head1", "base": "dev"}), \
                mock.patch.object(pr, "changed_files", return_value=["docs/a.md"]), \
                mock.patch.object(pr, "ci", return_value={"state": "pass", "failing": [], "pending": []}), \
                mock.patch.object(pr.gh, "api_list", return_value=reviews), \
                mock.patch.object(pr, "base_project_text", return_value=""), \
                mock.patch.object(pr.gh, "review_login", return_value=None), \
                mock.patch.object(pr.gh, "app_mode", return_value=False), \
                mock.patch.object(pr.gh, "acts_as_owner", return_value=False):
            return pr.gate("acme/tc", 57)

    def test_the_gate_passes_and_fails_on_the_linked_items_labels(self):
        reviews = [{"body": f"{role}: APPROVED", "commit_id": "head1", "submitted_at": "2026-11-12T10:00:00Z",
                    "user": {"login": "x"}} for role in ("QA", "REVIEW")]
        self.fake.on("GET", self.ITEMS, linked(dto(1)))
        self.assertTrue(self.gate(reviews)["passed"])
        for answer in (linked(dto(1), dto(2, flags=["security"])), linked(), problem(503, "unavailable"),
                       linked(dto(1), repo="acme/elsewhere")):
            with self.subTest(answer=answer[1].get("items", answer[0])):
                self.fake.on("GET", self.ITEMS, answer)
                result = self.gate(reviews)
                self.assertFalse(result["passed"])
                self.assertEqual(result["missing"], ["SECURITY: no verdict"])


class LinkPrTest(ScriptTestCase):
    def link(self, *argv, pull=PULL, role=("dev", [1])):
        with mock.patch.object(pr.gh, "repo", return_value="acme/tc"), \
                mock.patch.object(pr.gh, "api", return_value=pull), as_job(stand_in(*role)), \
                mock.patch.dict(os.environ, {"TC_CONSOLE_URL": CONSOLE}):
            return self.run_cli(pr, "link-pr", "57", *argv)

    def test_links_the_item_its_head_branch_names(self):
        self.fake.load_fixtures()
        code, out, _ = self.link()
        self.assertEqual((code, out), (0, {"item": "TC-1", "pr": 57, "pr_url": PULL["html_url"],
                                           "head_ref": "feature/tc-1-login", "linked_at": "2026-11-12T10:00:00.000Z",
                                           "url": f"{CONSOLE}/p/tc/t/1"}))
        post = self.fake.seen[0]
        self.assertEqual((post["method"], post["path"]), ("POST", f"{P}/items/TC-1/pr-links"))
        self.assertRegex(post["headers"]["idempotency-key"], r"^[A-Za-z0-9_-]{8,128}$")

    def test_an_item_the_job_does_not_hold_is_refused_and_explained(self):
        self.fake.load_fixtures()
        code, out, err = self.link(role=("dev", [2]))
        self.assertEqual((code, out), (1, None))
        self.assertIn("TC-1: this job does not hold the item (item-not-held)", err)

    def test_a_link_to_another_repository_is_refused_with_the_field(self):
        self.fake.load_fixtures()
        code, _, err = self.link(pull=dict(PULL, html_url="https://github.com/acme/elsewhere/pull/57"))
        self.assertEqual(code, 1)
        self.assertIn("the console refused the link (url)", err)

    def test_the_item_comes_from_item_when_the_branch_names_none(self):
        pull = dict(PULL, head={"ref": "fix/typo"})
        code, _, err = self.link(pull=pull)
        self.assertEqual((code, self.fake.seen), (1, []))
        self.assertIn("names no item", err)
        for bad in ("TC-0", "x y"):
            code, _, _ = self.link("--item", bad, pull=pull)
            self.assertEqual((code, self.fake.seen), (1, []))
        self.fake.on("POST", f"{P}/items/7/pr-links", (201, {"linkedAt": "2026-11-12T10:00:00.000Z"}, {}))
        code, out, _ = self.link("--item", "tc-7", pull=dict(pull, body="Closes `TC-7`"), role=("dev", [7]))
        self.assertEqual((code, out["item"], out["url"]), (0, "TC-7", f"{CONSOLE}/p/tc/t/7"))

    def test_item_is_refused_when_the_pr_does_not_name_it(self):
        # The gate reads only the items a PR names; a link it cannot see would bypass a `security` label.
        for pull in (dict(PULL, head={"ref": "fix/typo"}), dict(PULL, body="Closes TC-8")):
            with self.subTest(head=pull["head"]["ref"], body=pull["body"]):
                code, _, err = self.link("--item", "TC-7", pull=pull, role=("dev", [7]))
                self.assertEqual((code, self.fake.seen), (1, []))
                self.assertIn("does not name TC-7: add `Closes TC-7` to its body", err)

    def test_a_bare_item_number_is_named_by_its_key_or_number_never_another_projects_key(self):
        # team-console#628: `--item 412` used to pass on a PR naming only ST-412.
        self.fake.on("GET", f"{P}/items/412", (200, dto(412), {}))
        self.fake.on("POST", f"{P}/items/TC-412/pr-links", (201, {"linkedAt": "2026-11-12T10:00:00.000Z"}, {}))
        code, _, err = self.link("--item", "412", pull=dict(PULL, head={"ref": "fix/typo"}, body="Closes ST-412"),
                                 role=("dev", [412]))
        self.assertEqual(code, 1)
        self.assertIn("does not name TC-412", err)
        self.assertEqual(self.paths(), [f"{P}/items/412"])
        for body in ("Closes #412", "Closes TC-412"):
            with self.subTest(body=body):
                code, out, _ = self.link("--item", "#412", pull=dict(PULL, head={"ref": "fix/typo"}, body=body),
                                         role=("dev", [412]))
                self.assertEqual((code, out["item"]), (0, "TC-412"))
        self.assertEqual(self.paths()[-1], f"{P}/items/TC-412/pr-links")


if __name__ == "__main__":
    unittest.main()
