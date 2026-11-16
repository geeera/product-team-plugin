try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import base64
import contextlib
import http.server
import io
import json
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time
import traceback
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import console, transport  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "contract" / "fixtures" / "tracker"
def stand_in(role="pm", items=(), project="tc", job_id="job-1"):
    """The console's local job stand-in (`Bearer local-job.<base64url(JSON)>`, team-console auth/local-job.ts): the
    fake console tells identities apart by it, as the console does."""
    claims = {"role": role, "project_slug": project, "job_id": job_id, "items": list(items)}
    return "local-job." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")


TOKEN = stand_in()
# What the runner's proxy forwards (team-console apps/runner/src/proxy/socket-server.ts), plus what it reads
# itself (authorization) and what HTTP framing needs (host, content-length).
FORWARDED = {"accept", "content-type", "idempotency-key", "if-match", "if-none-match",
             "authorization", "host", "content-length"}
_KEY = re.compile(r"(/items/)[A-Z][A-Z0-9]*-(\d+)")
ANY = object()


def problem(status, slug, **extra):
    return status, {"type": f"https://team-console/problems/{slug}", "title": slug, "status": status, **extra}, {}


def route_key(method, target):
    """A request as the fake console matches it: an item's key and its bare number are one address (the console's
    `itemNumberOfKey`), and `limit` is the client's page size, not part of the route."""
    parts = urllib.parse.urlsplit(target)
    query = tuple(sorted((k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "limit"))
    return method, _KEY.sub(r"\g<1>\2", parts.path), query


def identity_of(authorization):
    """{role, items, project} of a stand-in bearer, or None when it is not one."""
    match = re.fullmatch(r"Bearer local-job\.([A-Za-z0-9_-]+)", authorization or "")
    if not match:
        return None
    raw = match.group(1)
    claims = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return {"role": claims["role"], "items": claims["items"], "project": claims["project_slug"]}


class FakeConsole:
    """Answers per route, in order (the last one repeats); a route may be bound to one identity and one body, as a
    fixture is. Every request is recorded with its headers and body."""

    def __init__(self):
        self.routes = []
        self.seen = []
        self.lock = threading.Lock()

    def on(self, method, target, *answers, who=ANY, body=ANY, if_match=ANY):
        self.routes.insert(0, (route_key(method, target), who, body, if_match, list(answers)))

    def serve(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        raw_body = handler.rfile.read(length) if length else b""
        body = json.loads(raw_body) if raw_body else None
        who = identity_of(handler.headers.get("Authorization"))
        if_match = handler.headers.get("If-Match")
        key = route_key(handler.command, handler.path)
        with self.lock:
            self.seen.append({"method": handler.command, "path": handler.path, "body": body,
                              "headers": {k.lower(): v for k, v in handler.headers.items()}})
            answers = next((a for k, w, b, m, a in self.routes
                            if k == key and w in (ANY, who) and b in (ANY, body) and m in (ANY, if_match)), None)
            answer = (answers.pop(0) if len(answers) > 1 else answers[0]) if answers else None
        if who is None:
            answer = problem(401, "unauthorized")
        elif answer is None:
            answer = problem(404, "not-found")
        if callable(answer):
            answer = answer()
        status, payload, headers = answer[0], answer[1], dict(answer[2])
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else b""
        handler.send_response(status)
        content_type = "application/problem+json" if status >= 400 and not isinstance(payload, bytes) else \
            "application/json"
        handler.send_header("Content-Type", headers.pop("Content-Type", content_type))
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def load_fixtures(self):
        """The vendored fixtures, each served only to its identity, its request body and its If-Match (two PATCH
        fixtures on the same item differ only by the version they expect, #611 `items.changed`/`items.stale`)."""
        fixtures = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES.glob("*.json"))]
        for fx in fixtures:
            self.on(fx["request"]["method"], fx["request"]["path"],
                    (fx["response"]["status"], fx["response"]["body"], {}),
                    who=fixture_identity(fx), body=fx["request"].get("body"),
                    if_match=fx["request"].get("ifMatch", ANY))
        return fixtures


def fixture_identity(fx):
    return {"role": fx["as"]["role"], "items": fx["as"]["items"], "project": fx["as"].get("project") or "tc"}


def as_job(token):
    return mock.patch.dict(os.environ, {"TC_JOB_TOKEN": token})


def token_of(fx):
    who = fixture_identity(fx)
    return stand_in(who["role"], who["items"], who["project"])


