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
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import brief, commands, demo, gh, owner, project, provenance, runlogissue, runstate  # noqa: E402

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


def node(c, editors=(), total=None, body=None, entries=True):
    """GraphQL IssueComment for REST comment `c`, read at the same moment. `editors`: who made each edit, oldest
    first (the creation entry is the author's); `body`: what GraphQL returns, by default the REST body."""
    author = c["user"]["login"]
    edits = [{"editedAt": EDITED, "editor": actor(e)} for e in reversed(editors)]
    if editors:
        edits.append({"editedAt": CREATED, "editor": actor(author)})
    if not entries:
        edits = []
    return {"fullDatabaseId": str(c["id"]), "url": c["html_url"], "body": c["body"] if body is None else body,
            "author": actor(author), "lastEditedAt": EDITED if editors else None,
            "editor": actor(editors[-1]) if editors else None,
            "userContentEdits": {"totalCount": len(edits) if total is None else total, "nodes": edits}}


def graphql_page(nodes, issue_author=OWNER, issue_editors=(), has_next=False, cursor=None, issue_body="Build X",
                 typename="Issue"):
    issue = node({"id": 0, "html_url": "", "user": {"login": issue_author}, "body": issue_body}, issue_editors)
    issue.pop("fullDatabaseId"), issue.pop("url")
    issue["__typename"] = typename
    issue["comments"] = {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}
    return {"repository": {"issueOrPullRequest": issue}}


def history(*nodes, issue_author=OWNER, issue_editors=()):
    with mock.patch.object(gh, "graphql", return_value=graphql_page(list(nodes), issue_author, issue_editors)):
        return provenance.fetch("o/r", 7)


def unavailable():
    with mock.patch.object(gh, "graphql", side_effect=gh.GhError("POST graphql → HTTP 502: bad gateway")):
        return provenance.fetch("o/r", 7)


class FetchTest(unittest.TestCase):
    def test_bot_editors_get_the_rest_login_and_comments_are_keyed_by_id_and_url(self):
        c = rest(5890087054, OWNER, "/go", edited=True)
        h = history(node(c, [BOT]))
        record = h["comments"]["5890087054"]
        self.assertIs(record, h["comments"][c["html_url"]])
        self.assertTrue(record["edited"] and record["complete"])
        self.assertIn(BOT, record["editors"])

    def test_follows_comment_pages(self):
        pages = [graphql_page([node(rest(1, OWNER, "a"))], has_next=True, cursor="c1"),
                 graphql_page([node(rest(2, OWNER, "b", edited=True), [BOT])])]
        with mock.patch.object(gh, "graphql", side_effect=pages) as call:
            h = provenance.fetch("o/r", 7)
        self.assertEqual(sorted(k for k in h["comments"] if k.isdigit()), ["1", "2"])
        self.assertEqual(call.call_args_list[1][0][1]["after"], "c1")

    def test_a_github_failure_becomes_an_error_not_an_exception(self):
        self.assertIn("HTTP 502", unavailable()["error"])

    def test_a_missing_issue_is_an_error(self):
        with mock.patch.object(gh, "graphql", return_value={"repository": {"issueOrPullRequest": None}}):
            self.assertIn("no issue", provenance.fetch("o/r", 7)["error"])

    def test_a_server_without_full_database_ids_falls_back_to_url_keys(self):
        c = rest(1, OWNER, "/go")
        url_only = node(c)
        url_only.pop("fullDatabaseId")
        answers = [gh.GhError("GraphQL: Field 'fullDatabaseId' doesn't exist on type 'IssueComment'"),
                   graphql_page([url_only])]
        with mock.patch.object(gh, "graphql", side_effect=answers) as call:
            h = provenance.fetch("o/r", 7)
        self.assertNotIn("fullDatabaseId", call.call_args_list[1][0][0])
        self.assertEqual(list(h["comments"]), [c["html_url"]])
        self.assertEqual([x["command"] for x in commands.parse([c], OWNER, h)], ["go"])

    def test_the_query_asks_for_an_issue_or_a_pull_request_by_number(self):
        with mock.patch.object(gh, "graphql", return_value=graphql_page([])) as call:
            provenance.fetch("o/r", 7)
        query = call.call_args[0][0]
        self.assertIn("issueOrPullRequest(number: $number)", query)
        self.assertIn("... on Issue {", query)
        self.assertIn("... on PullRequest {", query)
        self.assertNotIn("issue(number:", query)

    def test_a_pull_request_number_is_checked_like_an_issue(self):
        genuine, forged = rest(1, OWNER, "/go"), rest(2, OWNER, "/approve", edited=True)
        page = graphql_page([node(genuine), node(forged, [BOT])], typename="PullRequest")
        with mock.patch.object(gh, "graphql", return_value=page):
            h = provenance.fetch("o/r", 15)
        self.assertNotIn("error", h)
        self.assertEqual([x["command"] for x in commands.parse([genuine, forged], OWNER, h)], ["go"])
        self.assertEqual([r["comment_id"] for r in commands.rejected([genuine, forged], OWNER, h)], [2])

    def test_anything_but_an_issue_or_pull_request_fails_closed(self):
        for answer in ({"repository": {"issueOrPullRequest": None}}, {"repository": None}, None,
                       graphql_page([], typename="Discussion")):
            with self.subTest(answer=str(answer)[:60]):
                with mock.patch.object(gh, "graphql", return_value=answer):
                    h = provenance.fetch("o/r", 15)
                self.assertIn("no issue or pull request #15", h["error"])
                edited = rest(1, OWNER, "/go", edited=True)
                self.assertEqual(commands.parse([edited], OWNER, h), [])

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(gh, "graphql", side_effect=gh.GhError("HTTP 502")) as call:
            self.assertIn("502", provenance.fetch("o/r", 7)["error"])
        self.assertEqual(call.call_count, 1)


