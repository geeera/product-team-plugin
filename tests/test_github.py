import json
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


class TokenTest(unittest.TestCase):
    def setUp(self):
        gh._token_cache = None

    def tearDown(self):
        gh._token_cache = None

    def test_env_token_wins_and_no_cli_is_needed(self):
        with mock.patch.dict("os.environ", {"GH_TOKEN": "t1", "GITHUB_TOKEN": "t2"}), \
             mock.patch("shutil.which", return_value=None):
            self.assertEqual(gh.token(), "t1")

    def test_missing_token_and_cli_means_unauthenticated(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(gh.shutil, "which", return_value=None):
            self.assertEqual(gh.token(), "")


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

    def test_rerun_supersedes_the_failed_attempt(self):
        result = checks.summarise([run("ci", conclusion="failure", started="2026-09-27T10:00:00Z"),
                                   run("ci", started="2026-09-27T11:00:00Z")], [])
        self.assertEqual(result["state"], "pass")

    def test_no_checks_yet_is_pending_not_pass(self):
        self.assertEqual(checks.summarise([], [])["state"], "pending")

    def test_legacy_statuses_newest_per_context(self):
        statuses = [{"context": "deploy", "state": "success"}, {"context": "deploy", "state": "failure"}]
        self.assertEqual(checks.summarise([], statuses)["state"], "pass")


if __name__ == "__main__":
    unittest.main()