def handler_for(fake):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_any(self):
            fake.serve(self)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_any

        def log_message(self, *_args):
            pass

    return Handler


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("unix", 0)  # BaseHTTPRequestHandler expects a (host, port) address


class ConsoleTestCase(unittest.TestCase):
    """A fake console on 127.0.0.1 and the job environment pointing at it."""

    socket_path = None

    def setUp(self):
        self.fake = FakeConsole()
        self.server = self.make_server()
        thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = mock.patch.dict(os.environ, self.environment(), clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PT_HTTP_DEADLINE", None)
        backoff = mock.patch.object(transport, "RETRY_BACKOFF", 0.01)
        backoff.start()
        self.addCleanup(backoff.stop)

    def make_server(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.fake))
        server.daemon_threads = True
        return server

    def environment(self):
        return {"TC_API_URL": f"http://127.0.0.1:{self.server.server_port}/api/v1", "TC_JOB_TOKEN": TOKEN,
                "TC_PROJECT": "tc", "NO_PROXY": "*"}

    def assert_no_token(self, text):
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(TOKEN.split(".", 1)[1], text)


class FixtureTest(ConsoleTestCase):
    def test_the_client_replays_every_vendored_fixture(self):
        fixtures = self.fake.load_fixtures()
        self.assertEqual(len(fixtures), 36)
        for fx in fixtures:
            req, expected = fx["request"], fx["response"]
            with self.subTest(fixture=fx["route"], path=req["path"], status=expected["status"]):
                relative = req["path"][len("/api/v1/"):]
                etag = req.get("ifMatch")
                with as_job(token_of(fx)):
                    if expected["status"] < 300:
                        body, _ = console.request(req["method"], relative, req.get("body"), etag=etag)
                        self.assertEqual(body, expected["body"])
                        continue
                    with self.assertRaises(console.ConsoleError) as caught:
                        console.request(req["method"], relative, req.get("body"), etag=etag)
                problem_body = expected["body"]
                self.assertEqual(caught.exception.status, expected["status"])
                self.assertEqual(caught.exception.type, problem_body["type"].rsplit("/", 1)[-1])
                self.assertEqual(caught.exception.field, problem_body.get("field"))
                self.assertEqual(caught.exception.reason, problem_body.get("reason"))
                # Decoupled from console.py's own exit_code formula: driven by the fixture's raw problem
                # type (not caught.exception.type, which that formula also reads) and the literal exit codes
                # `pr`'s CLI actually relies on, so a regression in the formula itself would fail this.
                is_not_cut_over = problem_body["type"].rsplit("/", 1)[-1] == "tracker-not-cut-over"
                self.assertEqual(caught.exception.exit_code, 3 if is_not_cut_over else 1)
        self.assertEqual(len(self.fake.seen), len(fixtures))  # 4xx answers are never retried

    def test_the_named_calls_send_the_fixture_requests(self):
        self.fake.load_fixtures()
        self.fake.on("GET", "/api/v1/projects/tc/decisions?command=reject&after=1", (200, {"decisions": [],
                                                                                            "next": None}, {}))
        self.assertEqual(console.inbox()["items"][0]["key"], "TC-1")
        self.assertEqual([r["id"] for r in console.requests("pending")], [1])
        self.assertEqual([d["item"]["key"] for d in console.decisions(command="reject")], ["TC-2"])
        with as_job(stand_in("dev", [1])):
            linked = console.link_pr(1, 57, "https://github.com/acme/tc/pull/57", "feature/tc-1-login")
        self.assertEqual(linked["linkedBy"], {"kind": "job", "name": "dev"})
        post = self.fake.seen[-1]
        self.assertEqual(post["path"], "/api/v1/projects/tc/items/1/pr-links")
        self.assertEqual(post["body"], {"number": 57, "url": "https://github.com/acme/tc/pull/57",
                                        "headRef": "feature/tc-1-login"})
        with self.assertRaises(console.ConsoleError) as caught:
            console.requests("open")
        self.assertEqual((caught.exception.status, caught.exception.field), (400, "state"))

    def test_only_the_bearer_and_the_forwarded_headers_are_sent(self):
        self.fake.load_fixtures()
        console.inbox()
        with as_job(stand_in("dev", [1])):
            console.link_pr(1, 57, "https://github.com/acme/tc/pull/57", "feature/tc-1-login")
        for seen, token in zip(self.fake.seen, (TOKEN, stand_in("dev", [1]))):
            with self.subTest(method=seen["method"]):
                self.assertEqual(seen["headers"]["authorization"], f"Bearer {token}")
                self.assertLessEqual(set(seen["headers"]), FORWARDED)
        self.assertNotIn("idempotency-key", self.fake.seen[0]["headers"])
        self.assertRegex(self.fake.seen[1]["headers"]["idempotency-key"], r"^[A-Za-z0-9_-]{8,128}$")

    def test_the_base_url_is_used_as_given(self):
        self.fake.load_fixtures()
        console.inbox()
        self.assertEqual(self.fake.seen[0]["path"], "/api/v1/projects/tc/inbox")