class OwnerCommandTest(unittest.TestCase):
    def test_an_unedited_owner_comment_counts(self):
        c = rest(1, OWNER, "/go")
        self.assertEqual([x["command"] for x in commands.parse([c], OWNER, history(node(c)))], ["go"])

    def test_an_owner_comment_edited_by_the_owner_counts(self):
        c = rest(1, OWNER, "/approve", edited=True)
        self.assertEqual([x["command"] for x in commands.parse([c], OWNER, history(node(c, [OWNER])))], ["approve"])

    def test_an_owner_comment_edited_by_the_team_bot_is_ignored_with_the_reason(self):
        comments = [rest(1, OWNER, "/go", edited=True)]
        h = history(node(comments[0], [BOT]))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        ignored = commands.rejected(comments, OWNER, h)
        self.assertEqual(len(ignored), 1)
        self.assertIn(f"edited by {BOT}", ignored[0]["reason"])

    def test_an_edit_by_another_login_is_ignored_even_when_the_owner_edited_last(self):
        comments = [rest(1, OWNER, "/resume", edited=True)]
        h = history(node(comments[0], [STRANGER, OWNER]))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn(STRANGER, commands.rejected(comments, OWNER, h)[0]["reason"])

    def test_an_edit_by_a_deleted_account_is_ignored(self):
        comments = [rest(1, OWNER, "/approve", edited=True)]
        h = history(node(comments[0], [None]))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn("deleted account", commands.rejected(comments, OWNER, h)[0]["reason"])

    def test_a_history_longer_than_one_page_is_ignored(self):
        comments = [rest(1, OWNER, "/approve", edited=True)]
        h = history(node(comments[0], [OWNER], total=provenance.EDITS_PER_ITEM + 5))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn("incomplete", commands.rejected(comments, OWNER, h)[0]["reason"])

    def test_an_edit_without_history_entries_is_ignored_even_when_the_last_editor_is_the_owner(self):
        comments = [rest(1, OWNER, "/approve", edited=True)]
        h = history(node(comments[0], [OWNER], entries=False))
        self.assertEqual(commands.parse(comments, OWNER, h), [])
        self.assertIn("incomplete", commands.rejected(comments, OWNER, h)[0]["reason"])

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

    def test_graphql_history_wins_over_rest_timestamps(self):
        c = rest(1, OWNER, "/go")
        self.assertEqual(commands.parse([c], OWNER, history(node(c, [BOT]))), [])

    def test_rest_edited_but_graphql_unedited_is_not_counted(self):
        # Two replicas disagree: REST says edited an hour later, GraphQL knows of no edit. Trust neither.
        c = rest(1, OWNER, "/go", edited=True)
        h = history(node(c))
        self.assertEqual(commands.parse([c], OWNER, h), [])
        self.assertIn("REST shows it edited", commands.rejected([c], OWNER, h)[0]["reason"])

    def test_the_text_comes_from_the_same_read_as_the_history(self):
        c = rest(1, OWNER, "/go")  # REST (a stale replica) shows a command…
        self.assertEqual(commands.parse([c], OWNER, history(node(c, body="thanks, looks good"))), [])
        c2 = rest(2, OWNER, "thanks")  # …or GraphQL has the newer text: the checked text is the one read
        self.assertEqual([x["command"] for x in commands.parse([c2], OWNER, history(node(c2, body="/approve")))],
                         ["approve"])

    def test_an_author_mismatch_between_the_two_reads_is_not_counted(self):
        c = rest(1, OWNER, "/go")
        forged = node(c)
        forged["author"] = actor(STRANGER)
        self.assertEqual(commands.parse([c], OWNER, history(forged)), [])

    def test_ignored_lists_only_owner_comments_with_commands(self):
        comments = [rest(1, OWNER, "thanks", edited=True), rest(2, BOT, "/go", edited=True)]
        h = history(node(comments[0], [BOT]), node(comments[1], [BOT]))
        self.assertEqual(commands.rejected(comments, OWNER, h), [])


