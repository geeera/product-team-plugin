"""HTTP discipline shared by the API clients (gh.py, console.py): one wall-clock deadline per call, bodies read in
chunks so a trickle cannot outlast it, and one retry for calls that are safe to repeat. No identity logic: callers
build their own requests and errors and decide what may be retried.
"""
from __future__ import annotations

import os
import socket
import time
from typing import Any, Callable, Type, TypeVar

DEFAULT_DEADLINE = 90.0
SOCKET_TIMEOUT = 60.0  # per blocking socket operation, never beyond what is left of the deadline
RETRY_BACKOFF = 2.0
CHUNK = 64 * 1024
ERROR_BODY_LIMIT = 500  # an error message quotes at most this much of a response body

T = TypeVar("T")


class Deadline(Exception):
    """The request's wall-clock budget ran out while the answer was still arriving."""


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


def is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError))


def clip(text: str, limit: int = ERROR_BODY_LIMIT) -> str:
    return text[:limit]


def read_until(resp: Any, deadline: float, socket_timeout: float = SOCKET_TIMEOUT) -> bytes:
    """The body, read in chunks so a slow trickle cannot outlast the deadline (a socket timeout is per read)."""
    chunks = []
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise Deadline()
        sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(min(socket_timeout, left))
        chunk = resp.read1(CHUNK) if hasattr(resp, "read1") else resp.read(CHUNK)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


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
