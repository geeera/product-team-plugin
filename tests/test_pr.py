import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
loader = importlib.machinery.SourceFileLoader("pr_cli", str(ROOT / "scripts" / "pr"))
spec = importlib.util.spec_from_loader("pr_cli", loader)
pr = importlib.util.module_from_spec(spec)
loader.exec_module(pr)


def pull(head_repo="o/r"):
    return {"head": {"repo": {"full_name": head_repo} if head_repo else None}}


class DeleteBranchTest(unittest.TestCase):
    def check(self, head, base="dev", head_repo="o/r"):
        with mock.patch.object(pr.gh, "api", return_value=pull(head_repo)):
            return pr.branch_deletion_blocker("o/r", 1, {"head": head, "base": base})

    def test_feature_branch_of_this_repo_is_deleted(self):
        self.assertEqual(self.check("feature/7-x"), "")

    def test_long_lived_branches_are_kept(self):
        for head, base in (("dev", "stage"), ("stage", "main"), ("main", "stage")):
            with self.subTest(head=head):
                self.assertIn("long-lived", self.check(head, base))

    def test_fork_branches_are_never_deleted_here(self):
        self.assertIn("lives in", self.check("feature/7-x", head_repo="someone/r"))
        self.assertIn("deleted fork", self.check("feature/7-x", head_repo=None))


class MergeMethodTest(unittest.TestCase):
    def test_method_is_required(self):
        with mock.patch.object(sys, "argv", ["pr", "merge", "5"]), self.assertRaises(SystemExit):
            pr.main()

    def test_review_must_name_the_reviewed_commit(self):
        with mock.patch.object(sys, "argv", ["pr", "review", "5", "--body", "QA: APPROVED"]), self.assertRaises(SystemExit):
            pr.main()


if __name__ == "__main__":
    unittest.main()
