try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


RESOLVED = json.loads((ROOT / "tests/contract/fixtures/team-config/resolved.job.json").read_text())["response"]["body"]
ALWAYS = {"qa": "always", "reviewer": "always"}
PROPORTIONAL_GATES = {"qa": "always", "reviewer": "code", "code_paths": ["apps/**", "libs/**"]}


def team_view(**gates):
    """The vendored `resolved.job` view with its `gates` overridden."""
    view = json.loads(json.dumps(RESOLVED))
    view["gates"].update(gates)
    return view


def run_gate(reviews, config="", files=("apps/web/page.tsx",), security=(), review_bot=None, same_account=False,
             team_bot=None, ci_state="pass", gates=None):
    """pr.gate with GitHub and the console mocked: `config` is project.yml on the base branch, `gates` the console's
    resolved gates (always/always by default), `files` the PR's changed paths."""
    with mock.patch.object(pr.teamconfig, "load", return_value=team_view(**(gates or ALWAYS))), \
         mock.patch.object(pr, "view", return_value={"head_sha": "head1", "base": "dev"}), \
         mock.patch.object(pr, "changed_files", return_value=list(files)), \
         mock.patch.object(pr, "security_check", return_value=list(security)), \
         mock.patch.object(pr, "ci", return_value={"state": ci_state, "failing": [], "pending": []}), \
         mock.patch.object(pr.gh, "api_list", return_value=reviews), \
         mock.patch.object(pr, "base_project_text", return_value=config) as base, \
         mock.patch.object(pr.gh, "review_login", return_value=review_bot), \
         mock.patch.object(pr.gh, "app_mode", return_value=team_bot is not None), \
         mock.patch.object(pr.gh, "token_login", return_value=team_bot), \
         mock.patch.object(pr.gh, "acts_as_owner", return_value=same_account):
        result = pr.gate("o/r", 5)
    base.assert_called_once_with("o/r", "dev")
    return result


def logins_yml(logins):
    return "team:\n  reviewer_logins: [%s]\n" % ", ".join(f"'{x}'" for x in logins) if logins else ""


class GateIdentityTest(unittest.TestCase):
    def gate(self, reviews, logins=(), review_bot=None, same_account=False, team_bot=None):
        return run_gate(reviews, logins_yml(logins), review_bot=review_bot, same_account=same_account,
                        team_bot=team_bot)

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

    def test_team_bot_listed_as_reviewer_fails_the_gate(self):
        own = [verdict("QA: APPROVED", "acme-team[bot]"), verdict("REVIEW: APPROVED", "acme-team[bot]")]
        result = self.gate(own, logins=["acme-team[bot]"], team_bot="acme-team[bot]")
        self.assertFalse(result["passed"])
        self.assertIn("team's own bot", result["missing"][0])

    def test_review_bot_equal_to_team_bot_fails_the_gate(self):
        own = [verdict("QA: APPROVED", "acme-team[bot]"), verdict("REVIEW: APPROVED", "acme-team[bot]")]
        result = self.gate(own, review_bot="acme-team[bot]", team_bot="acme-team[bot]")
        self.assertFalse(result["passed"])
        self.assertIn("same bot", result["missing"][0])

    def test_same_account_warning_only_when_it_is_same_account(self):
        reviews = [verdict("QA: APPROVED", "x"), verdict("REVIEW: APPROVED", "x")]
        self.assertIn("same-account mode", self.gate(reviews, same_account=True)["warning"])
        warning = self.gate(reviews, same_account=False)["warning"]
        self.assertNotIn("same-account", warning)
        self.assertIn("no reviewing identity", warning)