class RetryTest(ConsoleTestCase):
    def test_a_get_is_retried_once_after_a_5xx(self):
        self.fake.on("GET", "/api/v1/projects/tc/inbox", problem(503, "unavailable"), (200, {"items": []}, {}))
        self.assertEqual(console.inbox(), {"items": []})
        self.assertEqual(len(self.fake.seen), 2)

    def test_a_write_retry_reuses_its_idempotency_key(self):
        created = (201, {"number": 57}, {})
        self.fake.on("POST", "/api/v1/projects/tc/items/1/pr-links", problem(502, "console-unreachable"), created)
        console.link_pr(1, 57, "https://github.com/acme/tc/pull/57", "feature/tc-1-login")
        keys = [s["headers"]["idempotency-key"] for s in self.fake.seen]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        console.link_pr(1, 57, "https://github.com/acme/tc/pull/57", "feature/tc-1-login")
        self.assertNotEqual(self.fake.seen[-1]["headers"]["idempotency-key"], keys[0])  # a new logical write

    def test_a_second_5xx_and_any_4xx_are_final(self):
        self.fake.on("GET", "/api/v1/projects/tc/inbox", problem(503, "unavailable"))
        with self.assertRaises(console.ConsoleError) as caught:
            console.inbox()
        self.assertEqual((caught.exception.status, len(self.fake.seen)), (503, 2))
        self.fake.seen.clear()
        status, body, _ = problem(429, "rate-limited")
        self.fake.on("GET", "/api/v1/projects/tc/inbox", (status, body, {"Retry-After": "7"}))
        with self.assertRaises(console.ConsoleError) as caught:
            console.inbox()
        self.assertEqual((caught.exception.retry_after, len(self.fake.seen)), (7, 1))

    def test_each_attempt_gives_up_before_the_proxy_does(self):
        self.assertLessEqual(console.ATTEMPT_DEADLINE, 55.0)

        def slow():
            time.sleep(1.0)
            return 200, {"items": ["late"]}, {}

        self.fake.on("GET", "/api/v1/projects/tc/inbox", slow, (200, {"items": []}, {}))
        with mock.patch.object(console, "ATTEMPT_DEADLINE", 0.3):
            started = time.monotonic()
            self.assertEqual(console.inbox(), {"items": []})
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(self.fake.seen), 2)

    def test_a_given_idempotency_key_is_sent_and_a_malformed_one_refused(self):
        self.fake.on("POST", "/api/v1/projects/tc/items/1/comments", (201, {"id": 1}, {}))
        console.request("POST", "projects/tc/items/1/comments", {"body": "x"}, idempotency_key="rerun-key-0001")
        self.assertEqual(self.fake.seen[-1]["headers"]["idempotency-key"], "rerun-key-0001")
        with self.assertRaises(console.ConsoleError):
            console.request("POST", "projects/tc/items/1/comments", {"body": "x"}, idempotency_key="a\r\nb: c")
        self.assertEqual(len(self.fake.seen), 1)

    def test_a_body_over_the_proxy_limit_is_refused_before_sending(self):
        with self.assertRaises(console.ConsoleError) as caught:
            console.request("POST", "projects/tc/items/1/comments", {"body": "x" * console.BODY_LIMIT})
        self.assertEqual(caught.exception.type, "body-too-large")
        self.assertEqual(self.fake.seen, [])


