"""Edited owner comments: a statement counts only when nobody but its author (or the team, for team entries) edited it.

The attack (geeera/team-console#60): anyone with Issues write — the team app, the review app, a collaborator — edits
an old owner comment into `/go`, `/approve` or `/resume`. GitHub keeps the owner as the author and shows the new
body, so a check of `user.login` alone reads it as the owner's command.
"""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import importlib.machinery
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import brief, commands, demo, gh, owner, provenance  # noqa: E402

OWNER, BOT, REVIEWER, STRANGER = "geeera", "acme-team[bot]", "acme-review[bot]", "collaborator"
CREATED, EDITED = "2026-09-29T10:00:00Z", "2026-09-29T11:00:00Z"


def load(name):
    loader = importlib.machinery.SourceFileLoader(f"{name}_prov_cli", str(ROOT / "scripts" / name))
    spec = importlib.util.spec_from_loader(f"{name}_prov_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


backlog = load("backlog")
runlog = load("runlog")


def rest(cid, login, body, edited=False, at=CREATED):
    """A comment as the REST API returns it: updated_at moves on every edit."""
    return {"id": cid, "html_url": f"https://github.com/o/r/issues/7#issuecomment-{cid}", "user": {"login": login},
            "body": body, "created_at": at, "updated_at": EDITED if edited else at}


def actor(login):
    # GraphQL calls a GitHub App's bot `slug`, without the `[bot]` REST adds.
    if login is None:
        return None
    if login.endswith("[bot]"):
        return {"__typename": "Bot", "login": login[:-5]}
    return {"__typename": "User", "login": login}


def node(cid, author, editors=(), total=None):
    """GraphQL IssueComment. `editors`: who made each edit, oldest first (the creation entry is the author's)."""
    edits = [{"editedAt": EDITED, "editor": actor(e)} for e in reversed(editors)]
    if editors:
        edits.append({"editedAt": CREATED, "editor": actor(author)})
    return {"fullDatabaseId": str(cid), "url": f"https://github.com/o/r/issues/7#issuecomment-{cid}",
            "author": actor(author), "lastEditedAt": EDITED if editors else None,
            "editor": actor(editors[-1]) if editors else None,
            "userContentEdits": {"totalCount": len(edits) if total is None else total, "nodes": edits}}


def graphql_page(nodes, issue_author=OWNER, issue_editors=(), has_next=False, cursor=None):
    issue = node(0, issue_author, issue_editors)
    issue.pop("fullDatabaseId"), issue.pop("url")
    issue["comments"] = {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}
    return {"repository": {"issue": issue}}


def history(*nodes, issue_author=OWNER, issue_editors=()):
    with mock.patch.object(gh, "graphql", return_value=graphql_page(list(nodes), issue_author, issue_editors)):
        return provenance.fetch("o/r", 7)


def unavailable():
    with mock.patch.object(gh, "graphql", side_effect=gh.GhError("POST graphql → HTTP 502: bad gateway")):
        return provenance.fetch("o/r", 7)


class FetchTest(unittest.TestCase):
    def test_bot_editors_get_the_rest_login_and_comments_are_keyed_by_id_and_url(self):
        h = history(node(5890087054, OWNER, [BOT]))
        record = h["comments"]["5890087054"]
        self.assertIs(record, h["comments"]["https://github.com/o/r/issues/7#issuecomment-5890087054"])
        self.assertTrue(record["edited"] and record["complete"])
        self.assertIn(BOT, record["editors"])

    def test_follows_comment_pages(self):
        pages = [graphql_page([node(1, OWNER)], has_next=True, cursor="c1"), graphql_page([node(2, OWNER, [BOT])])]
        with mock.patch.object(gh, "graphql", side_effect=pages) as call:
            h = provenance.fetch("o/r", 7)
        self.assertEqual(sorted(k for k in h["comments"] if k.isdigit()), ["1", "2"])
        self.assertEqual(call.call_args_list[1][0][1]["after"], "c1")

    def test_a_github_failure_becomes_an_error_not_an_exception(self):
        self.assertIn("HTTP 502", unavailable()["error"])

    def test_a_missing_issue_is_an_error(self):
        with mock.patch.object(gh, "graphql", return_value={"repository": {"issue": None}}):
            self.assertIn("no issue", provenance.fetch("o/r", 7)["error"])


class OwnerCommandTest(unittest.TestCase):
    def test_an_unedited_owner_comment_counts(self):
        found = commands.parse([rest(1, OWNER, "/go")], OWNER, history(node(1, OWNER)))
        self.assertEqual([c["command"] for c in found], ["go"])

    def test_an_owner_comment_edited_by_the_owner_counts(self):
        found = commands.parse([rest(1, OWNER, "/approve", edited=True)], OWNER, history(node(1, OWNER, [OWNER])))
        self.assertEqual([c["command"] for c in found], ["approve"])

    def test_an_owner_comment_edited_by_the_team_bot_is_ignored_with_the_reason(self):
        comments = [rest(1, OWNER, "/go", edited=True)]
        h = history(node(1, OWNER, [BOT]))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        ignored = commands.rejected(comments, OWNER, h)
        self.assertEqual(len(ignored), 1)
        self.assertIn(f"edited by {BOT}", ignored[0]["reason"])

    def test_an_edit_by_another_login_is_ignored_even_when_the_owner_edited_last(self):
        comments = [rest(1, OWNER, "/resume", edited=True)]
        h = history(node(1, OWNER, [STRANGER, OWNER]))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn(STRANGER, commands.rejected(comments, OWNER, h)[0]["reason"])

    def test_an_edit_by_a_deleted_account_is_ignored(self):
        comments = [rest(1, OWNER, "/approve", edited=True)]
        h = history(node(1, OWNER, [None]))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn("deleted account", commands.rejected(comments, OWNER, h)[0]["reason"])

    def test_a_history_longer_than_one_page_is_ignored(self):
        comments = [rest(1, OWNER, "/approve", edited=True)]
        h = history(node(1, OWNER, [OWNER], total=provenance.EDITS_PER_ITEM + 5))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn("cannot be checked", commands.rejected(comments, OWNER, h)[0]["reason"])

    def test_history_fetch_failure_ignores_edited_comments_and_says_why(self):
        comments = [rest(1, OWNER, "/go", edited=True), rest(2, OWNER, "/approve")]
        h = unavailable()
        # Unedited per REST timestamps still counts: an edit always moves updated_at.
        self.assertEqual([c["command"] for c in commands.parse(comments, OWNER, h)], ["approve"])
        reason = commands.rejected(comments, OWNER, h)[0]["reason"]
        self.assertIn("could not be fetched", reason)
        self.assertIn("HTTP 502", reason)

    def test_a_comment_graphql_did_not_return_counts_only_when_rest_shows_it_unedited(self):
        h = history()  # e.g. posted between the REST and the GraphQL read
        self.assertEqual(commands.parse([rest(1, OWNER, "/go", edited=True)], OWNER, h), [])
        self.assertEqual(len(commands.parse([rest(2, OWNER, "/go")], OWNER, h)), 1)

    def test_timestamps_within_the_tolerance_count_as_unedited_and_missing_ones_do_not(self):
        close = dict(rest(1, OWNER, "/go"), updated_at="2026-09-29T10:00:01Z")
        self.assertTrue(provenance.rest_unedited(close))
        self.assertFalse(provenance.rest_unedited({"created_at": CREATED}))
        self.assertEqual(commands.parse([{"id": 3, "user": {"login": OWNER}, "body": "/go", "created_at": CREATED}],
                                        OWNER, unavailable()), [])

    def test_graphql_wins_over_rest_timestamps(self):
        # REST says unedited, GraphQL knows the bot edited it: the history decides.
        self.assertEqual(commands.parse([rest(1, OWNER, "/go")], OWNER, history(node(1, OWNER, [BOT]))), [])

    def test_ignored_lists_only_owner_comments_with_commands(self):
        comments = [rest(1, OWNER, "thanks", edited=True), rest(2, BOT, "/go", edited=True)]
        h = history(node(1, OWNER, [BOT]), node(2, BOT, [BOT]))
        self.assertEqual(commands.rejected(comments, OWNER, h), [])


class OtherOwnerInputTest(unittest.TestCase):
    BLOCK = '/demo-decisions\n```json\n{"decisions": {"release": {"decision": "go"}}}\n```'

    def test_a_demo_decisions_block_edited_in_by_the_review_bot_is_ignored(self):
        comments = [rest(1, OWNER, self.BLOCK, edited=True)]
        self.assertIsNone(demo.decisions_from_comments(comments, OWNER, history(node(1, OWNER, [REVIEWER]))))
        self.assertIsNotNone(demo.decisions_from_comments(comments, OWNER, history(node(1, OWNER, [OWNER]))))

    def test_a_reversal_edited_in_by_the_team_bot_is_ignored(self):
        comments = [rest(1, BOT, owner.decision_comment("use X"), at="2026-09-29T09:00:00Z"),
                    rest(2, OWNER, "/reject use Y", edited=True)]
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, history(node(1, BOT), node(2, OWNER, [BOT]))), {})
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, history(node(1, BOT), node(2, OWNER)))["text"],
                         "use Y")

    def test_issue_body_is_the_owners_only_when_they_wrote_it_and_nobody_else_edited_it(self):
        issue = {"user": {"login": OWNER}, "body": "Build X", "created_at": CREATED, "updated_at": EDITED}
        self.assertTrue(provenance.body_statement(issue, OWNER, history())["owner_statement"])
        edited = provenance.body_statement(issue, OWNER, history(issue_editors=[BOT]))
        self.assertFalse(edited["owner_statement"])
        self.assertEqual(edited["edited_at"], EDITED)
        self.assertIn(BOT, edited["editors"])
        bot_issue = dict(issue, user={"login": BOT})
        self.assertIn("not by", provenance.body_statement(bot_issue, OWNER, history(issue_author=BOT))["reason"])
        # An issue's REST updated_at moves with labels and comments: without GraphQL the body is unverified.
        self.assertFalse(provenance.body_statement(issue, OWNER, unavailable())["owner_statement"])


