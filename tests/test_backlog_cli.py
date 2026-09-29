try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
loader = importlib.machinery.SourceFileLoader("backlog_cli", str(ROOT / "scripts" / "backlog"))
spec = importlib.util.spec_from_loader("backlog_cli", loader)
backlog = importlib.util.module_from_spec(spec)
loader.exec_module(backlog)


class LabelArgsTest(unittest.TestCase):
    def run_cli(self, *argv):
        seen = {}
        with mock.patch.object(sys, "argv", ["backlog", *argv]), \
             mock.patch.object(backlog.gh, "repo", return_value="o/r"), \
             mock.patch.object(backlog, "cmd_label", side_effect=lambda repo, args: seen.update(changes=args.changes)):
            backlog.main()
        return seen["changes"]

    def test_label_removal_is_not_read_as_an_option(self):
        self.assertEqual(self.run_cli("label", "7", "-needs:owner"), ["-needs:owner"])

    def test_mixed_add_and_remove(self):
        self.assertEqual(self.run_cli("label", "7", "+design:approved", "-design:awaiting-approval"),
                         ["+design:approved", "-design:awaiting-approval"])


class BodyFileTest(unittest.TestCase):
    def test_missing_body_file_exits_cleanly(self):
        args = type("A", (), {"body_file": "/nonexistent/x.md", "body": None})()
        with self.assertRaises(SystemExit) as caught:
            backlog.body_arg(args)
        self.assertIn("cannot read --body-file", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
