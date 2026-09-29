import base64
import contextlib
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
loader = importlib.machinery.SourceFileLoader("pr_cli", str(ROOT / "scripts" / "pr"))
spec = importlib.util.spec_from_loader("pr_cli", loader)
pr = importlib.util.module_from_spec(spec)
loader.exec_module(pr)


def pull(head_repo="o/r"):
    return {"head": {"repo": {"full_name": head_repo} if head_repo else None}}


class DeleteBranchTest(unittest.TestCase):
    def check(self, head, base="dev", head_repo="o/r"):
        with mock.patch.object(pr.gh, "api", return_value=pull(head_repo)):
            return pr.branch_deletion_blocker("o/r", 1, {"head": head, "base": base})

    def test_feature_branch_of_this_repo_is_deleted(self):
        self.assertEqual(self.check("feature/7-x"), "")

    def test_long_lived_branches_are_kept(self):
        for head, base in (("dev", "stage"), ("stage", "main"), ("main", "stage")):
            with self.subTest(head=head):
                self.assertIn("long-lived", self.check(head, base))

    def test_fork_branches_are_never_deleted_here(self):
        self.assertIn("lives in", self.check("feature/7-x", head_repo="someone/r"))
        self.assertIn("deleted fork", self.check("feature/7-x", head_repo=None))


class MergeMethodTest(unittest.TestCase):
    def test_method_is_required(self):
        with mock.patch.object(sys, "argv", ["pr", "merge", "5"]), self.assertRaises(SystemExit):
            pr.main()

    def test_review_must_name_the_reviewed_commit(self):
        with mock.patch.object(sys, "argv", ["pr", "review", "5", "--body", "QA: APPROVED"]), self.assertRaises(SystemExit):
            pr.main()


def verdict(body, login, sha="head1"):
    return {"body": body, "commit_id": sha, "submitted_at": "2026-09-29T10:00:00Z", "user": {"login": login}}


class GateIdentityTest(unittest.TestCase):
    def gate(self, reviews, logins=(), review_bot=None, same_account=False):
        with mock.patch.object(pr, "view", return_value={"head_sha": "head1"}), \
             mock.patch.object(pr, "security_check", return_value=[]), \
             mock.patch.object(pr, "ci", return_value={"state": "pass", "failing": [], "pending": []}), \
             mock.patch.object(pr.gh, "api_list", return_value=reviews), \
             mock.patch.object(pr.project, "reviewer_logins", return_value=list(logins)), \
             mock.patch.object(pr.gh, "review_login", return_value=review_bot), \
             mock.patch.object(pr.gh, "acts_as_owner", return_value=same_account):
            return pr.gate("o/r", 5)

    def test_with_the_review_app_only_its_bot_can_pass_the_gate(self):
        forged = [verdict("QA: APPROVED", "acme-team[bot]"), verdict("REVIEW: APPROVED", "geeera")]
        result = self.gate(forged, review_bot="acme-review[bot]")
        self.assertFalse(result["passed"])
        self.assertEqual(result["reviewers"], ["acme-review[bot]"])
        genuine = [verdict("QA: APPROVED", "acme-review[bot]"), verdict("REVIEW: APPROVED", "acme-review[bot]")]
        self.assertTrue(self.gate(forged + genuine, review_bot="acme-review[bot]")["passed"])
        self.assertNotIn("warning", self.gate(genuine, review_bot="acme-review[bot]"))

    def test_bot_login_in_project_yml_works_without_the_review_key(self):
        genuine = [verdict("QA: APPROVED", "acme-review[bot]"), verdict("REVIEW: APPROVED", "acme-review[bot]")]
        self.assertTrue(self.gate(genuine, logins=["acme-review[bot]"])["passed"])

    def test_same_account_warning_only_when_it_is_same_account(self):
        reviews = [verdict("QA: APPROVED", "x"), verdict("REVIEW: APPROVED", "x")]
        self.assertIn("same-account mode", self.gate(reviews, same_account=True)["warning"])
        warning = self.gate(reviews, same_account=False)["warning"]
        self.assertNotIn("same-account", warning)
        self.assertIn("no reviewing identity", warning)


SECRET = "ghs_S3CRETtoken"


