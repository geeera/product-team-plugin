try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import socket
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import transport  # noqa: E402


class Chunks:
    def __init__(self, *chunks):
        self.chunks, self.asked = list(chunks), []

    def read1(self, size):
        self.asked.append(size)
        return self.chunks.pop(0)[:size] if self.chunks else b""


class ReadUntilTest(unittest.TestCase):
    def test_an_answer_at_the_limit_is_read_and_one_byte_more_is_too_large(self):
        later = time.monotonic() + 30
        self.assertEqual(transport.read_until(Chunks(b"abc", b"de"), later, limit=5), b"abcde")
        with self.assertRaises(transport.TooLarge) as caught:
            transport.read_until(Chunks(b"abc", b"def"), later, limit=5)
        self.assertEqual(caught.exception.limit, 5)

    def test_never_asks_for_more_than_one_byte_past_the_limit(self):
        answer = Chunks(b"x" * 10)
        with self.assertRaises(transport.TooLarge):
            transport.read_until(answer, time.monotonic() + 30, limit=3)
        self.assertEqual(answer.asked, [4])

    def test_a_spent_deadline_reads_nothing(self):
        answer = Chunks(b"x")
        with self.assertRaises(transport.Deadline):
            transport.read_until(answer, time.monotonic() - 1)
        self.assertEqual(answer.asked, [])


class NetworkFailureTest(unittest.TestCase):
    def test_a_timeout_is_retryable_until_the_deadline_is_spent(self):
        error = RuntimeError("timed out")
        retry = transport.network_failure(socket.timeout(), error, time.monotonic() + 30)
        self.assertIsInstance(retry, transport.Retryable)
        self.assertIs(retry.error, error)
        self.assertIsInstance(transport.network_failure(TimeoutError(), error, time.monotonic() - 1),
                              transport.Deadline)

    def test_any_other_failure_is_the_callers_error(self):
        error = RuntimeError("refused")
        self.assertIs(transport.network_failure(ConnectionRefusedError(), error, time.monotonic() + 30), error)


if __name__ == "__main__":
    unittest.main()
