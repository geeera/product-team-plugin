try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import io
import json
import os
import socket
import sys
import unittest
import urllib.error
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
             mock.patch.object(gh.time, "sleep"), \
             mock.patch.object(gh, "token", return_value=""):
            with self.assertRaises(gh.GhError):
                gh.api("repos/o/r")
        with mock.patch.object(gh, "_request", return_value=("<html>proxy error</html>", {})):
            with self.assertRaises(gh.GhError):
                gh.api("repos/o/r")


class Clock:
    """time.monotonic for the tests: moves only when a fake read or sleep says so."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeResponse:
    def __init__(self, chunks, clock=None, per_chunk=0.0, headers=None):
        self.chunks, self.clock, self.per_chunk = list(chunks), clock, per_chunk
        self.headers = headers or {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read1(self, _size):
        if self.clock:
            self.clock.now += self.per_chunk
        return self.chunks.pop(0) if self.chunks else b""


def http_error(code, body=b"oops"):
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, io.BytesIO(body))


class DeadlineRetryTest(unittest.TestCase):
    """gh._request: one wall-clock budget per call (PT_HTTP_DEADLINE), one retry for GETs only."""

    def call(self, answers, method="GET", env=None, clock=None):
        clock = clock or Clock()
        queue = list(answers)

        def answer(*_args, **_kwargs):
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item() if callable(item) else item

        opener = mock.Mock(side_effect=answer)
        with mock.patch("urllib.request.urlopen", opener), \
             mock.patch.object(gh.time, "monotonic", clock), \
             mock.patch.object(gh.time, "sleep", side_effect=clock.sleep) as sleep, \
             mock.patch.object(gh, "token", return_value=""), \
             mock.patch.dict("os.environ", env or {}, clear=False):
            if env is None:
                os.environ.pop("PT_HTTP_DEADLINE", None)
            try:
                return gh._request(method, "https://api.github.com/x", {"a": 1} if method != "GET" else None), \
                    opener, sleep
            except gh.GhError as exc:
                return exc, opener, sleep

    def test_a_get_is_retried_once_after_a_5xx(self):
        result, opener, sleep = self.call([http_error(502), FakeResponse([b'{"ok": ', b"true}"])])
        self.assertEqual(result[0], '{"ok": true}')
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_a_get_is_retried_once_after_a_timeout_then_fails(self):
        timeout = urllib.error.URLError(socket.timeout("timed out"))
        result, opener, _ = self.call([timeout, TimeoutError("read timed out"), FakeResponse([b"{}"])])
        self.assertIsInstance(result, gh.GhError)
        self.assertEqual(opener.call_count, 2)

    def test_writes_are_never_retried(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for failure in (http_error(502), TimeoutError("read timed out")):
                with self.subTest(method=method, failure=type(failure).__name__):
                    result, opener, sleep = self.call([failure, FakeResponse([b"{}"])], method=method)
                    self.assertIsInstance(result, gh.GhError)
                    self.assertEqual(opener.call_count, 1)
                    sleep.assert_not_called()

    def test_client_errors_and_refused_connections_are_not_retried(self):
        for failure in (http_error(404), http_error(403), urllib.error.URLError(ConnectionRefusedError())):
            with self.subTest(failure=repr(failure)[:40]):
                result, opener, _ = self.call([failure, FakeResponse([b"{}"])])
                self.assertIsInstance(result, gh.GhError)
                self.assertEqual(opener.call_count, 1)

    def test_a_trickling_answer_is_cut_at_the_deadline(self):
        clock = Clock()
        slow = FakeResponse([b"{"] * 10, clock=clock, per_chunk=40.0)
        result, opener, _ = self.call([slow, FakeResponse([b"{}"])], clock=clock)
        self.assertIsInstance(result, gh.GhError)
        self.assertIn("within 90 s", str(result))
        self.assertEqual(opener.call_count, 1)  # the budget is spent: no retry

    def test_the_deadline_comes_from_the_environment_and_bounds_the_socket_timeout(self):
        result, opener, _ = self.call([FakeResponse([b"{}"])], env={"PT_HTTP_DEADLINE": "5"})
        self.assertEqual(result[0], "{}")
        self.assertEqual(opener.call_args[1]["timeout"], 5.0)
        _, opener, _ = self.call([FakeResponse([b"{}"])])
        self.assertEqual(opener.call_args[1]["timeout"], gh.SOCKET_TIMEOUT)

    def test_no_retry_when_the_backoff_would_outlast_the_deadline(self):
        clock = Clock()

        def slow_failure(*_a, **_k):
            clock.now += 89.0
            raise http_error(503)

        result, opener, sleep = self.call([slow_failure, FakeResponse([b"{}"])], clock=clock)
        self.assertIsInstance(result, gh.GhError)
        self.assertIn("HTTP 503", str(result))
        self.assertEqual(opener.call_count, 1)
        sleep.assert_not_called()

    def test_a_real_trickling_socket_is_cut_at_the_deadline(self):
        """End to end over a local socket: urllib's per-read timeout alone would wait for the whole trickle."""
        import http.server
        import threading

        class Trickle(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        gh.time.sleep(0.05)
                except OSError:
                    pass  # the client gave up, as it should

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Trickle)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/x"
            with mock.patch.dict("os.environ", {"PT_HTTP_DEADLINE": "0.5", "NO_PROXY": "*", "no_proxy": "*"}), \
                 mock.patch.object(gh, "token", return_value=""):
                started = gh.time.monotonic()
                with self.assertRaises(gh.GhError) as caught:
                    gh._request("GET", url)
                elapsed = gh.time.monotonic() - started
        finally:
            server.shutdown()
            server.server_close()
        self.assertIn("within 0.5 s", str(caught.exception))
        self.assertLess(elapsed, 2.0)

    def test_an_unreadable_deadline_is_an_error(self):
        for value in ("soon", "0", "-3"):
            with self.subTest(value=value):
                result, opener, _ = self.call([FakeResponse([b"{}"])], env={"PT_HTTP_DEADLINE": value})
                self.assertIn("PT_HTTP_DEADLINE", str(result))
                opener.assert_not_called()


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
