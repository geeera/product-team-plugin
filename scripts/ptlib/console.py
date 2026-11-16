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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

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
                 field: Optional[str] = None, reason: Optional[str] = None, kind: Optional[str] = None,
                 retry_after: Optional[int] = None):
        self.status, self.type, self.detail = status, type, detail
        self.field, self.reason, self.kind, self.retry_after = field, reason, kind, retry_after
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
    # `reason` names a transition refusal, `kind` the sort of secret a 422 `body-secret` found (never its value).
    return ConsoleError(detail, status=status, type=kind, field=text("field"), reason=text("reason"),
                        kind=text("kind"), retry_after=retry)


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
        if not isinstance(page, dict) or not isinstance(page.get(member), list) \
                or not all(isinstance(row, dict) for row in page[member]):
            raise ConsoleError(f"GET {path}: the answer has no {member!r} list of objects", type="unexpected-answer")
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


Patch = Union[Dict[str, Any], Callable[[Dict[str, Any]], Dict[str, Any]]]


def update_item(number: int, patch: Patch, *, reapply: bool = True) -> Dict[str, Any]:
    """GET → If-Match → PATCH; `patch` is the body, or a function of the item just read that builds it. On 412 the
    item is re-read once and the patch sent again when `reapply` (a label or status delta is safe to re-apply; a
    title or body edit is not, and fails with the 412)."""
    return _with_version(number, "PATCH", project_path("items", number), patch, reapply)


def queue_move(number: int, move: str) -> Dict[str, Any]:
    """`POST …/items/N/queue {move}` with the item's version (#456); a 412 is one re-read, the move is one step
    from wherever the item is now."""
    return _with_version(number, "POST", project_path("items", number, "queue"), {"move": move}, True)


def _with_version(number: int, method: str, path: str, patch: Patch, reapply: bool) -> Dict[str, Any]:
    for tries_left in (1, 0):
        current, etag = item(number)
        body = patch(current) if callable(patch) else patch
        try:
            dto, _ = request(method, path, body, etag=etag)
            return _object(dto, f"item {number}")
        except ConsoleError as exc:
            if exc.status != 412 or not reapply or not tries_left:
                raise
    raise AssertionError("unreachable")  # the loop returns or raises


def _object(dto: Any, what: str) -> Dict[str, Any]:
    if not isinstance(dto, dict):
        raise ConsoleError(f"GET {what}: the answer is not an object", type="unexpected-answer")
    return dto


def get_item(number: Union[int, str]) -> Dict[str, Any]:
    """The item with its body, for reading; `item()` is the form a write uses (it needs the ETag). `number` may also
    be the item's key (`TC-412`): the console reads either, and answers 404 for another project's key."""
    dto, _ = request("GET", project_path("items", number))
    return _object(dto, f"item {number}")


def items(**filters: Any) -> List[Dict[str, Any]]:
    """`GET …/items` with its filters (state, status, kind, label, sprint), in the console's queue order: (rank,
    number), the order `backlog next` dispatches in (#456). Callers must not re-sort it by number."""
    return list_all(project_path("items", **filters), "items")


def comments(number: int) -> List[Dict[str, Any]]:
    return list_all(project_path("items", number, "comments"), "comments")


def asks(number: int) -> List[Dict[str, Any]]:
    return list_all(project_path("items", number, "asks"), "asks")


def item_decisions(number: int) -> List[Dict[str, Any]]:
    return list_all(project_path("items", number, "decisions"), "decisions")


def events(number: int) -> List[Dict[str, Any]]:
    return list_all(project_path("items", number, "events"), "events")


def labels() -> List[Dict[str, Any]]:
    dto, _ = request("GET", project_path("labels"))
    rows = _object(dto, "labels").get("labels")
    if not isinstance(rows, list):
        raise ConsoleError("GET labels: the answer has no 'labels' list", type="unexpected-answer")
    return rows


def sprints(state: Optional[str] = None) -> List[Dict[str, Any]]:
    """The project's sprints (one unpaged list); `state` is open, closed or all (the console's default)."""
    dto, _ = request("GET", project_path("sprints", state=state))
    rows = _object(dto, "sprints").get("sprints")
    if not isinstance(rows, list) or not all(isinstance(s, dict) and _is_number(s.get("number")) for s in rows):
        raise ConsoleError("GET sprints: the answer has no 'sprints' list of numbered sprints",
                           type="unexpected-answer")
    return rows


def _is_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def as_milestone(sprint: Dict[str, Any]) -> Dict[str, Any]:
    """A sprint in the milestone shape `calendar.pick_current_sprint` and the sprint outputs read."""
    due = sprint.get("dueOn")
    return {"number": sprint["number"], "title": sprint_title(sprint), "state": sprint.get("state"),
            "due_on": due if isinstance(due, str) else None}


def _sprint_name(number: int) -> str:
    return f"Sprint {number:02d}"  # what the console titles a sprint created without a title


def sprint_title(sprint: Dict[str, Any]) -> str:
    title = sprint.get("title")
    return title if isinstance(title, str) and title else _sprint_name(sprint["number"])


def text_of(row: Dict[str, Any], name: str) -> Optional[str]:
    """A string member of a console row, or None: rows are data, a member of another type is not trusted."""
    value = row.get(name)
    return value if isinstance(value, str) else None