class OtherOwnerInputTest(unittest.TestCase):
    BLOCK = '/demo-decisions\n```json\n{"decisions": {"release": {"decision": "go"}}}\n```'

    def test_a_demo_decisions_block_edited_in_by_the_review_bot_is_ignored(self):
        c = rest(1, OWNER, self.BLOCK, edited=True)
        self.assertIsNone(demo.decisions_from_comments([c], OWNER, history(node(c, [REVIEWER]))))
        self.assertIsNotNone(demo.decisions_from_comments([c], OWNER, history(node(c, [OWNER]))))

    def test_a_reversal_edited_in_by_the_team_bot_is_ignored(self):
        comments = [rest(1, BOT, owner.decision_comment("use X"), at="2026-09-29T09:00:00Z"),
                    rest(2, OWNER, "/reject use Y", edited=True)]
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, history(node(comments[0]), node(comments[1], [BOT])),
                                                 [BOT]), {})
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, history(node(comments[0]), node(comments[1], [OWNER])),
                                                 [BOT])["text"], "use Y")

    def test_issue_body_is_the_owners_only_when_they_wrote_it_and_nobody_else_edited_it(self):
        issue = {"user": {"login": OWNER}, "body": "Build X", "created_at": CREATED, "updated_at": EDITED}
        mine = provenance.body_statement(issue, OWNER, history())
        self.assertTrue(mine["owner_statement"])
        self.assertEqual(mine["body"], "Build X")
        edited = provenance.body_statement(issue, OWNER, history(issue_editors=[BOT]))
        self.assertFalse(edited["owner_statement"])
        self.assertIsNone(edited["body"])
        self.assertEqual(edited["edited_at"], EDITED)
        self.assertIn(BOT, edited["editors"])
        bot_issue = dict(issue, user={"login": BOT})
        self.assertIn("not by", provenance.body_statement(bot_issue, OWNER, history(issue_author=BOT))["reason"])
        # An issue's REST updated_at moves with labels and comments: without GraphQL the body is unverified.
        self.assertFalse(provenance.body_statement(issue, OWNER, unavailable())["owner_statement"])