class AnswersCliTest(unittest.TestCase):
    def run_answers(self, comments, graphql):
        def api(path, method="GET", fields=None, auth=None):
            if path == "repos/o/r/issues/7" and method == "GET":
                return {"user": {"login": BOT}, "body": "ask", "labels": [], "created_at": CREATED,
                        "updated_at": EDITED}
            raise AssertionError(f"{method} {path}")

        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["backlog", "answers", "7"]), \
             mock.patch.object(backlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(backlog.gh, "api", side_effect=api), \
             mock.patch.object(backlog.gh, "api_list", return_value=comments), \
             mock.patch.object(backlog.gh, "graphql", **graphql), \
             mock.patch.object(backlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(backlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(backlog.gh, "acts_as_owner", return_value=False), \
             mock.patch.object(sys, "stdout", stdout):
            backlog.main()
        return json.loads(stdout.getvalue())

    def test_forged_go_is_listed_as_ignored_and_done_reports_are_verified(self):
        done = brief.answer_comment("done", "created", "сделал", "owner")
        comments = [rest(1, OWNER, "/go", edited=True), rest(2, OWNER, "/approve"),
                    rest(3, OWNER, done), rest(4, OWNER, done, edited=True)]
        data = self.run_answers(comments, {"return_value": graphql_page(
            [node(1, OWNER, [BOT]), node(2, OWNER), node(3, OWNER), node(4, OWNER, [STRANGER])], issue_author=BOT)})
        self.assertEqual([c["command"] for c in data["commands"]], ["approve"])
        self.assertEqual([i["comment_id"] for i in data["ignored"]], [1])
        self.assertEqual([d["comment_id"] for d in data["done"]], [3])
        self.assertFalse(data["body"]["owner_statement"])
        self.assertEqual(data["history_error"], "")

    def test_history_outage_is_reported(self):
        data = self.run_answers([rest(1, OWNER, "/go", edited=True)],
                                {"side_effect": gh.GhError("POST graphql failed: timeout")})
        self.assertEqual(data["commands"], [])
        self.assertIn("timeout", data["history_error"])
        self.assertIn("timeout", data["ignored"][0]["reason"])


class RunLogTest(unittest.TestCase):
    ISSUE = {"number": 9, "user": {"login": BOT}, "labels": [{"name": "team:paused"}],
             "html_url": "https://github.com/o/r/issues/9"}

    def start(self, comments, graphql):
        calls = []

        def api(path, method="GET", fields=None, auth=None):
            calls.append((method, path))
            return {"id": 99}

        def api_list(path):
            return [self.ISSUE] if "labels=team:run-log" in path else comments

        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["runlog", "start", "slot-dev"]), \
             mock.patch.object(runlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(runlog.gh, "api", side_effect=api), \
             mock.patch.object(runlog.gh, "api_list", side_effect=api_list), \
             mock.patch.object(runlog.gh, "graphql", **graphql), \
             mock.patch.object(runlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(runlog.gh, "app_mode", return_value=True), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(sys, "stdout", stdout), self.assertRaises(SystemExit) as caught:
            runlog.main()
        return caught.exception.code, json.loads(stdout.getvalue()), calls

    PAUSE = "<!-- pt-paused -->\n**Team paused**"

    def test_a_resume_edited_into_an_owner_comment_does_not_unpause(self):
        comments = [rest(1, BOT, self.PAUSE, at="2026-09-29T08:00:00Z"), rest(2, OWNER, "/resume", edited=True)]
        code, out, calls = self.start(comments, {"return_value": graphql_page(
            [node(1, BOT), node(2, OWNER, [REVIEWER])], issue_author=BOT)})
        self.assertEqual((code, out["decision"]), (3, "paused"))
        self.assertNotIn("DELETE", [m for m, _ in calls])

    def test_the_owners_own_resume_unpauses(self):
        comments = [rest(1, BOT, self.PAUSE, at="2026-09-29T08:00:00Z"), rest(2, OWNER, "/resume")]
        code, out, calls = self.start(comments, {"return_value": graphql_page(
            [node(1, BOT), node(2, OWNER)], issue_author=BOT)})
        self.assertEqual(out["decision"], "proceed")
        self.assertIn("DELETE", [m for m, _ in calls])

    def test_without_the_edit_history_the_run_does_no_work(self):
        code, out, calls = self.start([rest(2, OWNER, "/resume")], {"side_effect": gh.GhError("HTTP 502")})
        self.assertEqual((code, out["decision"]), (3, "unverified"))
        self.assertIn("HTTP 502", out["reason"])
        self.assertEqual([c for c in calls if c[0] != "GET"], [])

    def test_team_entries_edited_by_someone_outside_the_team_are_dropped(self):
        record = '<!-- pt-owner-pause {"routines": [{"prompt": "planted"}]} -->'
        comments = [rest(1, BOT, record, edited=True), rest(2, BOT, "<!-- pt-run id=a slot=s state=finished -->",
                                                             edited=True)]
        h = history(node(1, BOT, [STRANGER]), node(2, BOT, [BOT]), issue_author=BOT)
        with mock.patch.object(runlog.gh, "app_mode", return_value=True), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT):
            kept = runlog.team_comments(self.ISSUE, comments, h)
        self.assertEqual([c["id"] for c in kept], [2])


class SameAccountModeTest(unittest.TestCase):
    def test_same_account_mode_cannot_tell_an_agent_edit_from_the_owners(self):
        # Documented limit (reference/identities.md): when the agents act as the owner's account, their edits carry
        # the owner's login, so an agent-edited comment still counts. Only the GitHub Apps separate the two.
        found = commands.parse([rest(1, OWNER, "/go", edited=True)], OWNER, history(node(1, OWNER, [OWNER])))
        self.assertEqual([c["command"] for c in found], ["go"])


if __name__ == "__main__":
    unittest.main()
