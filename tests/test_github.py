try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import checks, gh  # noqa: E402


class RemoteTest(unittest.TestCase):
    def test_https_ssh_and_proxied_remotes(self):
        for url in ("https://github.com/geeera/storify.git", "git@github.com:geeera/storify.git",
                    "http://127.0.0.1:41235/git/geeera/storify", "https://github.com/geeera/storify/"):
            with self.subTest(url=url):
                self.assertEqual(gh.parse_remote(url), "geeera/storify")

    def test_unusable_remote(self):
        self.assertIsNone(gh.parse_remote("storify"))


class PaginationTest(unittest.TestCase):
    def test_follows_next_links_and_unwraps_wrapped_lists(self):
        pages = {
            "https://api.github.com/a": (json.dumps({"total_count": 3, "check_runs": [1, 2]}),
                                         {"Link": '<https://api.github.com/a?page=2>; rel="next", <x>; rel="last"'}),
            "https://api.github.com/a?page=2": (json.dumps({"total_count": 3, "check_runs": [3]}), {}),
        }
        with mock.patch.object(gh, "_request", side_effect=lambda m, url, *a, **k: pages[url]):
            self.assertEqual(gh.api_list("a"), [1, 2, 3])


class ErrorTest(unittest.TestCase):
    def test_timeouts_and_non_json_become_gh_errors(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")), \
             mock.patch.object(gh, "token", return_value=""):
            with self.assertRaises(gh.GhError):
                gh.api("repos/o/r")
        with mock.patch.object(gh, "_request", return_value=("<html>proxy error</html>", {})):
            with self.assertRaises(gh.GhError):
                gh.api("repos/o/r")


class TokenTest(unittest.TestCase):
    def setUp(self):
        gh._token_cache = gh._cli_token_cache = None

    def tearDown(self):
        gh._token_cache = gh._cli_token_cache = None

    # Token values are compared as booleans: a failing assertion must never print a credential.
    def test_env_token_wins_and_no_cli_is_needed(self):
        with mock.patch.dict("os.environ", {"GH_TOKEN": "t1", "GITHUB_TOKEN": "t2"}), \
             mock.patch.object(gh.shutil, "which", return_value=None), \
             mock.patch.object(gh, "_run_gh_auth_token") as cli:
            self.assertTrue(gh.token() == "t1", "GH_TOKEN should win")
        cli.assert_not_called()

    def test_missing_token_and_cli_means_unauthenticated(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(gh.shutil, "which", return_value=None):
            self.assertTrue(gh.token() == "", "expected no token")

    def test_cli_token_is_used_when_the_env_has_none(self):
        fake = mock.Mock(returncode=0, stdout="fake-cli-token\n")
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(gh.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(gh, "_run_gh_auth_token", return_value=fake):
            self.assertTrue(gh.token() == "fake-cli-token", "expected the (mocked) CLI token")

    def test_suite_guard_trips_on_an_unmocked_gh_auth_token(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(gh.shutil, "which", return_value="/usr/bin/gh"):
            with self.assertRaises(_isolation.RealCredentialAccess):
                gh.token()

    def test_suite_removed_real_credentials_from_the_environment(self):
        self.assertFalse([k for k in os.environ if _isolation.CREDENTIAL_ENV.match(k)])


def run(name, status="completed", conclusion="success", started="2026-09-27T10:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started}


class ChecksTest(unittest.TestCase):
    def test_all_green_passes(self):
        self.assertEqual(checks.summarise([run("ci"), run("lint", conclusion="skipped")], [])["state"], "pass")

    def test_any_failure_fails(self):
        result = checks.summarise([run("ci"), run("e2e", conclusion="failure")], [])
        self.assertEqual((result["state"], result["failing"]), ("fail", ["e2e"]))

    def test_running_is_pending(self):
        self.assertEqual(checks.summarise([run("ci", status="in_progress", conclusion=None)], [])["state"], "pending")

    def test_same_job_name_in_two_workflows_both_count(self):
        result = checks.summarise([run("build"), run("build", conclusion="failure")], [])
        self.assertEqual(result["state"], "fail")

    def test_unfinished_actions_suite_keeps_ci_pending(self):
        suites = [{"id": 7, "status": "in_progress", "app": {"slug": "github-actions"}}]
        self.assertEqual(checks.summarise([run("ci")], [], suites)["state"], "pending")

    def test_idle_suites_of_other_apps_are_ignored(self):
        suites = [{"id": 8, "status": "queued", "app": {"slug": "some-app"}}]
        self.assertEqual(checks.summarise([run("ci")], [], suites)["state"], "pass")

    def test_no_checks_yet_is_pending_not_pass(self):
        self.assertEqual(checks.summarise([], [])["state"], "pending")

    def test_legacy_statuses_newest_per_context(self):
        statuses = [{"context": "deploy", "state": "success"}, {"context": "deploy", "state": "failure"}]
        self.assertEqual(checks.summarise([], statuses)["state"], "pass")


if __name__ == "__main__":
    unittest.main()