class AnswerLimitsTest(ConsoleTestCase):
    """What a buggy or hostile peer can make the client hold or send (SECURITY notes on plugin#25)."""

    def test_an_answer_over_the_size_cap_is_refused_and_not_retried(self):
        # Connection: close, so the fake does not wait on a connection the client drops mid-body.
        self.fake.on("GET", "/api/v1/projects/tc/inbox", (200, b"[" + b"1," * 2000 + b"1]", {"Connection": "close"}))
        with mock.patch.object(transport, "RESPONSE_LIMIT", 1000), \
                self.assertRaises(console.ConsoleError) as caught:
            console.inbox()
        self.assertEqual(caught.exception.type, "answer-too-large")
        self.assertEqual(len(self.fake.seen), 1)

    def test_a_malformed_etag_from_the_console_is_a_console_error(self):
        for etag in ('"1"\r\n\tX-Evil: 1', "1", '"1" junk'):
            with self.subTest(etag=etag):
                self.fake.seen.clear()
                self.fake.on("GET", "/api/v1/projects/tc/items/7", (200, {"number": 7}, {"ETag": etag}))
                with self.assertRaises(console.ConsoleError) as caught:
                    console.update_item(7, {"status": "ready"})
                self.assertEqual(caught.exception.type, "unexpected-answer")
                self.assertEqual([s["method"] for s in self.fake.seen], ["GET"])  # no PATCH went out

    def test_a_malformed_if_match_is_refused_before_sending(self):
        for etag in ('"1"\nX-Evil: 1', '"\u00e9"', ""):
            with self.subTest(etag=etag), self.assertRaises(console.ConsoleError) as caught:
                console.request("PATCH", "projects/tc/items/7", {"status": "ready"}, etag=etag)
            self.assertEqual(caught.exception.type, "etag-invalid")
        self.assertEqual(self.fake.seen, [])

    def test_a_header_http_client_refuses_is_a_console_error_without_its_value(self):
        refused = ValueError(f"Invalid header value b'Bearer {TOKEN}'")
        with mock.patch.object(http.client.HTTPConnection, "putheader", side_effect=refused), \
                self.assertRaises(console.ConsoleError) as caught:
            console.inbox()
        self.assertEqual(caught.exception.type, "request-invalid")
        self.assert_no_token(str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)


class HeaderTrickleTest(unittest.TestCase):
    """The attempt deadline holds while the status line and headers arrive, not only for the body."""

    def setUp(self):
        self.requests = 0
        test = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                test.requests += 1
                try:
                    if test.requests == 1:
                        self.request.sendall(b"HTTP/1.1 200 OK\r\nX-Pad: ")
                        for _ in range(60):  # 3 s of header bytes, each well inside a 0.3 s socket timeout
                            self.request.sendall(b"x")
                            time.sleep(0.05)
                        return
                    body = b'{"items": []}'
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                except OSError:
                    pass  # the client gave up, as it should

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = mock.patch.dict(os.environ, {"TC_API_URL": f"http://127.0.0.1:{self.server.server_address[1]}/api/v1",
                                           "TC_JOB_TOKEN": TOKEN, "TC_PROJECT": "tc", "NO_PROXY": "*"})
        for patch in (env, mock.patch.object(transport, "RETRY_BACKOFF", 0.01),
                      mock.patch.object(console, "ATTEMPT_DEADLINE", 0.3)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("PT_HTTP_DEADLINE", None)

    def test_a_trickle_of_header_bytes_is_cut_at_the_attempt_deadline(self):
        started = time.monotonic()
        self.assertEqual(console.inbox(), {"items": []})  # the retry, on a connection the server closes
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.requests, 2)