class PushTest(unittest.TestCase):
    @contextlib.contextmanager
    def patched(self, app=True, returncode=0, stderr=b""):
        """git and GitHub replaced by recorders; yields the list of (argv, kwargs) git was called with."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return mock.Mock(returncode=returncode, stdout=b"", stderr=stderr)

        def fake_git(*args, cwd="."):
            calls.append((["git", *args], {}))
            return b"abc123\n"

        with mock.patch.object(pr.gh, "app_mode", return_value=app), \
             mock.patch.object(pr.gh, "token", return_value=SECRET), \
             mock.patch.object(pr.gh, "token_login", return_value="acme-team[bot]"), \
             mock.patch.object(pr.gh, "API", "https://api.github.com"), \
             mock.patch.object(pr, "git", side_effect=fake_git), \
             mock.patch.object(pr.subprocess, "run", side_effect=fake_run):
            yield calls

    def run_push(self, app=True, branch="feature/7-x", returncode=0, stderr=b""):
        with self.patched(app, returncode, stderr) as calls:
            return pr.push("o/r", branch), calls

    def test_app_push_keeps_the_token_out_of_argv_config_and_output(self):
        result, calls = self.run_push()
        argv, kwargs = calls[-1]
        header_value = base64.b64encode(f"x-access-token:{SECRET}".encode()).decode()
        self.assertEqual(argv, ["git", "push", "https://github.com/o/r.git", "refs/heads/feature/7-x:refs/heads/feature/7-x"])
        for arg in argv:
            self.assertNotIn(SECRET, arg)
            self.assertNotIn(header_value, arg)
        self.assertNotIn("config", argv)
        self.assertNotIn("-c", argv)
        env = kwargs["env"]
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "http.https://github.com/.extraheader")
        self.assertEqual(env["GIT_CONFIG_VALUE_1"], f"AUTHORIZATION: basic {header_value}")
        self.assertEqual((env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]), (env["GIT_CONFIG_KEY_1"], ""))
        self.assertEqual((env["GIT_CONFIG_KEY_2"], env["GIT_CONFIG_VALUE_2"]), ("credential.helper", ""))
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(result, {"branch": "feature/7-x", "sha": "abc123", "as": "acme-team[bot]"})

    def test_cli_output_never_contains_the_token(self):
        stdout = io.StringIO()
        with self.patched(), mock.patch.object(sys, "argv", ["pr", "push"]), \
             mock.patch.object(pr.gh, "repo", return_value="o/r"), mock.patch.object(sys, "stdout", stdout):
            pr.main()  # no --branch: the current branch, from `git rev-parse` (abc123 in the fake)
        self.assertNotIn(SECRET, stdout.getvalue())
        self.assertIn("acme-team[bot]", stdout.getvalue())

    def test_a_failed_push_scrubs_the_token_from_the_error(self):
        with self.assertRaises(pr.gh.GhError) as caught:
            self.run_push(returncode=128, stderr=f"fatal: Authentication failed for x-access-token:{SECRET}".encode())
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIn("***", str(caught.exception))

    def test_never_pushes_long_lived_branches(self):
        for branch in ("dev", "stage", "main"):
            with self.subTest(branch=branch), self.assertRaisesRegex(pr.gh.GhError, "merged PR"):
                self.run_push(branch=branch)

    def test_without_the_team_app_it_is_a_plain_push(self):
        result, calls = self.run_push(app=False)
        self.assertEqual(calls[-1][0], ["git", "push", "-u", "origin", "refs/heads/feature/7-x:refs/heads/feature/7-x"])
        self.assertEqual(result["sha"], "abc123")

    def test_never_forces(self):
        _, calls = self.run_push()
        self.assertFalse([a for argv, _ in calls for a in argv if a.startswith("--force") or a == "-f" or a.startswith("+")])


class GitIdentityTest(unittest.TestCase):
    def test_prints_bot_author_and_committer_exports(self):
        ident = {"login": "acme-team[bot]", "id": 9001, "name": "acme-team[bot]",
                 "email": "9001+acme-team[bot]@users.noreply.github.com"}
        with mock.patch.object(pr.gh, "app_mode", return_value=True), \
             mock.patch.object(pr.gh, "app_identity", return_value=ident):
            lines = pr.git_identity_lines()
        self.assertEqual(lines, [
            "export GIT_AUTHOR_NAME='acme-team[bot]'",
            "export GIT_AUTHOR_EMAIL='9001+acme-team[bot]@users.noreply.github.com'",
            "export GIT_COMMITTER_NAME='acme-team[bot]'",
            "export GIT_COMMITTER_EMAIL='9001+acme-team[bot]@users.noreply.github.com'",
        ])

    def test_nothing_without_a_team_app(self):
        with mock.patch.object(pr.gh, "app_mode", return_value=False):
            self.assertEqual(pr.git_identity_lines(), [])


if __name__ == "__main__":
    unittest.main()
