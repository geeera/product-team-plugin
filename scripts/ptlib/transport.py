"""HTTP discipline shared by the API clients (gh.py, console.py): one wall-clock deadline per call, bodies read in
chunks so a trickle cannot outlast it, a cap on how much of an answer is held in memory, and one retry for calls
that are safe to repeat. No identity logic: callers build their own requests and errors and decide what may be
retried.
"""
from __future__ import annotations

import http.client
import io
import os
import socket
import time
from typing import Any, Callable, Optional, Type, TypeVar

DEFAULT_DEADLINE = 90.0
SOCKET_TIMEOUT = 60.0  # per blocking socket operation, never beyond what is left of the deadline
RETRY_BACKOFF = 2.0
CHUNK = 64 * 1024
ERROR_BODY_LIMIT = 500  # an error message quotes at most this much of a response body
# Above any answer the plugin reads: a pull request diff, or a console page of 100 comments at their 64 K-char cap.
RESPONSE_LIMIT = 32 * 1024 * 1024

T = TypeVar("T")


class Deadline(Exception):
    """The request's wall-clock budget ran out while the answer was still arriving."""


class TooLarge(Exception):
    """The answer is longer than the caller's limit; nothing past the limit was read."""

    def __init__(self, limit: int):
        super().__init__(f"the answer is over {limit} bytes")
        self.limit = limit


class Retryable(Exception):
    """A failure a repeatable call may be retried after (a timeout or a 5xx); `error` is what the caller raises."""

    def __init__(self, error: Exception):
        super().__init__(str(error))
        self.error = error


def deadline_seconds(error: Type[Exception]) -> float:
    """PT_HTTP_DEADLINE: the wall-clock budget of one API call, retry included (default 90 s)."""
    raw = os.environ.get("PT_HTTP_DEADLINE", "").strip()
    if not raw:
        return DEFAULT_DEADLINE
    try:
        value = float(raw)
    except ValueError:
        raise error(f"PT_HTTP_DEADLINE must be a number of seconds, not {raw!r}") from None
    if not value > 0:
        raise error(f"PT_HTTP_DEADLINE must be positive, not {raw!r}")
    return value


def remaining(deadline: float) -> float:
    """What is left of the deadline; Deadline when nothing is."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise Deadline()
    return left


def is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError))


def clip(text: str, limit: int = ERROR_BODY_LIMIT) -> str:
    return text[:limit]


def read_until(resp: Any, deadline: float, socket_timeout: float = SOCKET_TIMEOUT,
               limit: Optional[int] = None) -> bytes:
    """The body, read in chunks so a slow trickle cannot outlast the deadline (a socket timeout is per read), and
    never more than `limit` bytes of it (RESPONSE_LIMIT by default): a longer answer is TooLarge."""
    cap = RESPONSE_LIMIT if limit is None else limit
    chunks = []
    size = 0
    while True:
        left = remaining(deadline)
        sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(min(socket_timeout, left))
        # One byte past the cap is enough to know the answer is over it.
        want = min(CHUNK, cap - size + 1)
        chunk = resp.read1(want) if hasattr(resp, "read1") else resp.read(want)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > cap:
            raise TooLarge(cap)
        chunks.append(chunk)


class _DeadlineReader(io.RawIOBase):
    """A socket's byte stream whose every read waits at most what is left of the deadline."""

    def __init__(self, sock: socket.socket, deadline: float, socket_timeout: float):
        super().__init__()
        self.sock, self.deadline, self.socket_timeout = sock, deadline, socket_timeout
        # The unbuffered SocketIO holds a reference: the socket stays open while the response reads, even after
        # http.client closes a connection it hands over to a `Connection: close` response.
        self.stream = sock.makefile("rb", buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> Optional[int]:
        self.sock.settimeout(min(self.socket_timeout, remaining(self.deadline)))
        return self.stream.readinto(buffer)

    def close(self) -> None:
        if not self.closed:
            self.stream.close()
        super().close()


class _DeadlineSocket:
    """What http.client.HTTPResponse needs of a socket (makefile) with a deadline on each read."""

    def __init__(self, sock: socket.socket, deadline: float, socket_timeout: float):
        self.sock, self.deadline, self.socket_timeout = sock, deadline, socket_timeout

    def makefile(self, mode: str, *_args: Any, **_kwargs: Any) -> io.BufferedReader:
        return io.BufferedReader(_DeadlineReader(self.sock, self.deadline, self.socket_timeout))


def deadline_response(deadline: float, socket_timeout: float = SOCKET_TIMEOUT) -> Callable[..., Any]:
    """An http.client `response_class` that reads the status line and headers, not only the body, within the
    deadline: a per-operation socket timeout alone lets a trickle of header bytes run on indefinitely."""
    def response(sock: socket.socket, *args: Any, **kwargs: Any) -> http.client.HTTPResponse:
        wrapped: Any = _DeadlineSocket(sock, deadline, socket_timeout)
        return http.client.HTTPResponse(wrapped, *args, **kwargs)

    return response


def network_failure(exc: BaseException, error: Exception, deadline: float) -> Exception:
    """What a caller raises for a failed socket operation: a timeout is Retryable (Deadline once the budget is
    spent); anything else is `error` itself."""
    if not is_timeout(exc):
        return error
    if time.monotonic() >= deadline:
        return Deadline()
    return Retryable(error)


def run(attempt: Callable[[], T], *, attempts: int, deadline: float, on_deadline: Callable[[], Exception]) -> T:
    """`attempt` up to `attempts` times: again only after a Retryable, and only when the backoff fits the deadline.

    Deadline → on_deadline(); the last Retryable → its error. Anything else propagates untouched.
    """
    for number in range(attempts):
        try:
            return attempt()
        except Deadline:
            raise on_deadline() from None
        except Retryable as exc:
            wait = RETRY_BACKOFF * (number + 1)
            if number + 1 >= attempts or deadline - time.monotonic() <= wait:
                raise exc.error from exc.__cause__
            time.sleep(wait)
    raise AssertionError("unreachable")  # the loop returns or raises
