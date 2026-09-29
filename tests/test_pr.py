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


def run_gate(reviews, config="", files=("apps/web/page.tsx",), security=(), review_bot=None, same_account=False,
             team_bot=None, ci_state="pass"):
    """pr.gate with GitHub mocked: `config` is project.yml on the base branch, `files` the PR's changed paths."""
    with mock.patch.object(pr, "view", return_value={"head_sha": "head1", "base": "dev"}), \
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
  language: en
"""
BOTH = [verdict("QA: APPROVED", "x"), verdict("REVIEW: APPROVED", "x")]
QA_ONLY = [verdict("QA: APPROVED", "x")]


class RequiredVerdictsGateTest(unittest.TestCase):
    """Which verdicts `pr gate` requires, from the review block of project.yml on the PR's base branch."""

    def test_without_a_review_block_qa_and_review_are_required_on_every_pr(self):
        for files in (["apps/web/page.tsx"], [".github/workflows/ci.yml"], ["docs/setup.md"]):
            with self.subTest(files=files):
                result = run_gate(QA_ONLY, "", files)
                self.assertEqual(result["required"], ["QA", "REVIEW"])
                self.assertEqual(result["missing"], ["REVIEW: no verdict"])
                self.assertFalse(result["policy"]["configured"])
                self.assertIn("default", result["why"]["REVIEW"]["why"])

    def test_reviewer_code_pr_touching_only_ci_and_docs_needs_no_review(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/setup.md", "README.md", ".github/workflows/ci.yml"],
                          security=["dependencies or CI: .github/workflows/ci.yml"])
        self.assertEqual(result["required"], ["QA", "SECURITY"])
        self.assertFalse(result["why"]["REVIEW"]["required"])
        self.assertIn("no changed path matches code_paths (apps/**, libs/**)", result["why"]["REVIEW"]["why"])
        self.assertEqual(result["missing"], ["SECURITY: no verdict"])

    def test_reviewer_code_docs_only_pr_passes_with_qa_alone(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/setup.md"])
        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["required"], ["QA"])

    def test_reviewer_code_pr_touching_apps_needs_review(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/setup.md", "apps/web/src/page.tsx"])
        self.assertEqual(result["required"], ["QA", "REVIEW"])
        self.assertEqual(result["missing"], ["REVIEW: no verdict"])
        self.assertIn("apps/web/src/page.tsx", result["why"]["REVIEW"]["why"])
        self.assertTrue(run_gate(BOTH, PROPORTIONAL, ["libs/ui/button.ts"])["passed"])

    def test_moving_code_out_of_a_code_path_still_needs_review(self):
        # changed_files lists a rename's old path too
        result = run_gate(QA_ONLY, PROPORTIONAL, ["tools/old.ts", "apps/web/old.ts"])
        self.assertIn("REVIEW", result["required"])

    def test_reviewer_never_and_qa_code(self):
        config = "review:\n  qa: code\n  reviewer: never\n  code_paths:\n    - apps/**\n"
        docs = run_gate([], config, ["docs/a.md"])
        self.assertEqual(docs["required"], [])
        self.assertTrue(docs["passed"])
        code = run_gate([], config, ["apps/a.ts"])
        self.assertEqual(code["required"], ["QA"])
        self.assertIn("never", code["why"]["REVIEW"]["why"])

    def test_security_is_required_whatever_the_policy_says(self):
        config = "review:\n  qa: never\n  reviewer: never\n"
        result = run_gate([], config, ["apps/api/auth.ts"], security=["sensitive path: apps/api/auth.ts"])
        self.assertEqual(result["required"], ["SECURITY"])
        self.assertTrue(result["why"]["SECURITY"]["required"])
        self.assertIn("auth.ts", result["why"]["SECURITY"]["why"])

    def test_every_verdict_gets_a_reason(self):
        result = run_gate(BOTH, PROPORTIONAL, ["apps/a.ts"])
        self.assertEqual(set(result["why"]), {"QA", "REVIEW", "SECURITY"})
        self.assertIn("qa: always", result["why"]["QA"]["why"])
        self.assertFalse(result["why"]["SECURITY"]["required"])
        self.assertEqual(result["policy"]["max_rework_rounds"], 1)

    def test_an_unreadable_review_block_fails_the_gate(self):
        result = run_gate(BOTH, "review:\n  reviewer: sometimes\n", ["docs/a.md"])
        self.assertFalse(result["passed"])
        self.assertIn("review.reviewer", result["missing"][0])

    def test_ci_still_decides(self):
        result = run_gate(QA_ONLY, PROPORTIONAL, ["docs/a.md"], ci_state="fail")
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing"], ["CI: fail"])

    def test_the_policy_is_read_from_the_base_branch_not_the_pr(self):
        # The PR adds a relaxed review block; dev has none, so REVIEW stays required.
        with mock.patch.object(pr.gh, "raw", return_value="") as raw, \
             mock.patch.object(pr, "view", return_value={"head_sha": "head1", "base": "dev"}), \
             mock.patch.object(pr, "changed_files", return_value=[".product-team/project.yml", "docs/a.md"]), \
             mock.patch.object(pr, "security_check", return_value=["agent tooling or gate config"]), \
             mock.patch.object(pr, "ci", return_value={"state": "pass", "failing": [], "pending": []}), \
             mock.patch.object(pr.gh, "api_list", return_value=QA_ONLY), \
             mock.patch.object(pr.gh, "review_login", return_value=None), \
             mock.patch.object(pr.gh, "app_mode", return_value=False), \
             mock.patch.object(pr.gh, "acts_as_owner", return_value=False):
            result = pr.gate("o/r", 5)
        self.assertTrue(raw.call_args[0][0].endswith("?ref=refs/heads/dev"))
        self.assertEqual(result["required"], ["QA", "REVIEW", "SECURITY"])


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

    def fake_run(self, argv, **kwargs):
        if argv[:2] == ["git", "push"] or argv[3:4] == ["push"]:
            self.pushes.append((argv, kwargs))
            return mock.Mock(returncode=self.push_returncode, stdout=b"", stderr=self.push_stderr)
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


if __name__ == "__main__":
    unittest.main()
