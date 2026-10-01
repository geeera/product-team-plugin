"""The plugin in a Claude Code cloud session: every GraphQL call answers HTTP 403, only REST works.

Scheduled runs of geeera/team-console failed from 0.10.1 to 0.10.2 because `runlog start` refused to work without
the GraphQL edit history. These tests drive the real scripts through a stateful fake of GitHub's REST API with the
GraphQL endpoint blocked the way the cloud blocks it, and check that every step of a slot still works and still
fails closed: edited entries and edited owner commands are untrusted, unedited ones count.
"""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import importlib.machinery
import importlib.util
import io
import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ptlib import gh, project, runstate  # noqa: E402

OWNER, BOT, STRANGER = "geeera", "acme-team[bot]", "collaborator"
REPO = "o/r"
BLOCKED = ('POST https://api.github.com/graphql → HTTP 403: {"message":"GitHub GraphQL is not available from '
           'Claude Code sessions; use the REST API"}')


def load(name):
    loader = importlib.machinery.SourceFileLoader(f"{name}_cloud_cli", str(ROOT / "scripts" / name))
    spec = importlib.util.spec_from_loader(f"{name}_cloud_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


runlog, backlog, inbox, brief = load("runlog"), load("backlog"), load("inbox"), load("brief")


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    """The test's wall clock: every step of a slot happens a minute after the previous one, never in the same
    second (run ids and `created_at` carry whole seconds)."""

    def __init__(self):
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def tick(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


class FakeGitHub:
    """Enough of the Issues REST API for one repository; the GraphQL endpoint answers the cloud's 403."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.issues, self.comments, self.next_id = {}, [], 100
        self.graphql_calls, self.writes = 0, []

    def create_issue(self, login, title, labels=(), body="", at=None):
        number = len(self.issues) + 1
        self.issues[number] = {"number": number, "title": title, "body": body, "state": "open", "node_id": f"I_{number}",
                               "user": {"login": login}, "labels": [{"name": l} for l in labels],
                               "html_url": f"https://github.com/{REPO}/issues/{number}",
                               "created_at": at or iso(self.clock.tick())}
        return self.issues[number]

    def comment(self, number, login, body, at=None, edited=False):
        cid, self.next_id = self.next_id, self.next_id + 1
        at = at or iso(self.clock.tick())
        c = {"id": cid, "issue": number, "user": {"login": login}, "body": body, "created_at": at,
             "updated_at": iso(self.clock.now + timedelta(minutes=5)) if edited else at,
             "html_url": f"https://github.com/{REPO}/issues/{number}#issuecomment-{cid}"}
        self.comments.append(c)
        return c

    def api(self, path, method="GET", fields=None, auth=None):
        if path == "graphql":
            self.graphql_calls += 1
            raise gh.GhError(BLOCKED)
        if method != "GET":
            self.writes.append((method, path))
        m = re.fullmatch(rf"repos/{REPO}/issues/(\d+)(/.*)?", path)
        if path == f"repos/{REPO}/issues" and method == "POST":
            return self.create_issue(BOT, fields["title"], fields.get("labels", []), fields.get("body", ""))
        if not m:
            raise AssertionError(f"unexpected {method} {path}")
        issue, rest = self.issues[int(m.group(1))], m.group(2) or ""
        if rest == "" and method == "GET":
            return issue
        if rest == "" and method == "PATCH":
            issue.update({k: v for k, v in fields.items() if k != "labels"})
            if "labels" in fields:
                issue["labels"] = [{"name": l} for l in fields["labels"]]
            return issue
        if rest == "/comments" and method == "POST":
            return self.comment(issue["number"], BOT, fields["body"])
        if rest == "/labels" and method == "POST":
            issue["labels"] += [{"name": l} for l in fields["labels"] if l not in {x["name"] for x in issue["labels"]}]
            return issue["labels"]
        if rest.startswith("/labels/") and method == "DELETE":
            name = rest[len("/labels/"):].replace("%3A", ":")
            issue["labels"] = [l for l in issue["labels"] if l["name"] != name]
            return None
        raise AssertionError(f"unexpected {method} {path}")

    def api_list(self, path):
        m = re.fullmatch(rf"repos/{REPO}/issues/(\d+)/comments\?.*", path)
        if m:
            return [c for c in self.comments if c["issue"] == int(m.group(1))]
        m = re.fullmatch(rf"repos/{REPO}/issues\?(.*)", path)
        if m:
            query = dict(p.split("=", 1) for p in m.group(1).split("&"))
            wanted = set(query.get("labels", "").split(",")) - {""}
            state = query.get("state", "open")
            return [i for i in self.issues.values() if (state == "all" or i["state"] == state)
                    and wanted <= {l["name"] for l in i["labels"]}]
        if path.startswith(f"repos/{REPO}/milestones") or path.startswith(f"repos/{REPO}/pulls"):
            return []
        raise AssertionError(f"unexpected list {path}")


class CloudSessionTest(unittest.TestCase):
    """One scheduled slot after another, with GraphQL blocked for the whole process."""

    def setUp(self):
        gh._graphql_unavailable = None
        self.addCleanup(setattr, gh, "_graphql_unavailable", None)
        self.clock = Clock()
        self.github = FakeGitHub(self.clock)
        self.log = self.github.create_issue(BOT, "Team run log", ["team:run-log"])
        self.now = self.clock.now

    def run_cli(self, module, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        clock = self.clock

        class ScriptClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.tick().astimezone(tz) if tz else clock.tick()

        with mock.patch.object(sys, "argv", [module.__name__, *argv]), \
             mock.patch.object(module, "datetime", ScriptClock), \
             mock.patch.object(gh, "api", side_effect=self.github.api), \
             mock.patch.object(gh, "api_list", side_effect=self.github.api_list), \
             mock.patch.object(gh, "owner_login", return_value=OWNER), \
             mock.patch.object(gh, "token_login", return_value=BOT), \
             mock.patch.object(gh, "app_mode", return_value=True), \
             mock.patch.object(gh, "acts_as_owner", return_value=False), \
             mock.patch.object(gh, "repo", return_value=REPO), \
             mock.patch.object(project, "run_log_issue", return_value=0), \
             mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
            try:
                module.main()
                code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                if not isinstance(exc.code, int) and exc.code:
                    stderr.write(str(exc.code))
        text = stdout.getvalue().strip()
        try:
            return code, json.loads(text) if text else None, stderr.getvalue()
        except ValueError:
            return code, text, stderr.getvalue()

    def start(self, slot="slot-dev"):
        return self.run_cli(runlog, "start", slot)

    def finish(self, run_id, state="finished", *extra):
        return self.run_cli(runlog, "finish", run_id, state, *extra)

    def log_entries(self):
        return [c["body"] for c in self.github.comments if c["issue"] == self.log["number"]]

    def test_start_proceeds_in_rest_only_mode_and_graphql_is_asked_once_per_process(self):
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"], out["history"]), (0, "proceed", "rest-only"))
        self.assertIn("GraphQL is not available from Claude Code sessions", out["history_error"])
        self.assertEqual(self.github.graphql_calls, 1)
        self.start("slot-pm")
        self.assertEqual(self.github.graphql_calls, 1)

    def test_finish_appends_and_the_next_start_sees_no_overlap(self):
        _, started, _ = self.start()
        code, out, _ = self.finish(started["run_id"], "finished", "--metric", "prs=2", "--acted", "7:5")
        self.assertEqual((code, out["state"], out["history"]), (0, "finished", "rest-only"))
        self.assertEqual([("POST", f"repos/{REPO}/issues/1/comments")] * 2, self.github.writes)
        entries = self.log_entries()
        self.assertEqual(len(entries), 2)
        self.assertIn(f"id={started['run_id']} slot=slot-dev state=started", entries[0])
        self.assertIn(f"id={started['run_id']} slot=slot-dev state=finished", entries[1])
        self.assertIn("<!-- pt-acted [[7, 5]] -->", entries[1])
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (0, "proceed"))
        self.assertNotEqual(out["run_id"], started["run_id"])
        _, status, _ = self.run_cli(runlog, "status")
        self.assertEqual([(r["id"], r["state"]) for r in status["runs"]],
                         [(started["run_id"], "finished"), (out["run_id"], "started")])

    def test_a_start_in_the_same_second_as_a_previous_run_gets_its_own_id(self):
        # Entries merge by run id, so a shared id would fold a new `started` into the finished run before it.
        runs = runstate.parse_runs([entry for entry in [
            {"id": 1, "created_at": "2026-10-01T09:00:00Z", "body": "<!-- pt-run id=20261001T090000Z-slot-dev slot=slot-dev state=finished -->"}]])
        at = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(runlog.unique_run_id(at, "slot-dev", runs), "20261001T090001Z-slot-dev")
        self.assertEqual(runlog.unique_run_id(at, "slot-pm", runs), "20261001T090000Z-slot-pm")

    def test_a_run_still_in_progress_is_an_overlap_even_without_graphql(self):
        _, first, _ = self.start()
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (3, "overlap"))
        self.assertIn(first["run_id"], out["reason"])

    def test_three_failed_runs_pause_and_only_the_owners_unedited_resume_lifts_it(self):
        for n in range(3):
            run_id = f"{iso(self.now - timedelta(hours=3 - n)).replace('-', '').replace(':', '')}-slot-dev"
            self.github.comment(1, BOT, f"<!-- pt-run id={run_id} slot=slot-dev state=started -->",
                                at=iso(self.now - timedelta(hours=3 - n, minutes=30)))
            self.github.comment(1, BOT, f"<!-- pt-run id={run_id} slot=slot-dev state=failed -->",
                                at=iso(self.now - timedelta(hours=3 - n, minutes=20)))
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (3, "pause"))
        self.assertIn("team:paused", {l["name"] for l in self.log["labels"]})
        self.assertIn(runstate.PAUSE_MARKER, self.log_entries()[-1])

        forged = self.github.comment(1, OWNER, "/resume", edited=True)
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (3, "paused"))
        self.assertIn("team:paused", {l["name"] for l in self.log["labels"]})
        _, answers, _ = self.run_cli(backlog, "answers", "1")
        self.assertEqual(answers["commands"], [])
        reason = answers["ignored"][0]["reason"]
        self.assertEqual(answers["ignored"][0]["comment_id"], forged["id"])
        self.assertIn("could not be fetched", reason)
        self.assertIn("write the command again in a new comment", reason)

        self.github.comment(1, OWNER, "/resume")
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (0, "proceed"))
        self.assertNotIn("team:paused", {l["name"] for l in self.log["labels"]})

    def test_owner_pause_record_and_resume_work_without_graphql(self):
        with mock.patch("builtins.open", mock.mock_open(read_data='{"routines": [{"id": "trig_1"}]}')):
            code, out, _ = self.run_cli(runlog, "pause", "--record-file", "rec.json", "--reason", "holiday")
        self.assertEqual((code, out["paused"]), (0, True))
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (3, "paused"))
        _, record, _ = self.run_cli(runlog, "pause-record")
        self.assertEqual(record["routines"], [{"id": "trig_1"}])
        # A pause record someone edited (routine prompts could be planted) is untrusted: no record to resume from.
        self.github.comments[-1]["updated_at"] = iso(self.now + timedelta(minutes=9))
        _, record, _ = self.run_cli(runlog, "pause-record")
        self.assertIsNone(record)
        code, out, _ = self.run_cli(runlog, "resume")
        self.assertEqual((code, out["resumed"]), (0, True))
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (0, "proceed"))

    def test_old_patched_entries_are_invisible_and_block_nothing(self):
        # The log as team-console #22 looks after 0.10.0–0.10.2: finish rewrote the started comment in place.
        old = self.github.comment(1, BOT, "<!-- pt-run id=20260925T200000Z-slot-dev slot=slot-dev state=finished -->"
                                  "\n<!-- pt-metrics {\"minutes\": 40} -->", at="2026-09-25T20:00:00Z", edited=True)
        self.github.comment(1, BOT, "<!-- pt-run id=20260926T200000Z-slot-dev slot=slot-dev state=started -->",
                            at="2026-09-26T20:00:00Z", edited=True)
        for day in (27, 28, 29):  # runs that died on the usage limit: never edited, trusted, counted as failed
            self.github.comment(1, BOT, f"<!-- pt-run id=202609{day}T200000Z-slot-dev slot=slot-dev state=started -->",
                                at=f"2026-09-{day}T20:00:00Z")
        _, status, _ = self.run_cli(runlog, "status")
        self.assertEqual([(r["state"], r["effective"]) for r in status["runs"]], [("started", "failed")] * 3)
        self.assertNotIn(old["id"], [r["comment_id"] for r in status["runs"]])
        self.assertEqual(status["history"], "rest-only")
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (3, "pause"))  # three dead runs in a row are a real streak
        self.github.comment(1, OWNER, "/resume")
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (0, "proceed"))
        _, stats, _ = self.run_cli(runlog, "stats", "--days", "30")
        self.assertEqual((stats["slots"]["slot-dev"]["runs"], stats["slots"]["slot-dev"]["unknown"]), (4, 0))
        self.assertEqual(stats["slots"]["slot-dev"]["median_minutes"], None)  # nothing read from edited entries

    def test_a_forged_edit_with_the_in_progress_run_id_keeps_the_overlap(self):
        _, started, _ = self.start()
        # A team entry someone rewrote to carry the running id and say it finished: untrusted over REST.
        self.github.comment(1, BOT, f"<!-- pt-run id={started['run_id']} slot=slot-dev state=finished -->", edited=True)
        code, out, _ = self.start()
        self.assertEqual((code, out["decision"]), (3, "overlap"))
        self.assertIn(started["run_id"], out["reason"])

    def test_backlog_owner_commands_unedited_count_edited_do_not(self):
        issue = self.github.create_issue(BOT, "Use R2?", ["kind:question", "owner:money"])
        genuine = self.github.comment(issue["number"], OWNER, "/approve")
        forged = self.github.comment(issue["number"], OWNER, "/reject never", edited=True)
        _, answers, _ = self.run_cli(backlog, "answers", str(issue["number"]))
        self.assertEqual([(c["command"], c["comment_id"]) for c in answers["commands"]], [("approve", genuine["id"])])
        self.assertEqual([(i["kind"], i["comment_id"]) for i in answers["ignored"]], [("edited", forged["id"])])
        self.assertEqual(answers["history"], "rest-only")
        self.assertIn("GraphQL is not available", answers["history_error"])
        # REST cannot date a body edit (updated_at moves with every label): the body is never the owner's here.
        self.assertFalse(answers["body"]["owner_statement"])

    def test_vanished_reads_the_appended_log_in_rest_only_mode(self):
        issue = self.github.create_issue(BOT, "Use R2?", ["kind:question"])
        gone = self.github.comment(issue["number"], OWNER, "/approve")
        _, started, _ = self.start()
        self.finish(started["run_id"], "finished", "--acted", f"{issue['number']}:{gone['id']}")
        self.github.comments.remove(gone)
        code, out, _ = self.run_cli(backlog, "vanished")
        self.assertEqual((code, out["error"], out["history"]), (0, "", "rest-only"))
        self.assertEqual([(v["issue"], v["comment_id"]) for v in out["vanished"]], [(issue["number"], gone["id"])])

    def test_inbox_update_creates_the_issue_and_survives_a_failed_pin(self):
        self.github.create_issue(BOT, "Pick a CDN", ["kind:question", "owner:money"],
                                 body="**Your answer:** /approve or /reject why\n<!-- pt-ask -->\n")
        code, out, err = self.run_cli(inbox, "update")
        self.assertEqual((code, out["pinned"]), (0, False))
        self.assertIn("could not pin", err)
        self.assertIn("GraphQL is not available", err)
        created = self.github.issues[out["number"]]
        self.assertIn("team:inbox", {l["name"] for l in created["labels"]})
        self.assertIn("#2 ", created["body"])
        code, again, _ = self.run_cli(inbox, "update")
        self.assertEqual((code, again["number"], again["pinned"]), (0, out["number"], None))

    def test_brief_mark_appends_and_the_latest_mark_is_read_back(self):
        for at in ("2026-09-29T10:00:00Z", "2026-09-30T10:00:00Z"):
            code, out, _ = self.run_cli(brief, "mark", "--at", at)
            self.assertEqual((code, out["briefed_at"]), (0, at))
        self.assertEqual([w for w in self.github.writes if w[0] == "PATCH"], [])
        code, out, _ = self.run_cli(brief, "show")
        self.assertEqual((code, out["since"]), (0, "2026-09-30T10:00:00Z"))
        self.assertEqual(out["runs"], {"total": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