class BaseProjectTextTest(unittest.TestCase):
    def test_reads_project_yml_from_the_base_branch_not_the_working_tree(self):
        with mock.patch.object(pr.gh, "raw", return_value="team:\n  reviewer_logins: ['acme-review[bot]']\n") as raw:
            self.assertIn("acme-review[bot]", pr.base_project_text("o/r", "dev"))
        self.assertEqual(raw.call_args[0][0], "repos/o/r/contents/.product-team/project.yml?ref=refs/heads/dev")

    def test_the_base_is_read_as_a_full_branch_ref(self):
        with mock.patch.object(pr.gh, "raw", return_value="") as raw:
            pr.base_project_text("o/r", "release/1.0 x")
        self.assertTrue(raw.call_args[0][0].endswith("?ref=refs/heads/release/1.0%20x"))

    def test_no_project_yml_on_the_base_means_empty_config(self):
        with mock.patch.object(pr.gh, "raw", side_effect=pr.gh.GhError("GET … → HTTP 404: Not Found")):
            self.assertEqual(pr.base_project_text("o/r", "dev"), "")

    def test_other_errors_are_not_swallowed(self):
        with mock.patch.object(pr.gh, "raw", side_effect=pr.gh.GhError("GET … → HTTP 500")):
            with self.assertRaises(pr.gh.GhError):
                pr.base_project_text("o/r", "dev")


PROPORTIONAL = """team:
  reviewer_logins: []
review:
  qa: always
  reviewer: code
  code_paths: ["apps/**", "libs/**"]
  max_rework_rounds: 1
owner:
  language: ru
"""
BOTH = [verdict("QA: APPROVED", "x"), verdict("REVIEW: APPROVED", "x")]
QA_ONLY = [verdict("QA: APPROVED", "x")]


