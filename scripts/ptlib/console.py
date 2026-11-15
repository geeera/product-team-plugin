"""The team console's tracker API (geeera/team-console ADR 0006) for the plugin scripts.

A job gets exactly these variables from its runner: TC_API_URL (the base, already ending in /api/v1), TC_JOB_TOKEN,
TC_PROJECT (its project slug), TC_RUNNER_SOCKET (HTTP over the runner's Unix socket, whose proxy adds the runner's
own credentials) and TC_CONSOLE_URL (the public origin, for links). The client sends `Authorization: Bearer
<TC_JOB_TOKEN>` and otherwise only the headers that proxy forwards; it never writes the token into an error, a log
line or an exception chain. Everything the console returns is data: callers validate it, never act on its text.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import transport

# The runner's proxy cuts a request at 60 s; an attempt must give up first so its retry still has a chance.
ATTEMPT_DEADLINE = 55.0
BODY_LIMIT = 256 * 1024  # the proxy's PROXY_BODY_MAX_BYTES
PAGE_LIMIT = 100
DETAIL_LIMIT = 300
EXIT_ERROR = 1
EXIT_NOT_CUT_OVER = 3
NOT_CUT_OVER = "tracker-not-cut-over"

_TOKEN = re.compile(r"^[\x21-\x7e]+$")  # header-safe: a CR/LF or space would split or corrupt the header
_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_RETRY_AFTER = re.compile(r"^\d{1,6}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")  # what the console accepts (tracker/idempotency.ts)


class ConsoleError(RuntimeError):
    """A refused or failed console call, parsed from the RFC 9457 problem when there is one.

    str() is `<status> <type> (<field>): <detail>`; it never carries the token or a response body."""

    def __init__(self, detail: str, *, status: Optional[int] = None, type: str = "request-failed",
                 field: Optional[str] = None, reason: Optional[str] = None, retry_after: Optional[int] = None):
        self.status, self.type, self.detail = status, type, detail
        self.field, self.reason, self.retry_after = field, reason, retry_after
        head = " ".join(p for p in (str(status) if status is not None else "", type) if p)
        super().__init__(f"{head}{f' ({field})' if field else ''}: {detail}")

    @property
    def exit_code(self) -> int:
        return EXIT_NOT_CUT_OVER if self.type == NOT_CUT_OVER else EXIT_ERROR


@dataclass(frozen=True)
class Config:
    api_url: str
    project: str
    token: str = field(repr=False)
    runner_socket: Optional[str] = None
    console_url: Optional[str] = None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url(name: str, raw: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password \
            or parts.query or parts.fragment:
        raise ConsoleError(f"{name} must be an http(s) URL without credentials, query or fragment",
                           type="config-invalid", field=name)
    return parts


def config(environ: Optional[Mapping[str, str]] = None) -> Config:
    """The job's console settings; a missing or unusable variable is an error that names it (never its value)."""
    env = os.environ if environ is None else environ

    def required(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            raise ConsoleError(f"{name} is not set: run this inside a console job", type="config-missing",
                               field=name)
        return value

    api_url, token, project = required("TC_API_URL"), required("TC_JOB_TOKEN"), required("TC_PROJECT")
    if not _TOKEN.match(token):
        raise ConsoleError("TC_JOB_TOKEN is not a bearer token (printable ASCII, no spaces)",
                           type="config-invalid", field="TC_JOB_TOKEN")
    if not _PROJECT.match(project):
        raise ConsoleError("TC_PROJECT is not a project slug", type="config-invalid", field="TC_PROJECT")
    base = _base_url("TC_API_URL", api_url)
    runner_socket = env.get("TC_RUNNER_SOCKET", "").strip() or None
    if runner_socket is not None and not os.path.isabs(runner_socket):
        raise ConsoleError("TC_RUNNER_SOCKET must be an absolute path", type="config-invalid",
                           field="TC_RUNNER_SOCKET")
    # Over the socket the runner is the only hop; anywhere else the token must not cross a network in clear.
    if runner_socket is None and base.scheme == "http" and not _is_loopback(base.hostname or ""):
        raise ConsoleError("TC_API_URL must be https unless it is a loopback address or TC_RUNNER_SOCKET is set",
                           type="config-invalid", field="TC_API_URL")
    console_url = env.get("TC_CONSOLE_URL", "").strip() or None
    if console_url is not None:
        _base_url("TC_CONSOLE_URL", console_url)
    return Config(api_url=api_url.rstrip("/"), project=project, token=token, runner_socket=runner_socket,
                  console_url=console_url.rstrip("/") if console_url else None)


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP/1.1 over a Unix socket (the runner's job socket, ADR R4); the URL's host only names the Host header."""

    def __init__(self, path: str, host: str, timeout: float):
        super().__init__(host, timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _connection(cfg: Config, timeout: float) -> http.client.HTTPConnection:
    base = urllib.parse.urlsplit(cfg.api_url)
    if cfg.runner_socket:
        return UnixHTTPConnection(cfg.runner_socket, base.netloc, timeout)
    if base.scheme == "https":
        return http.client.HTTPSConnection(base.netloc, timeout=timeout)
    return http.client.HTTPConnection(base.netloc, timeout=timeout)


def join_path(*segments: Any, **query: Any) -> str:
    """A path under the API base with each segment escaped; query values of None are left out."""
    text = "/".join(urllib.parse.quote(str(s), safe="") for s in segments)
    params = [(k, str(v)) for k, v in query.items() if v is not None]
    return f"{text}?{urllib.parse.urlencode(params)}" if params else text


def project_path(*segments: Any, **query: Any) -> str:
    return join_path("projects", config().project, *segments, **query)


def _redact(text: str, token: str) -> str:
    return text.replace(token, "[redacted]") if token else text


def _problem(status: int, payload: bytes, headers: Dict[str, str], token: str) -> ConsoleError:
    try:
        body = json.loads(payload.decode("utf-8")) if payload else None
    except ValueError:
        body = None
    retry_after = headers.get("retry-after", "").strip()
    retry = int(retry_after) if _RETRY_AFTER.match(retry_after) else None
    if not isinstance(body, dict):
        # Not a problem document (a proxy's error page, say): its text is never quoted.
        return ConsoleError(f"HTTP {status} without a problem body", status=status, type="http-error",
                            retry_after=retry)

    def text(name: str, limit: int = 100) -> Optional[str]:
        value = body.get(name)
        return transport.clip(_redact(value, token), limit) if isinstance(value, str) and value else None

    kind = (text("type", 200) or "about:blank").rstrip("/").rsplit("/", 1)[-1]
    detail = text("detail", DETAIL_LIMIT) or text("title") or f"HTTP {status}"
    return ConsoleError(detail, status=status, type=kind, field=text("field"), reason=text("reason"),
                        retry_after=retry)


def _attempt(cfg: Config, method: str, target: str, data: Optional[bytes], headers: Dict[str, str],
             deadline: float) -> Tuple[int, Dict[str, str], bytes]:
    now = time.monotonic()
    if now >= deadline:
        raise transport.Deadline()
    attempt_deadline = min(deadline, now + ATTEMPT_DEADLINE)
    conn = _connection(cfg, min(ATTEMPT_DEADLINE, deadline - now))
    where = f"{method} {target}"
    try:
        # putrequest, not request(): no Accept-Encoding or other header the proxy would drop.
        conn.putrequest(method, target, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders(data)
        resp = conn.getresponse()
        payload = transport.read_until(resp, attempt_deadline, ATTEMPT_DEADLINE)
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload
    except transport.Deadline:
        if time.monotonic() >= deadline:
            raise
        raise transport.Retryable(ConsoleError(f"{where}: no complete answer within {ATTEMPT_DEADLINE:g} s",
                                               type="timeout")) from None
    except (OSError, http.client.HTTPException) as exc:
        # The exception's class and errno only: its text could quote what was being sent.
        error = ConsoleError(f"{where} failed: {type(exc).__name__}"
                             f"{f' (errno {exc.errno})' if getattr(exc, 'errno', None) else ''}",
                             type="timeout" if transport.is_timeout(exc) else "unreachable")
        if transport.is_timeout(exc):
            if time.monotonic() >= deadline:
                raise transport.Deadline() from None
            raise transport.Retryable(error) from None
        raise error from None
    finally:
        conn.close()


def request(method: str, path: str, body: Any = None, *, etag: Optional[str] = None,
            idempotency_key: Optional[str] = None) -> Tuple[Any, Dict[str, str]]:
    """One call to `<TC_API_URL>/<path>` → (JSON or None, lower-cased headers).

    Every write carries an Idempotency-Key (the console requires one): `idempotency_key`, else a fresh uuid4 for
    this logical write. Its retry reuses the key, so the console replays instead of writing twice; that is why a
    write may be retried at all. One retry after a timeout or a 5xx, for reads and writes; a 4xx never is."""
    cfg = config()
    method = method.upper()
    budget = transport.deadline_seconds(ConsoleError)
    deadline = time.monotonic() + budget
    target = f"{urllib.parse.urlsplit(cfg.api_url).path.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {cfg.token}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > BODY_LIMIT:
            raise ConsoleError(f"the request body is {len(data)} bytes, over the runner's {BODY_LIMIT // 1024} KB",
                               type="body-too-large")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    if etag is not None:
        headers["If-Match"] = etag
    if method != "GET":
        key = idempotency_key or str(uuid.uuid4())
        if not _IDEMPOTENCY_KEY.match(key):
            raise ConsoleError("the Idempotency-Key must be 8 to 128 letters, digits, _ or -",
                               type="idempotency-key")
        headers["Idempotency-Key"] = key

    def attempt() -> Tuple[Any, Dict[str, str]]:
        status, answer_headers, payload = _attempt(cfg, method, target, data, headers, deadline)
        if 200 <= status < 300:
            if not payload.strip():
                return None, answer_headers
            try:
                return json.loads(payload.decode("utf-8")), answer_headers
            except ValueError:
                raise ConsoleError(f"{method} {path}: HTTP {status} answered with something other than JSON",
                                   status=status, type="not-json") from None
        error = _problem(status, payload, answer_headers, cfg.token)
        if status >= 500:
            raise transport.Retryable(error)
        raise error

    return transport.run(attempt, attempts=2, deadline=deadline,
                         on_deadline=lambda: ConsoleError(f"{method} {path}: no complete answer within "
                                                          f"{budget:g} s (PT_HTTP_DEADLINE)", type="timeout"))


def list_all(path: str, member: str) -> List[Any]:
    """Every row of a cursor-paged list (`?after=<next>&limit=100`), in the order the console returns them."""
    rows: List[Any] = []
    after: Optional[int] = None
    joiner = "&" if "?" in path else "?"
    for _ in range(10_000):  # a cursor that never ends is a console bug, not a reason to loop forever
        query = urllib.parse.urlencode([("limit", PAGE_LIMIT)] + ([("after", after)] if after is not None else []))
        page, _ = request("GET", f"{path}{joiner}{query}")
        if not isinstance(page, dict) or not isinstance(page.get(member), list):
            raise ConsoleError(f"GET {path}: the answer has no {member!r} list", type="unexpected-answer")
        rows.extend(page[member])
        cursor = page.get("next")
        if cursor is None:
            return rows
        if not isinstance(cursor, int) or isinstance(cursor, bool) or (after is not None and cursor <= after):
            raise ConsoleError(f"GET {path}: the next cursor {cursor!r} does not advance", type="unexpected-answer")
        after = cursor
    raise ConsoleError(f"GET {path}: more than 10000 pages", type="unexpected-answer")


def item(number: int) -> Tuple[Dict[str, Any], str]:
    """The item and its ETag (the version an If-Match names)."""
    dto, headers = request("GET", project_path("items", number))
    etag = headers.get("etag")
    if not isinstance(dto, dict) or not etag:
        raise ConsoleError(f"GET item {number}: no item or no ETag in the answer", type="unexpected-answer")
    return dto, etag


def update_item(number: int, patch: Dict[str, Any], *, reapply: bool = True) -> Dict[str, Any]:
    """GET → If-Match → PATCH. On 412 the item is re-read once and the same patch sent again when `reapply` (a
    label or status delta is safe to re-apply; a title or body edit is not, and fails with the 412)."""
    for tries_left in (1, 0):
        _, etag = item(number)
        try:
            dto, _ = request("PATCH", project_path("items", number), patch, etag=etag)
            return dto
        except ConsoleError as exc:
            if exc.status != 412 or not reapply or not tries_left:
                raise
    raise AssertionError("unreachable")  # the loop returns or raises


def inbox() -> Dict[str, Any]:
    dto, _ = request("GET", project_path("inbox"))
    return dto


def requests(state: str) -> List[Dict[str, Any]]:
    """The project's owner requests; `state` (pending, handled or all) is mandatory for a job."""
    return list_all(project_path("requests", state=state), "requests")


def decisions(since: Optional[str] = None, command: Optional[str] = None) -> List[Dict[str, Any]]:
    return list_all(project_path("decisions", since=since, command=command), "decisions")


def link_pr(number: int, pr_number: int, url: str, head_ref: str) -> Dict[str, Any]:
    dto, _ = request("POST", project_path("items", number, "pr-links"),
                     {"number": pr_number, "url": url, "headRef": head_ref})
    return dto


def item_url(number: int) -> Optional[str]:
    """The item's page in the console, or None: behind the runner socket the API does not know its public origin."""
    cfg = config()
    return f"{cfg.console_url}/p/{urllib.parse.quote(cfg.project, safe='')}/t/{number}" if cfg.console_url else None


def report(exc: ConsoleError) -> int:
    """The error on stderr, as the scripts print it; returns the exit code (3 for a project still on GitHub)."""
    if exc.type == NOT_CUT_OVER:
        print("console: this project is still on GitHub Issues: run product-team 0.11 until the cutover",
              file=sys.stderr)
    else:
        print(f"console: {exc}", file=sys.stderr)
    return exc.exit_code