class TeamDecisionDateTest(unittest.TestCase):
    """A decision marker dates the team's decision; an owner /reject after it is a reversal."""
    DECIDED = rest(1, BOT, owner.decision_comment("use X"), at="2026-09-29T09:00:00Z")
    REJECT = rest(2, OWNER, "/reject use Y", at="2026-09-29T10:00:00Z")

    def test_a_marker_posted_by_someone_outside_the_team_cannot_bury_a_reversal(self):
        buried = rest(3, STRANGER, owner.decision_comment("still X"), at="2026-09-29T12:00:00Z")
        comments = [self.DECIDED, self.REJECT, buried]
        h = history(*(node(c) for c in comments))
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, h, [BOT])["text"], "use Y")

    def test_a_marker_edited_into_a_later_team_comment_cannot_bury_it_either(self):
        later = rest(3, BOT, owner.decision_comment("still X"), at="2026-09-29T12:00:00Z", edited=True)
        comments = [self.DECIDED, self.REJECT, later]
        h = history(node(self.DECIDED), node(self.REJECT), node(later, [REVIEWER]))
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, h, [BOT])["text"], "use Y")
        self.assertEqual(owner.team_decided_at(comments, [BOT, OWNER], h), "2026-09-29T09:00:00Z")

    def test_the_teams_own_later_decision_answers_it_visibly(self):
        later = rest(3, BOT, owner.decision_comment("use Y", "2", "https://x/r2"), at="2026-09-29T12:00:00Z")
        comments = [self.DECIDED, self.REJECT, later]
        self.assertIn("pt-reversal-handled id=2", later["body"])
        self.assertIn("**Answers your /reject:** https://x/r2", later["body"])
        h = history(*(node(c) for c in comments))
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, h, [BOT]), {})
        handled = owner.handled_reversals(comments, [BOT], h)
        self.assertEqual([x["reject_comment_id"] for x in handled], [2])
        summary = brief.summary(None, [], [], [{"number": 7, "title": "t", "url": "u", "decided_at": later["created_at"],
                                                "handled_reversals": handled}], [], [], None, False)
        self.assertEqual([(a["number"], a["reject_comment_id"]) for a in summary["answered_rejects"]], [(7, 2)])

    def test_a_status_reason_quoting_the_marker_is_not_a_decision(self):
        # `backlog move/close --reason` and relayed answers are team comments whose body does not start with it.
        moved = rest(3, BOT, f"**Status → `blocked`**: {owner.DECISION_MARKER} still X", at="2026-09-29T12:00:00Z")
        comments = [self.DECIDED, self.REJECT, moved]
        h = history(*(node(c) for c in comments))
        self.assertEqual(owner.reversed_by_owner(comments, OWNER, h, [BOT])["text"], "use Y")
        self.assertFalse(owner.is_decision(moved["body"]))