class RequiredVerdictsGateTest(unittest.TestCase):
    """Which verdicts `pr gate` requires: the console's `gates` (ADR 0009 d.11), never project.yml's review block."""

    def test_the_console_gates_decide_qa_and_review(self):
        for files in (["apps/web/page.tsx"], [".github/workflows/ci.yml"], ["docs/setup.md"]):
            with self.subTest(files=files):
                result = run_gate(QA_ONLY, "", files)
                self.assertEqual(result["required"], ["QA", "REVIEW"])
                self.assertEqual(result["missing"], ["REVIEW: no verdict"])
                self.assertEqual((result["policy"]["source"], result["policy"]["revision"]),
                                 ("team config (console, revision 0)", 0))
                self.assertIn("team config (console, revision 0): reviewer: always", result["why"]["REVIEW"]["why"])

    def test_reviewer_code_pr_touching_only_ci_and_docs_needs_no_review(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/setup.md", "README.md", ".github/workflows/ci.yml"],
                          security=["dependencies or CI: .github/workflows/ci.yml"], gates=PROPORTIONAL_GATES)
        self.assertEqual(result["required"], ["QA", "SECURITY"])
        self.assertFalse(result["why"]["REVIEW"]["required"])
        self.assertIn("no changed path matches code_paths (apps/**, libs/**)", result["why"]["REVIEW"]["why"])
        self.assertEqual(result["missing"], ["SECURITY: no verdict"])

    def test_reviewer_code_docs_only_pr_passes_with_qa_alone(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/setup.md"], gates=PROPORTIONAL_GATES)
        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["required"], ["QA"])

    def test_reviewer_code_pr_touching_apps_needs_review(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/setup.md", "apps/web/src/page.tsx"], gates=PROPORTIONAL_GATES)
        self.assertEqual(result["required"], ["QA", "REVIEW"])
        self.assertEqual(result["missing"], ["REVIEW: no verdict"])
        self.assertIn("apps/web/src/page.tsx", result["why"]["REVIEW"]["why"])
        self.assertTrue(run_gate(BOTH, PROPORTIONAL, ["libs/ui/button.ts"], gates=PROPORTIONAL_GATES)["passed"])

    def test_moving_code_out_of_a_code_path_still_needs_review(self):
        # changed_files lists a rename's old path too
        result = run_gate(QA_ONLY, PROPORTIONAL, ["tools/old.ts", "apps/web/old.ts"], gates=PROPORTIONAL_GATES)
        self.assertIn("REVIEW", result["required"])

    def test_qa_and_reviewer_on_code(self):
        gates = {"qa": "code", "reviewer": "code", "code_paths": ["apps/**"]}
        docs = run_gate([], "", ["docs/a.md"], gates=gates)
        self.assertEqual(docs["required"], [])
        self.assertTrue(docs["passed"])
        code = run_gate([], "", ["apps/a.ts"], gates=gates)
        self.assertEqual(code["required"], ["QA", "REVIEW"])

    def test_security_is_required_whatever_the_policy_says(self):
        gates = {"qa": "code", "reviewer": "code", "code_paths": ["apps/**"]}
        result = run_gate([], "", ["server/auth.ts"], security=["sensitive path: server/auth.ts"], gates=gates)
        self.assertEqual(result["required"], ["SECURITY"])
        self.assertTrue(result["why"]["SECURITY"]["required"])
        self.assertIn("auth.ts", result["why"]["SECURITY"]["why"])

    def test_every_verdict_gets_a_reason(self):
        result = run_gate(BOTH, PROPORTIONAL, ["apps/a.ts"], gates=PROPORTIONAL_GATES)
        self.assertEqual(set(result["why"]), {"QA", "REVIEW", "SECURITY"})
        self.assertIn("qa: always", result["why"]["QA"]["why"])
        self.assertFalse(result["why"]["SECURITY"]["required"])
        self.assertEqual(result["policy"]["max_rework_rounds"], 1)

    def test_an_unreadable_review_block_on_the_base_is_only_drift(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = run_gate(BOTH, "review:\n  reviewer: sometimes\n", ["docs/a.md"])
        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["team_config"]["drift"], ["review"])
        self.assertIn("project.yml differs from the console's team config (revision 0) at review", stderr.getvalue())

    def test_ci_still_decides(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/a.md"], ci_state="fail", gates=PROPORTIONAL_GATES)
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing"], ["CI: fail"])

    def test_a_relaxed_review_block_in_project_yml_never_relaxes_the_gate(self):
        # team-console#683: project.yml is documentation on a console-tracker project; any merged PR can edit it.
        relaxed = "review:\n  qa: never\n  reviewer: never\n"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = run_gate([], relaxed, ["docs/a.md"])
        self.assertEqual(result["required"], ["QA", "REVIEW"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["team_config"]["drift"], ["review.code_paths", "review.qa", "review.reviewer"])
        self.assertIn("review.qa, review.reviewer", stderr.getvalue())

    def test_the_gate_refuses_to_judge_outside_a_console_job(self):
        # No TC_* variables (tests/_isolation.py removes them): a local or hub gate on a console-tracker project.
        with mock.patch.object(pr, "view") as viewed, mock.patch.object(pr, "base_project_text") as base:
            result = pr.gate("o/r", 5)
        self.assertFalse(result["passed"])
        self.assertEqual(result["required"], [])
        self.assertEqual(len(result["missing"]), 1)
        self.assertIn("not inside a console job", result["missing"][0])
        self.assertIn("refuses to judge rather than use project.yml's review block", result["missing"][0])
        viewed.assert_not_called()
        base.assert_not_called()

    def test_merge_refuses_outside_a_console_job(self):
        with mock.patch.object(pr.gh, "repo", return_value="o/r"), \
                mock.patch.object(pr, "view", return_value={"head_sha": "head1", "base": "dev", "head": "feature/x"}), \
                mock.patch.object(pr.gh, "api") as api, \
                mock.patch.object(sys, "argv", ["pr", "merge", "5", "--method", "squash"]):
            with self.assertRaises(SystemExit) as caught:
                pr.main()
        self.assertIn("refusing to merge #5: team config: not inside a console job", str(caught.exception.code))
        api.assert_not_called()


SECRET = "ghs_S3CRETtoken"
BASIC = base64.b64encode(f"x-access-token:{SECRET}".encode()).decode()
REAL_RUN = subprocess.run


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class PushTest(unittest.TestCase):
    """A real repository for every git check; only the network step (`git push`) is intercepted."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for args in (["init", "-q", "-b", "feature/7-x"], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                            "commit", "-q", "--allow-empty", "-m", "c"]):
            REAL_RUN(["git", "-C", self.repo, *args], check=True, capture_output=True, env=env)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        self.pushes = []
        self.remote_sha = None  # what GitHub answers after the push; None: the commit that was pushed
        self.ls_remote = (0, b"")

    def git_out(self, *args):
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return REAL_RUN(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                        capture_output=True, env=env).stdout.decode().strip()

    def fake_run(self, argv, **kwargs):
        if argv[:2] == ["git", "push"] or argv[3:4] == ["push"]:
            self.pushes.append((argv, kwargs))
            return mock.Mock(returncode=self.push_returncode, stdout=b"", stderr=self.push_stderr)
        if "ls-remote" in argv and "--get-url" not in argv:  # reading the branch back from GitHub
            code, stderr = self.ls_remote
            sha = self.remote_sha or self.git_out("rev-parse", "HEAD")
            return mock.Mock(returncode=code, stdout=f"{sha}\t{argv[-1]}\n".encode() if not code else b"",
                             stderr=stderr)
        return REAL_RUN(argv, **kwargs)

    def run_push(self, branch="feature/7-x", app=True, returncode=0, stderr=b""):
        self.push_returncode, self.push_stderr = returncode, stderr
        with mock.patch.object(pr.gh, "app_mode", return_value=app), \
             mock.patch.object(pr.gh, "token", return_value=SECRET), \
             mock.patch.object(pr.gh, "token_login", return_value="acme-team[bot]"), \
             mock.patch.object(pr.gh, "API", "https://api.github.com"), \
             mock.patch.object(pr.subprocess, "run", side_effect=self.fake_run):
            return pr.push("o/r", branch)

    def config(self, *args):
        REAL_RUN(["git", "config", *args], check=True, capture_output=True)

    def test_app_push_goes_to_github_with_the_token_only_in_env(self):
        result = self.run_push()
        argv, kwargs = self.pushes[-1]
        self.assertEqual(argv, ["git", "push", "https://github.com/o/r.git",
                                "refs/heads/feature/7-x:refs/heads/feature/7-x"])
        self.assertFalse(any(SECRET in a or BASIC in a for a in argv), "token in argv")
        env = kwargs["env"]
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "http.https://github.com/.extraheader")
        self.assertTrue(env["GIT_CONFIG_VALUE_1"] == f"AUTHORIZATION: basic {BASIC}", "token mismatch")
        self.assertEqual((env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]), (env["GIT_CONFIG_KEY_1"], ""))
        self.assertEqual((env["GIT_CONFIG_KEY_2"], env["GIT_CONFIG_VALUE_2"]), ("credential.helper", ""))
        self.assertEqual((env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_NOSYSTEM"]), (os.devnull, "1"))
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertFalse(SECRET in (Path(self.repo) / ".git" / "config").read_text(), "token leaked")
        self.assertFalse(SECRET in json.dumps(result), "token leaked")
        self.assertEqual(result["as"], "acme-team[bot]")

    def test_insteadof_rewrite_of_github_is_refused_before_pushing(self):
        # A proxy rewrite drops the host-scoped header; the proxy would then push with its own credentials.
        self.config("url.http://127.0.0.1:9/git/.insteadOf", "https://github.com/")
        with self.assertRaisesRegex(pr.gh.GhError, "insteadof rewrites"):
            self.run_push()
        self.assertEqual(self.pushes, [])

    def test_pushinsteadof_rewrite_is_refused_before_pushing(self):
        self.config("url.http://proxy.local/.pushInsteadOf", "https://github.com/o/")
        with self.assertRaisesRegex(pr.gh.GhError, "pushinsteadof rewrites"):
            self.run_push()
        self.assertEqual(self.pushes, [])

    def test_rewrites_of_other_hosts_do_not_block_the_push(self):
        self.config("url.http://proxy.local/.insteadOf", "https://gitlab.com/")
        self.run_push()
        self.assertEqual(len(self.pushes), 1)

    def test_git_itself_must_resolve_the_push_url_unchanged(self):
        # Belt and braces: even if the config scan missed a rewrite, git's own resolution is compared.
        with mock.patch.object(pr, "_git_env", side_effect=lambda args, env: mock.Mock(
                returncode=0 if args[0] != "config" else 1, stdout=b"http://proxy.local/o/r.git\n", stderr=b"")):
            with self.assertRaisesRegex(pr.gh.GhError, "git would push to http://proxy.local/o/r.git"):
                self.run_push()

    def test_global_config_rewrites_are_ignored_not_inherited(self):
        home = Path(self.repo) / "home"
        home.mkdir()
        (home / ".gitconfig").write_text('[url "http://127.0.0.1:9/"]\n\tinsteadOf = https://github.com/\n')
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.run_push()
        self.assertEqual(len(self.pushes), 1)

    def test_cli_output_never_contains_the_token(self):
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["pr", "push"]), mock.patch.object(pr.gh, "repo", return_value="o/r"), \
             mock.patch.object(sys, "stdout", stdout):
            self.push_returncode, self.push_stderr = 0, b""
            with mock.patch.object(pr.gh, "app_mode", return_value=True), \
                 mock.patch.object(pr.gh, "token", return_value=SECRET), \
                 mock.patch.object(pr.gh, "token_login", return_value="acme-team[bot]"), \
                 mock.patch.object(pr.subprocess, "run", side_effect=self.fake_run):
                pr.main()  # no --branch: the current branch
        self.assertFalse(SECRET in stdout.getvalue(), "token leaked")
        self.assertIn('"branch": "feature/7-x"', stdout.getvalue())

    def test_a_failed_push_scrubs_the_token_from_the_error(self):
        with self.assertRaises(pr.gh.GhError) as caught:
            self.run_push(returncode=128, stderr=f"fatal: auth failed x-access-token:{SECRET} {BASIC}".encode())
        self.assertFalse(SECRET in str(caught.exception), "token leaked")
        self.assertFalse(BASIC in str(caught.exception), "token leaked")

    def test_only_team_branch_prefixes_are_pushed(self):
        for branch in ("dev", "stage", "main", "master", "gh-pages", "+feature/x", "+x", "HEAD:dev",
                       "refs/heads/main", "feature/x:main", "feature/../main", "feature/", "release/1", "x"):
            with self.subTest(branch=branch), self.assertRaisesRegex(pr.gh.GhError, "refusing to push"):
                self.run_push(branch=branch)
        self.assertEqual(self.pushes, [])

    def test_a_trailing_newline_is_rejected(self):
        for branch in ("feature/7-x\n", "feature/7-x\nmain", "fix/1\r"):
            with self.subTest(branch=branch), self.assertRaisesRegex(pr.gh.GhError, "refusing to push"):
                pr.check_pushable(branch)

    def test_every_prefix_the_team_uses_is_allowed(self):
        for branch in ("feature/7-x", "fix/8-y", "hotfix/9-z", "chore/product-team-0.10.0", "backmerge/stage-dev",
                       "revert/9-z", "design/12-onboarding", "docs/adr-3"):
            with self.subTest(branch=branch):
                pr.check_pushable(branch)

    def test_without_the_team_app_it_is_a_plain_push(self):
        result = self.run_push(app=False)
        self.assertEqual(self.pushes[-1][0][3:], ["push", "-u", "origin", "refs/heads/feature/7-x:refs/heads/feature/7-x"])
        self.assertEqual(result["as"], "session remote (no team app configured)")

    def test_a_stale_local_branch_is_refused_when_head_is_on_another_branch(self):
        # #625: a worktree on rework-619 pushing --branch feature/7-x used to push the old local ref and succeed.
        stale = self.git_out("rev-parse", "HEAD")
        self.git_out("switch", "-q", "-c", "rework-619")
        self.git_out("commit", "-q", "--allow-empty", "-m", "rework")
        with self.assertRaisesRegex(pr.gh.GhError, f"refusing to push feature/7-x: the local branch is at "
                                                   f"{stale[:12]}, but HEAD \\(branch rework-619\\)"):
            self.run_push()
        with self.assertRaisesRegex(pr.gh.GhError, "the local branch is at"):
            self.run_push(app=False)
        self.assertEqual(self.pushes, [])

    def test_a_branch_at_head_is_pushed_from_another_branch_or_a_detached_head(self):
        head = self.git_out("rev-parse", "HEAD")
        self.git_out("switch", "-q", "-c", "rework-619")
        self.assertEqual(self.run_push()["sha"], head)
        self.git_out("switch", "-q", "--detach")
        self.assertEqual(self.run_push(app=False)["sha"], head)
        self.assertEqual(len(self.pushes), 2)

    def test_a_missing_local_branch_is_refused(self):
        with self.assertRaisesRegex(pr.gh.GhError, "no local branch of that name and HEAD is on branch feature/7-x"):
            self.run_push(branch="feature/8-y")
        self.assertEqual(self.pushes, [])

    def test_success_needs_the_remote_branch_at_the_pushed_commit(self):
        self.remote_sha = "0" * 40
        for app in (True, False):
            with self.subTest(app=app), self.assertRaisesRegex(pr.gh.GhError, "remote branch is at 000000000000, not"):
                self.run_push(app=app)
        self.ls_remote = (128, f"fatal: x-access-token:{SECRET}".encode())
        with self.assertRaisesRegex(pr.gh.GhError, "could not read it back") as caught:
            self.run_push()
        self.assertFalse(SECRET in str(caught.exception), "token leaked")

    def test_never_forces(self):
        self.run_push()
        argv = self.pushes[-1][0]
        self.assertFalse([a for a in argv if a.startswith("--force") or a == "-f" or a.startswith("+")])


class CommitTest(unittest.TestCase):
    IDENT = {"login": "acme-team[bot]", "id": 9001, "name": "acme-team[bot]",
             "email": "9001+acme-team[bot]@users.noreply.github.com"}

    def run_commit(self, args, app=True, identity=None):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return mock.Mock(returncode=0)

        lookup = {"side_effect": identity} if isinstance(identity, Exception) else {"return_value": identity or self.IDENT}
        with mock.patch.object(pr.gh, "app_mode", return_value=app), \
             mock.patch.object(pr.gh, "app_identity", **lookup), \
             mock.patch.object(pr.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {"PT_TEAM_APP_KEY": "secret-key", "PT_OWNER_TOKEN": "owner"}):
            return pr.commit(args), calls

    def test_commits_as_the_bot_with_the_given_git_args(self):
        code, calls = self.run_commit(["-m", "feat: x", "--", "a.py"])
        argv, kwargs = calls[0]
        self.assertEqual((code, argv), (0, ["git", "commit", "-m", "feat: x", "--", "a.py"]))
        env = kwargs["env"]
        for role in ("AUTHOR", "COMMITTER"):
            self.assertEqual(env[f"GIT_{role}_NAME"], "acme-team[bot]")
            self.assertEqual(env[f"GIT_{role}_EMAIL"], "9001+acme-team[bot]@users.noreply.github.com")
        self.assertNotIn("PT_TEAM_APP_KEY", env)
        self.assertNotIn("PT_OWNER_TOKEN", env)

    def test_failed_identity_lookup_commits_nothing(self):
        with self.assertRaises(pr.gh.GhError):
            self.run_commit(["-m", "x"], identity=pr.gh.GhError("app not installed"))

    def test_author_override_is_refused_in_app_mode(self):
        for arg in ("--author=someone <s@x>", "--author"):
            with self.subTest(arg=arg), self.assertRaisesRegex(pr.gh.GhError, "sets the author itself"):
                self.run_commit(["-m", "x", arg])

    def test_reusing_a_commit_resets_its_author_to_the_bot(self):
        for args in (["--amend", "--no-edit"], ["-C", "HEAD"], ["-CHEAD"], ["-c", "HEAD"], ["--reuse-message=HEAD"],
                     ["--reuse-message", "HEAD"], ["--reedit-message=HEAD"]):
            with self.subTest(args=args):
                _, calls = self.run_commit(args)
                self.assertEqual(calls[0][0], ["git", "commit", *args, "--reset-author"])
        _, calls = self.run_commit(["--amend", "--reset-author", "--no-edit"])
        self.assertEqual(calls[0][0], ["git", "commit", "--amend", "--reset-author", "--no-edit"])
        _, calls = self.run_commit(["-m", "x"])
        self.assertEqual(calls[0][0], ["git", "commit", "-m", "x"])
        _, calls = self.run_commit(["--amend", "--no-edit"], app=False)
        self.assertEqual(calls[0][0], ["git", "commit", "--amend", "--no-edit"])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_amending_an_owner_commit_makes_the_bot_its_author(self):
        with tempfile.TemporaryDirectory() as repo:
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            REAL_RUN(["git", "init", "-q", repo], check=True, capture_output=True, env=env)
            REAL_RUN(["git", "-C", repo, "-c", "user.name=geeera", "-c", "user.email=owner@example.com", "commit", "-q",
                      "--allow-empty", "-m", "owner"], check=True, capture_output=True, env=env)
            cwd = os.getcwd()
            os.chdir(repo)
            try:
                with mock.patch.object(pr.gh, "app_mode", return_value=True), \
                     mock.patch.object(pr.gh, "app_identity", return_value=self.IDENT), \
                     mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
                    self.assertEqual(pr.commit(["-q", "--amend", "--no-edit", "--allow-empty"]), 0)
            finally:
                os.chdir(cwd)
            log = REAL_RUN(["git", "-C", repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>"], capture_output=True,
                           text=True, env=env, check=True).stdout.strip()
        bot = "acme-team[bot] <9001+acme-team[bot]@users.noreply.github.com>"
        self.assertEqual(log, f"{bot}|{bot}")

    def test_commit_help_is_prs_own_and_looks_nothing_up(self):
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["pr", "commit", "--help"]), mock.patch.object(sys, "stdout", stdout), \
             mock.patch.object(pr.gh, "app_identity", side_effect=AssertionError("identity lookup")), \
             mock.patch.object(pr.subprocess, "run", side_effect=AssertionError("git ran")), \
             self.assertRaises(SystemExit) as caught:
            pr.main()
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("pr commit -m M", stdout.getvalue())

    def test_without_the_team_app_it_is_a_plain_commit(self):
        _, calls = self.run_commit(["-m", "x"], app=False)
        self.assertNotIn("GIT_AUTHOR_NAME", {k for k in calls[0][1]["env"] if k not in os.environ})

    def test_cli_passes_everything_after_commit_to_git(self):
        with mock.patch.object(sys, "argv", ["pr", "commit", "-m", "msg", "--no-verify"]), \
             mock.patch.object(pr, "commit", return_value=0) as commit, self.assertRaises(SystemExit) as caught:
            pr.main()
        commit.assert_called_once_with(["-m", "msg", "--no-verify"])
        self.assertEqual(caught.exception.code, 0)


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


STABLE_SHA = "a" * 40
PRE_SHA = "b" * 40


def ls_remote_tags(*versions_and_shas):
    lines = [f"{sha}\trefs/tags/v{version}" for version, sha in versions_and_shas]
    return ("\n".join(lines) + "\n").encode()


class ReleaseVersionsTest(unittest.TestCase):
    def test_a_pre_release_tag_is_not_a_release(self):
        with mock.patch.object(pr, "git", return_value=ls_remote_tags(
                ("0.11.0", STABLE_SHA), ("0.12.0-pre.1", PRE_SHA))):
            self.assertEqual(pr.release_versions(), {STABLE_SHA: "0.11.0"})

    def test_a_pinned_pre_release_version_still_parses(self):
        # release_versions() keeps pre-release tags out of self-update's "released" list, but a product can
        # still be pinned to one explicitly: parse_version itself must not reject the string.
        self.assertEqual(pr.vendoring.parse_version("0.12.0-pre.1")[:3], (0, 12, 0))


class VerifySelfUpdateTest(unittest.TestCase):
    def check(self, git_responses, n=1):
        def fake_git(*args, cwd="."):
            for prefix, result in git_responses:
                if args[:len(prefix)] == prefix:
                    if isinstance(result, Exception):
                        raise result
                    return result
            raise AssertionError(f"unexpected git call: {args}")

        info = {"head_sha": STABLE_SHA, "base": "main"}
        with mock.patch.object(pr, "git", side_effect=fake_git):
            return pr.verify_self_update("o/r", n, info)

    def test_a_pre_release_tag_is_not_offered_to_a_stable_product(self):
        manifest = json.dumps({"commit": PRE_SHA, "version": "0.12.0-pre.1"}).encode()
        problems = self.check([
            (("fetch",), b""),
            (("rev-parse",), (STABLE_SHA + "\n").encode()),
            (("merge-base",), (STABLE_SHA + "\n").encode()),
            (("diff-tree",), b""),
            (("show", f"{STABLE_SHA}:{pr.vendoring.MANIFEST}"), manifest),
            (("show", f"refs/pt/base-1:{pr.vendoring.MANIFEST}"), pr.gh.GhError("not installed")),
            (("ls-remote",), ls_remote_tags(("0.11.0", STABLE_SHA), ("0.12.0-pre.1", PRE_SHA))),
        ])
        self.assertEqual(problems, [f"plugin commit {PRE_SHA[:12]} is not a tagged release"])


if __name__ == "__main__":
    unittest.main()