class ItemUpdateTest(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.fake.on("GET", "/api/v1/projects/tc/items/7", (200, {"number": 7}, {"ETag": '"1"'}),
                     (200, {"number": 7}, {"ETag": '"2"'}))

    def test_a_412_rereads_once_and_reapplies_the_patch(self):
        self.fake.on("PATCH", "/api/v1/projects/tc/items/7", problem(412, "version-mismatch"),
                     (200, {"number": 7, "status": "ready"}, {}))
        self.assertEqual(console.update_item(7, {"status": "ready"})["status"], "ready")
        patches = [s for s in self.fake.seen if s["method"] == "PATCH"]
        self.assertEqual([p["headers"]["if-match"] for p in patches], ['"1"', '"2"'])
        self.assertEqual([p["body"] for p in patches], [{"status": "ready"}] * 2)

    def test_a_412_is_final_for_an_edit_and_on_the_second_try(self):
        self.fake.on("PATCH", "/api/v1/projects/tc/items/7", problem(412, "version-mismatch"))
        for reapply, patches in ((False, 1), (True, 2)):
            self.fake.seen.clear()
            with self.subTest(reapply=reapply), self.assertRaises(console.ConsoleError) as caught:
                console.update_item(7, {"title": "x"}, reapply=reapply)
            self.assertEqual(caught.exception.status, 412)
            self.assertEqual(len([s for s in self.fake.seen if s["method"] == "PATCH"]), patches)


class PaginationTest(ConsoleTestCase):
    def test_follows_next_cursors_with_the_largest_page(self):
        base = "/api/v1/projects/tc/requests?state=all"
        self.fake.on("GET", base, (200, {"requests": [{"id": 1}, {"id": 2}], "next": 2}, {}))
        self.fake.on("GET", base + "&after=2", (200, {"requests": [{"id": 3}], "next": None}, {}))
        self.assertEqual([r["id"] for r in console.requests("all")], [1, 2, 3])
        self.assertTrue(all("limit=100" in s["path"] for s in self.fake.seen))

    def test_a_cursor_that_does_not_advance_is_an_error(self):
        base = "/api/v1/projects/tc/requests?state=all"
        self.fake.on("GET", base, (200, {"requests": [{"id": 1}], "next": 4}, {}))
        self.fake.on("GET", base + "&after=4", (200, {"requests": [{"id": 1}], "next": 4}, {}))
        with self.assertRaisesRegex(console.ConsoleError, "does not advance"):
            console.requests("all")

    def test_a_cursor_may_go_down_in_queue_order(self):
        # The items cursor is the last item's number in (rank, number) order (team-console#456).
        base = "/api/v1/projects/tc/items?state=open"
        self.fake.on("GET", base, (200, {"items": [{"number": 9}], "next": 9}, {}))
        self.fake.on("GET", base + "&after=9", (200, {"items": [{"number": 2}], "next": 2}, {}))
        self.fake.on("GET", base + "&after=2", (200, {"items": [{"number": 5}], "next": None}, {}))
        self.assertEqual([r["number"] for r in console.list_all("projects/tc/items?state=open", "items")], [9, 2, 5])


class PrItemsTest(ConsoleTestCase):
    ITEMS = "/api/v1/projects/tc/prs/57/items"

    def test_the_linked_fixture_is_read_with_its_repository(self):
        self.fake.load_fixtures()
        with as_job(stand_in("orchestrator")):
            linked = console.pr_items(57)
        self.assertEqual((linked["number"], linked["provider"], linked["repo"]), (57, "github", "acme/tc"))
        self.assertEqual([(i["key"], i["labels"][-1]) for i in linked["items"]], [("TC-1", "security")])

    def test_pages_are_joined_and_must_agree(self):
        def page(rows, nxt, repo="acme/tc"):
            return 200, {"number": 57, "provider": "github", "repo": repo, "next": nxt,
                         "items": [{"key": f"TC-{n}", "labels": []} for n in rows]}, {}

        self.fake.on("GET", self.ITEMS, page([3], 3))
        self.fake.on("GET", self.ITEMS + "?after=3", page([1], None))
        self.assertEqual([i["key"] for i in console.pr_items(57)["items"]], ["TC-3", "TC-1"])
        self.fake.on("GET", self.ITEMS + "?after=3", page([1], None, repo="acme/other"))
        with self.assertRaisesRegex(console.ConsoleError, "one repository"):
            console.pr_items(57)


class NotCutOverTest(ConsoleTestCase):
    def test_a_project_still_on_github_exits_3(self):
        self.fake.on("GET", "/api/v1/projects/tc/inbox", problem(409, "tracker-not-cut-over"))
        with self.assertRaises(console.ConsoleError) as caught:
            console.inbox()
        self.assertEqual(caught.exception.exit_code, 3)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(console.report(caught.exception), 3)
        self.assertIn("still on GitHub Issues", stderr.getvalue())

    def test_other_errors_print_the_problem_and_exit_1(self):
        self.fake.load_fixtures()
        with self.assertRaises(console.ConsoleError) as caught:
            console.request("GET", "projects/tc/decisions?since=yesterday")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(console.report(caught.exception), 1)
        self.assertEqual(stderr.getvalue(),
                         "console: 400 query-invalid (since): since is an ISO 8601 UTC instant (2026-11-12T10:00:00Z)\n")


class TokenSecrecyTest(ConsoleTestCase):
    """The token reaches the console's Authorization header and nothing else: no error, report or traceback."""

    def rendered(self, exc):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            console.report(exc)
        return "\n".join([str(exc), repr(exc), stderr.getvalue(),
                          "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))])

    def test_no_error_carries_the_token(self):
        self.fake.load_fixtures()
        echo = (401, {"type": "x/unauthorized", "detail": f"bad token Bearer {TOKEN}", "field": TOKEN}, {})
        self.fake.on("GET", "/api/v1/projects/tc/echo", echo)
        self.fake.on("GET", "/api/v1/projects/tc/html", (502, f"<html>{TOKEN}</html>".encode(), {}))
        failures = [lambda: console.request("GET", "projects/tc/echo"),
                    lambda: console.request("GET", "projects/tc/html"),
                    lambda: console.request("POST", "projects/tc/items/TC-1/pr-links", {"number": 1}),
                    lambda: console.request("GET", "projects/tc/decisions")]
        for call in failures:
            with self.assertRaises(console.ConsoleError) as caught:
                call()
            self.assert_no_token(self.rendered(caught.exception))
            self.assertNotIn("<html>", self.rendered(caught.exception))  # a non-problem body is never quoted

    def test_an_unreachable_console_names_no_token(self):
        with mock.patch.dict(os.environ, {"TC_API_URL": "http://127.0.0.1:9/api/v1"}):  # nothing listens on 9
            with self.assertRaises(console.ConsoleError) as caught:
                console.inbox()
        self.assertEqual(caught.exception.type, "unreachable")
        self.assert_no_token(self.rendered(caught.exception))

    def test_the_config_does_not_print_the_token(self):
        self.assert_no_token(repr(console.config()))
        for bad in (f"{TOKEN}\r\nX-Evil: 1", f"{TOKEN} x"):
            with self.subTest(bad=bad[-6:]), mock.patch.dict(os.environ, {"TC_JOB_TOKEN": bad}):
                with self.assertRaises(console.ConsoleError) as caught:
                    console.inbox()
                self.assertEqual(caught.exception.field, "TC_JOB_TOKEN")
                self.assert_no_token(self.rendered(caught.exception))
        self.assertEqual(self.fake.seen, [])


class ConfigTest(unittest.TestCase):
    ENV = {"TC_API_URL": "https://console.example/api/v1", "TC_JOB_TOKEN": TOKEN, "TC_PROJECT": "tc"}

    def test_each_missing_variable_is_named(self):
        for name in ("TC_API_URL", "TC_JOB_TOKEN", "TC_PROJECT"):
            with self.subTest(name=name):
                env = {k: v for k, v in self.ENV.items() if k != name}
                with self.assertRaisesRegex(console.ConsoleError, f"{name} is not set"):
                    console.config(env)

    def test_plain_http_only_to_loopback_or_over_the_socket(self):
        with self.assertRaisesRegex(console.ConsoleError, "must be https"):
            console.config({**self.ENV, "TC_API_URL": "http://console.example/api/v1"})
        for url in ("http://localhost:8787/api/v1", "http://[::1]:8787/api/v1"):
            self.assertEqual(console.config({**self.ENV, "TC_API_URL": url}).api_url, url)
        cfg = console.config({**self.ENV, "TC_API_URL": "http://tc/api/v1", "TC_RUNNER_SOCKET": "/run/tc.sock"})
        self.assertEqual(cfg.runner_socket, "/run/tc.sock")

    def test_loopback_is_this_machine_only(self):
        for host in ("localhost", "127.0.0.1", "127.8.0.1", "::1"):
            self.assertTrue(transport.is_loopback(host), host)
        for host in ("localhost.example", "10.0.0.1", "0.0.0.0", "", "tc"):
            self.assertFalse(transport.is_loopback(host), host)

    def test_unusable_values_are_refused(self):
        for name, value in (("TC_API_URL", "https://user:pw@console.example/api/v1"),
                            ("TC_API_URL", "ftp://console.example"), ("TC_PROJECT", "../tc"),
                            ("TC_RUNNER_SOCKET", "relative.sock"), ("TC_CONSOLE_URL", "javascript:alert(1)")):
            with self.subTest(name=name, value=value), self.assertRaises(console.ConsoleError) as caught:
                console.config({**self.ENV, name: value})
            self.assertEqual(caught.exception.field, name)

    def test_item_url_needs_the_console_origin(self):
        with mock.patch.dict(os.environ, self.ENV):
            self.assertIsNone(console.item_url(7))
            with mock.patch.dict(os.environ, {"TC_CONSOLE_URL": "https://console.example/"}):
                self.assertEqual(console.item_url(7), "https://console.example/p/tc/t/7")

    def test_the_test_environment_has_no_console_variables(self):
        self.assertTrue(_isolation.CREDENTIAL_ENV.match("TC_JOB_TOKEN"))
        self.assertTrue(_isolation.CREDENTIAL_ENV.match("TC_RUNNER_SOCKET"))


class RunnerSocketTest(ConsoleTestCase):
    """TC_RUNNER_SOCKET set: the same requests go over the runner's Unix socket, never TCP."""

    def make_server(self):
        # sun_path is 104 bytes on macOS: a short directory, not the (long) per-user TMPDIR.
        directory = tempfile.mkdtemp(prefix="tc-", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.addCleanup(shutil.rmtree, directory, True)
        self.socket_path = os.path.join(directory, "job.sock")
        server = UnixHTTPServer(self.socket_path, handler_for(self.fake))
        os.chmod(self.socket_path, 0o600)  # as the runner leaves it, whatever the umask
        return server

    def environment(self):
        return {"TC_API_URL": "http://tc/api/v1", "TC_RUNNER_SOCKET": self.socket_path, "TC_JOB_TOKEN": TOKEN,
                "TC_PROJECT": "tc"}

    def test_requests_go_through_the_unix_socket(self):
        self.fake.load_fixtures()
        self.assertIsInstance(console._connection(console.config(), 1.0), console.UnixHTTPConnection)
        self.assertEqual(console.inbox()["items"][0]["askId"], 1)
        with as_job(stand_in("dev", [1])):
            linked = console.link_pr(1, 57, "https://github.com/acme/tc/pull/57", "feature/tc-1-login")
        self.assertEqual(linked["number"], 57)
        self.assertEqual([s["path"] for s in self.fake.seen],
                         ["/api/v1/projects/tc/inbox", "/api/v1/projects/tc/items/1/pr-links"])
        for seen in self.fake.seen:
            self.assertEqual(seen["headers"]["host"], "tc")
            self.assertLessEqual(set(seen["headers"]), FORWARDED)

    def test_a_missing_socket_is_an_error_without_the_token(self):
        with mock.patch.dict(os.environ, {"TC_RUNNER_SOCKET": self.socket_path + ".gone"}):
            with self.assertRaises(console.ConsoleError) as caught:
                console.inbox()
        self.assertEqual(caught.exception.type, "unreachable")
        self.assert_no_token(str(caught.exception))


    def test_a_socket_open_to_every_user_or_not_a_socket_is_refused(self):
        os.chmod(self.socket_path, 0o666)
        try:
            with self.assertRaises(console.ConsoleError) as caught:
                console.inbox()
        finally:
            os.chmod(self.socket_path, 0o600)
        self.assertEqual((caught.exception.type, caught.exception.field), ("socket-untrusted", "TC_RUNNER_SOCKET"))
        directory = os.path.dirname(self.socket_path)
        plain = os.path.join(directory, "plain")
        Path(plain).write_text("")
        link = os.path.join(directory, "link.sock")
        os.symlink(self.socket_path, link)
        for path in (plain, link):
            with self.subTest(path=os.path.basename(path)), mock.patch.dict(os.environ, {"TC_RUNNER_SOCKET": path}):
                with self.assertRaisesRegex(console.ConsoleError, "is not a socket"):
                    console.inbox()
        self.assertEqual(self.fake.seen, [])

    def test_a_socket_of_another_user_is_used_only_through_a_shared_group(self):
        self.fake.on("GET", "/api/v1/projects/tc/inbox", (200, {"items": []}, {}))
        info = os.stat(self.socket_path)
        other = mock.patch.object(console.os, "geteuid", return_value=info.st_uid + 1000)
        with other, mock.patch.object(console.os, "getegid", return_value=info.st_gid + 1000), \
                mock.patch.object(console.os, "getgroups", return_value=[info.st_gid + 1001]):
            with self.assertRaisesRegex(console.ConsoleError, "belongs to another user"):
                console.inbox()
        self.assertEqual(self.fake.seen, [])
        with other, mock.patch.object(console.os, "getgroups", return_value=[info.st_gid]):
            self.assertEqual(console.inbox(), {"items": []})  # the runner's 0660 socket under the job's group

if __name__ == "__main__":
    unittest.main()