class DecideCliTest(unittest.TestCase):
    def run_decide(self, argv, comments):
        posted = []

        def api(path, method="GET", fields=None, auth=None):
            if path == "repos/o/r/issues/7" and method == "GET":
                return {"number": 7, "title": "t", "html_url": "u", "state": "open", "labels": [{"name": "kind:task"}]}
            if path == "repos/o/r/issues/7/comments" and method == "POST":
                posted.append(fields["body"])
                return {"html_url": "https://x/c", "id": 9}
            if path == "repos/o/r/issues/7" and method == "PATCH":
                return {"number": 7, "title": "t", "html_url": "u", "state": "open", "labels": []}
            raise AssertionError(f"{method} {path}")

        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["backlog", *argv]), \
             mock.patch.object(backlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(backlog.gh, "api", side_effect=api), \
             mock.patch.object(backlog.gh, "api_list", return_value=comments), \
             mock.patch.object(backlog.gh, "graphql", return_value=graphql_page([node(c) for c in comments])), \
             mock.patch.object(backlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(backlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(sys, "stdout", stdout):
            backlog.main()
        return posted

    COMMENTS = [TeamDecisionDateTest.DECIDED, TeamDecisionDateTest.REJECT]

    def test_decide_refuses_while_an_owner_reversal_is_open(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_decide(["decide", "7", "--body", "still X"], self.COMMENTS)
        self.assertIn("--handles-reversal 2", str(caught.exception))

    def test_decide_naming_the_reversal_records_it_as_handled(self):
        posted = self.run_decide(["decide", "7", "--body", "use Y", "--handles-reversal", "2"], self.COMMENTS)
        self.assertIn("pt-reversal-handled id=2", posted[0])

    def test_decide_without_a_reversal_just_records(self):
        posted = self.run_decide(["decide", "7", "--body", "use X"], [TeamDecisionDateTest.DECIDED])
        self.assertNotIn("pt-reversal-handled", posted[0])

    def test_a_decision_marker_cannot_be_posted_as_a_plain_comment(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_decide(["comment", "7", "--body", owner.decision_comment("x")], [])
        self.assertIn("backlog decide", str(caught.exception))


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
             mock.patch.object(backlog.runlogissue, "acted_on", side_effect=AssertionError("once per run only")), \
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
        nodes = [node(comments[0], [BOT]), node(comments[1]), node(comments[2]), node(comments[3], [STRANGER])]
        data = self.run_answers(comments, {"return_value": graphql_page(nodes, issue_author=BOT)})
        self.assertEqual([c["command"] for c in data["commands"]], ["approve"])
        self.assertEqual([i["comment_id"] for i in data["ignored"]], [1])
        self.assertEqual([d["comment_id"] for d in data["done"]], [3])
        self.assertFalse(data["body"]["owner_statement"])
        self.assertEqual(data["history_error"], "")
        self.assertNotIn("vanished", data)

    def test_history_outage_is_reported(self):
        data = self.run_answers([rest(1, OWNER, "/go", edited=True)],
                                {"side_effect": gh.GhError("POST graphql failed: timeout")})
        self.assertEqual(data["commands"], [])
        self.assertIn("timeout", data["history_error"])
        self.assertIn("timeout", data["ignored"][0]["reason"])



class VanishedCliTest(unittest.TestCase):
    NOW = "2099-01-01T00:00:00Z"  # every entry below is recent

    def run_vanished(self, acted, comments_by_issue):
        listed = []

        def api_list(path):
            listed.append(path)
            number = int(path.split("/issues/")[1].split("/")[0])
            found = comments_by_issue.get(number, [])
            if isinstance(found, Exception):
                raise found
            return found

        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["backlog", "vanished"]), \
             mock.patch.object(backlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(backlog.gh, "api_list", side_effect=api_list), \
             mock.patch.object(backlog.runlogissue, "acted_on", return_value=acted), \
             mock.patch.object(sys, "stdout", stdout):
            backlog.main()
        return json.loads(stdout.getvalue()), listed

    def test_a_deleted_command_the_team_acted_on_is_flagged_once_per_issue_read(self):
        acted = ([{"issue": 7, "comment_id": 1, "run_id": "r1", "at": self.NOW},
                  {"issue": 7, "comment_id": 2, "run_id": "r1", "at": self.NOW},
                  {"issue": 8, "comment_id": 5, "run_id": "r2", "at": self.NOW},
                  {"issue": 9, "comment_id": 6, "run_id": "r0", "at": "2020-01-01T00:00:00Z"}], "")
        data, listed = self.run_vanished(acted, {7: [rest(2, OWNER, "/approve")], 8: [rest(5, OWNER, "/go")]})
        self.assertEqual([(v["issue"], v["comment_id"]) for v in data["vanished"]], [(7, 1)])
        self.assertEqual(data["checked"], 3)  # the entry older than --days is not re-checked
        self.assertEqual(len(listed), 2)

    def test_a_deleted_issue_reports_all_its_commands_and_the_check_continues(self):
        acted = ([{"issue": 7, "comment_id": 1, "run_id": "r1", "at": self.NOW},
                  {"issue": 7, "comment_id": 2, "run_id": "r1", "at": self.NOW},
                  {"issue": 8, "comment_id": 5, "run_id": "r2", "at": self.NOW}], "")
        gone = gh.GhError("GET https://api.github.com/repos/o/r/issues/7/comments → HTTP 410: gone")
        data, _ = self.run_vanished(acted, {7: gone, 8: []})
        self.assertEqual([(v["issue"], v["comment_id"]) for v in data["vanished"]], [(7, 1), (7, 2), (8, 5)])
        self.assertEqual(data["vanished"][0]["reason"], "issue #7 is gone")

    def test_other_failures_abort_instead_of_reporting_nothing(self):
        acted = ([{"issue": 7, "comment_id": 1, "run_id": "r1", "at": self.NOW}], "")
        with self.assertRaises(SystemExit) as caught:
            self.run_vanished(acted, {7: gh.GhError("GET … → HTTP 502: bad gateway")})
        self.assertIn("502", str(caught.exception))

    def test_an_unreadable_run_log_is_reported_not_guessed(self):
        data, _ = self.run_vanished(([], "run log unreadable: 502"), {})
        self.assertEqual(data["vanished"], [])
        self.assertIn("502", data["error"])


class ActedRecordTest(unittest.TestCase):
    def test_acted_pairs_are_parsed_and_round_trip_through_a_run_entry(self):
        pairs = runstate.parse_acted(["7:5890087054", "12:3"])
        body = f"<!-- pt-run id=a slot=s state=finished -->\n{runstate.acted_marker(pairs)}\n**s** finished"
        run = runstate.parse_runs([{"body": body, "created_at": CREATED, "id": 1}])[0]
        self.assertEqual(run["acted"], [[7, 5890087054], [12, 3]])

    def test_malformed_acted_values_are_refused_or_ignored(self):
        for bad in ("7", "7:x", "a:1", "7:1:2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                runstate.parse_acted([bad])
        body = '<!-- pt-run id=a slot=s state=finished -->\n<!-- pt-acted [[7, "x"], 3] -->'
        self.assertEqual(runstate.parse_runs([{"body": body, "created_at": CREATED}])[0]["acted"], [])

    def test_acted_on_reads_only_the_teams_unedited_entries(self):
        log = {"number": 9, "user": {"login": BOT}, "created_at": CREATED}
        mine = rest(1, BOT, "<!-- pt-run id=r1 slot=s state=finished -->\n<!-- pt-acted [[7, 11]] -->", edited=True)
        planted = rest(2, BOT, "<!-- pt-run id=r2 slot=s state=finished -->\n<!-- pt-acted [[7, 12]] -->",
                       edited=True)

        def api_list(path):
            return [log] if "labels=" in path else [mine, planted]

        with mock.patch.object(gh, "api_list", side_effect=api_list), \
             mock.patch.object(gh, "graphql", return_value=graphql_page(
                 [node(mine, [BOT]), node(planted, [STRANGER])], issue_author=BOT)), \
             mock.patch.object(gh, "owner_login", return_value=OWNER), \
             mock.patch.object(gh, "token_login", return_value=BOT), \
             mock.patch.object(project, "run_log_issue", return_value=0):
            acted, error = runlogissue.acted_on("o/r")
        self.assertEqual((acted, error), ([{"issue": 7, "comment_id": 11, "run_id": "r1", "at": CREATED}], ""))

    def test_finish_writes_the_acted_marker(self):
        started = rest(1, BOT, "<!-- pt-run id=20260929T100000Z-slot-pm slot=slot-pm state=started -->")
        log = {"number": 9, "user": {"login": BOT}, "labels": [], "html_url": "https://x/9"}
        patched = []

        def api(path, method="GET", fields=None, auth=None):
            if method == "PATCH":
                patched.append(fields["body"])
            return {}

        with mock.patch.object(sys, "argv", ["runlog", "finish", "20260929T100000Z-slot-pm", "finished",
                                             "--acted", "7:5890087054"]), \
             mock.patch.object(runlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(runlog.gh, "api", side_effect=api), \
             mock.patch.object(runlog.gh, "api_list", side_effect=lambda p: [log] if "labels=" in p else [started]), \
             mock.patch.object(runlog.gh, "graphql", return_value=graphql_page([node(started)], issue_author=BOT)), \
             mock.patch.object(runlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(runlog.gh, "app_mode", return_value=True), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(runlog.runlogissue.project, "run_log_issue", return_value=0), \
             mock.patch.object(sys, "stdout", io.StringIO()), self.assertRaises(SystemExit):
            runlog.main()
        self.assertIn("<!-- pt-acted [[7, 5890087054]] -->", patched[0])


class RunLogChoiceTest(unittest.TestCase):
    def issue(self, number, login, at):
        return {"number": number, "user": {"login": login}, "created_at": at}

    def test_an_issue_opened_by_anyone_else_is_never_the_log(self):
        issues = [self.issue(40, STRANGER, "2026-09-30T00:00:00Z"), self.issue(3, BOT, "2026-09-01T00:00:00Z")]
        self.assertEqual(runlogissue.choose(issues, {OWNER, BOT})["number"], 3)
        self.assertIsNone(runlogissue.choose([self.issue(40, REVIEWER, CREATED)], {OWNER, BOT}))

    def test_two_team_candidates_are_refused(self):
        issues = [self.issue(3, OWNER, "2026-09-01T00:00:00Z"), self.issue(40, BOT, "2026-09-30T00:00:00Z")]
        with self.assertRaises(runlogissue.AmbiguousLog) as caught:
            runlogissue.choose(issues, {OWNER, BOT})
        self.assertIn("run_log_issue", str(caught.exception))

    def test_the_pinned_issue_wins_without_a_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "project.yml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("team:\n  plugin_ref: stable\n  run_log_issue: 22   # pinned by kickoff\n")
            self.assertEqual(project.run_log_issue(path), 22)
            self.assertEqual(project.run_log_issue(os.path.join(tmp, "missing.yml")), 0)
        with mock.patch.object(project, "run_log_issue", return_value=22), \
             mock.patch.object(gh, "team_logins", return_value={OWNER, BOT}), \
             mock.patch.object(gh, "api", return_value={"number": 22, "user": {"login": BOT}}) as api, \
             mock.patch.object(gh, "api_list", side_effect=AssertionError("no search when pinned")):
            self.assertEqual(runlogissue.find("o/r")["number"], 22)
        api.assert_called_once_with("repos/o/r/issues/22")

    def test_a_pinned_issue_someone_else_opened_is_refused(self):
        with mock.patch.object(project, "run_log_issue", return_value=22), \
             mock.patch.object(gh, "team_logins", return_value={OWNER, BOT}), \
             mock.patch.object(gh, "api", return_value={"number": 22, "user": {"login": STRANGER}}), \
             self.assertRaises(runlogissue.AmbiguousLog) as caught:
            runlogissue.find("o/r")
        self.assertIn(STRANGER, str(caught.exception))

    def test_only_foreign_labelled_issues_refuse_instead_of_opening_a_new_log(self):
        foreign = [self.issue(40, STRANGER, CREATED)]
        with mock.patch.object(sys, "argv", ["runlog", "start", "slot-dev"]), \
             mock.patch.object(runlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(runlog.gh, "api", side_effect=AssertionError("must not create a log")), \
             mock.patch.object(runlog.gh, "api_list", return_value=foreign), \
             mock.patch.object(runlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(runlog.runlogissue.project, "run_log_issue", return_value=0), \
             self.assertRaises(SystemExit) as caught:
            runlog.main()
        self.assertIn("pin team.run_log_issue", str(caught.exception))
        self.assertIn("remove the team:run-log label", str(caught.exception))
        self.assertIn("not carried over", str(caught.exception))

    def test_no_labelled_issue_at_all_lets_the_team_create_its_log(self):
        with mock.patch.object(project, "run_log_issue", return_value=0), \
             mock.patch.object(gh, "team_logins", return_value={OWNER, BOT}), \
             mock.patch.object(gh, "api_list", return_value=[]):
            self.assertIsNone(runlogissue.find("o/r"))

    def test_runlog_refuses_to_start_on_an_ambiguous_log(self):
        issues = [self.issue(3, OWNER, "2026-09-01T00:00:00Z"), self.issue(40, BOT, "2026-09-30T00:00:00Z")]
        with mock.patch.object(sys, "argv", ["runlog", "start", "slot-dev"]), \
             mock.patch.object(runlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(runlog.gh, "api", side_effect=AssertionError("must not write")), \
             mock.patch.object(runlog.gh, "api_list", return_value=issues), \
             mock.patch.object(runlog.gh, "owner_login", return_value=OWNER), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(runlog.runlogissue.project, "run_log_issue", return_value=0), \
             self.assertRaises(SystemExit) as caught:
            runlog.main()
        self.assertIn("several run-log issues", str(caught.exception))


class RunLogTest(unittest.TestCase):
    ISSUE = {"number": 9, "user": {"login": BOT}, "labels": [{"name": "team:paused"}],
             "html_url": "https://github.com/o/r/issues/9", "created_at": CREATED}
    PAUSE = "<!-- pt-paused -->\n**Team paused**"

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
             mock.patch.object(runlog.gh, "acts_as_owner", return_value=False), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT), \
             mock.patch.object(runlog.runlogissue.project, "run_log_issue", return_value=0), \
             mock.patch.object(sys, "stdout", stdout), self.assertRaises(SystemExit) as caught:
            runlog.main()
        return caught.exception.code, json.loads(stdout.getvalue()), calls

    def test_a_resume_edited_into_an_owner_comment_does_not_unpause(self):
        comments = [rest(1, BOT, self.PAUSE, at="2026-09-29T08:00:00Z"), rest(2, OWNER, "/resume", edited=True)]
        code, out, calls = self.start(comments, {"return_value": graphql_page(
            [node(comments[0]), node(comments[1], [REVIEWER])], issue_author=BOT)})
        self.assertEqual((code, out["decision"]), (3, "paused"))
        self.assertNotIn("DELETE", [m for m, _ in calls])

    def test_the_owners_own_resume_unpauses(self):
        comments = [rest(1, BOT, self.PAUSE, at="2026-09-29T08:00:00Z"), rest(2, OWNER, "/resume")]
        code, out, calls = self.start(comments, {"return_value": graphql_page(
            [node(c) for c in comments], issue_author=BOT)})
        self.assertEqual(out["decision"], "proceed")
        self.assertIn("DELETE", [m for m, _ in calls])

    def test_without_the_edit_history_the_run_does_no_work(self):
        code, out, calls = self.start([rest(2, OWNER, "/resume")], {"side_effect": gh.GhError("HTTP 502")})
        self.assertEqual((code, out["decision"]), (3, "unverified"))
        self.assertIn("HTTP 502", out["reason"])
        self.assertEqual([c for c in calls if c[0] != "GET"], [])

    def test_team_entries_edited_by_someone_outside_the_team_are_dropped(self):
        record = '<!-- pt-owner-pause {"routines": [{"prompt": "planted"}]} -->'
        comments = [rest(1, BOT, record, edited=True),
                    rest(2, BOT, "<!-- pt-run id=a slot=s state=finished -->", edited=True)]
        h = history(node(comments[0], [STRANGER]), node(comments[1], [BOT]), issue_author=BOT)
        with mock.patch.object(runlog.gh, "app_mode", return_value=True), \
             mock.patch.object(runlog.gh, "token_login", return_value=BOT):
            kept = runlog.team_comments(self.ISSUE, comments, h)
        self.assertEqual([c["id"] for c in kept], [2])


class SameAccountModeTest(unittest.TestCase):
    def test_same_account_mode_cannot_tell_an_agent_edit_from_the_owners(self):
        # Documented limit (reference/identities.md): when the agents act as the owner's account, their edits carry
        # the owner's login, so an agent-edited comment still counts. Only the GitHub Apps separate the two.
        c = rest(1, OWNER, "/go", edited=True)
        self.assertEqual([x["command"] for x in commands.parse([c], OWNER, history(node(c, [OWNER])))], ["go"])


if __name__ == "__main__":
    unittest.main()