def as_issue(dto: Dict[str, Any], sprint_titles: Optional[Mapping[int, str]] = None,
             queue: Optional[int] = None, ask: Optional[str] = None) -> Dict[str, Any]:
    """An ItemSummaryDto as the issue dict picker, inbox and metrics read. `milestone` is the title of the item's
    sprint (`Sprint NN` when the title is not known); `queue` is the item's index in a `GET …/items` list, which is
    the console's queue order, so `picker.pick` dispatches in it instead of by number."""
    number = dto.get("number")
    labels = dto.get("labels")
    if not _is_number(number) or not isinstance(labels, list) or not all(isinstance(x, str) for x in labels) \
            or not isinstance(dto.get("title"), str):
        raise ConsoleError("an item in the answer has no number, title or label list", type="unexpected-answer")
    sprint = dto.get("sprint")
    milestone = None
    if _is_number(sprint):
        milestone = (sprint_titles or {}).get(sprint) or _sprint_name(sprint)
    issue = {"number": number, "key": text_of(dto, "key"), "title": dto["title"], "url": item_url(number),
             "labels": list(labels), "kind": text_of(dto, "kind"), "status": text_of(dto, "status"),
             "state": text_of(dto, "state"), "milestone": milestone, "sprint": sprint if _is_number(sprint) else None,
             "closed_at": text_of(dto, "closedAt"), "created_at": text_of(dto, "createdAt"),
             "updated_at": text_of(dto, "updatedAt"), "ask": ask}
    if queue is not None:
        issue["queue"] = queue
    return issue


def issues(sprint_titles: Optional[Mapping[int, str]] = None, **filters: Any) -> List[Dict[str, Any]]:
    """`items(**filters)` as issue dicts, each with its `queue` index in the console's order."""
    return [as_issue(dto, sprint_titles, queue=index)
            for index, dto in enumerate(items(**filters))]


def inbox() -> Dict[str, Any]:
    """`GET …/inbox`, the console's InboxDto: `items` (each with its `section`), `setup`, `paused` and their URLs."""
    dto, _ = request("GET", project_path("inbox"))
    rows = _object(dto, "inbox").get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ConsoleError("GET inbox: the answer has no 'items' list of objects", type="unexpected-answer")
    return dto


def requests(state: str) -> List[Dict[str, Any]]:
    """The project's owner requests; `state` (pending, handled or all) is mandatory for a job."""
    return list_all(project_path("requests", state=state), "requests")


def decisions(since: Optional[str] = None, command: Optional[str] = None) -> List[Dict[str, Any]]:
    return list_all(project_path("decisions", since=since, command=command), "decisions")


def link_pr(number: Union[int, str], pr_number: int, url: str, head_ref: str) -> Dict[str, Any]:
    dto, _ = request("POST", project_path("items", number, "pr-links"),
                     {"number": pr_number, "url": url, "headRef": head_ref})
    return _object(dto, f"pr-link on item {number}")


def pr_link_refusal(exc: ConsoleError) -> Optional[str]:
    """What a refused `pr-links` write means for the job that sent it; None for any other error."""
    if exc.status == 403 and exc.type == "pr-link-role":
        if exc.reason == "item-not-held":
            return ("this job does not hold the item (item-not-held): a developer links a pull request only to an "
                    "item its job was given")
        return "only a developer job or the orchestrator links a pull request to an item"
    if exc.status == 422 and exc.type == "pr-link-invalid":
        return f"the console refused the link ({exc.field or 'body'}): {exc.detail}"
    return None


def instant(value: Any) -> datetime:
    """A console timestamp (`2026-11-12T10:00:00.000Z`) as an aware datetime; the epoch when it is not one."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def query_instant(value: datetime) -> str:
    """A `?since=` value: the UTC instant to the second, the form the console's query validation names."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    """`POST …/items`: title, body, kind, status, severity, tier, sprint and `labels` (flags only) → the ItemDto."""
    dto, _ = request("POST", project_path("items"), payload)
    return _object(dto, "created item")


def comment(number: int, body: str, kind: str = "note") -> Dict[str, Any]:
    """`POST …/items/N/comments`; `kind` is `note` or a team `decision` (the owner's decisions are not comments)."""
    dto, _ = request("POST", project_path("items", number, "comments"), {"kind": kind, "body": body})
    return _object(dto, f"comment on item {number}")


def ask(number: int, category: str, text: str, commands: List[str], recommended: Optional[str]) -> Dict[str, Any]:
    """`POST …/items/N/asks`: the question to the owner; the `owner:<category>` flag is the server's effect."""
    dto, _ = request("POST", project_path("items", number, "asks"),
                     {"category": category, "ask": text, "commands": commands, "recommended": recommended})
    return _object(dto, f"ask on item {number}")


def item_requests(number: int) -> List[Dict[str, Any]]:
    return list_all(project_path("items", number, "requests"), "requests")


def handle_request(request_id: int, result: str, reason: str) -> Dict[str, Any]:
    """`POST …/requests/ID/handled {result, reason}`: the PM's answer; which job answered is the token's."""
    dto, _ = request("POST", project_path("requests", request_id, "handled"), {"result": result, "reason": reason})
    return _object(dto, f"request {request_id}")


def create_sprint(due_on: str, title: Optional[str]) -> Dict[str, Any]:
    """`POST …/sprints`: the console numbers it; `title` None names it `Sprint NN`."""
    dto, _ = request("POST", project_path("sprints"), {"dueOn": due_on, "title": title})
    sprint = _object(dto, "created sprint")
    if not _is_number(sprint.get("number")):
        raise ConsoleError("POST sprints: the answer has no sprint number", type="unexpected-answer")
    return sprint


def project_url() -> Optional[str]:
    """The project's tracker page in the console, or None without TC_CONSOLE_URL."""
    cfg = config()
    return f"{cfg.console_url}/p/{urllib.parse.quote(cfg.project, safe='')}" if cfg.console_url else None


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
