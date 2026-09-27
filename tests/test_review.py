import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import review  # noqa: E402

HEAD = "abc"


def rv(body, sha=HEAD, at="2026-09-27T10:00:00Z"):
    return {"body": body, "commit_id": sha, "submitted_at": at}


class SecurityRelevanceTest(unittest.TestCase):
    def test_sensitive_paths_need_security(self):
        for path in ("apps/api/src/auth/auth.controller.ts", "apps/api/src/purchases/apple-jws.ts",
                     "libs/shared/staff-session/index.ts", "apps/api/src/uploads/presign.ts"):
            with self.subTest(path=path):
                self.assertTrue(review.security_reasons([path]))

    def test_dependencies_and_ci_need_security(self):
        for path in ("package.json", "apps/worker/pyproject.toml", ".github/workflows/ci.yml", "apps/api/Dockerfile"):
            with self.subTest(path=path):
                self.assertTrue(review.security_reasons([path]))

    def test_plain_ui_change_does_not(self):
        self.assertEqual(review.security_reasons(["apps/studio/src/app/stories/stories.page.ts", "README.md"]), [])

    def test_design_tokens_are_not_auth_tokens(self):
        self.assertEqual(review.security_reasons(["apps/studio/src/styles/tokens.scss",
                                                  "apps/reader/lib/src/shared/ui/tokens/colors.dart"]), [])
        self.assertTrue(review.security_reasons(["apps/api/src/auth/refresh-token.service.ts"]))

    def test_label_forces_it(self):
        self.assertEqual(review.security_reasons([], ["security"]), ["issue labelled security"])


class GateTest(unittest.TestCase):
    def test_passes_with_current_approvals(self):
        result = review.gate([rv("QA: APPROVED"), rv("REVIEW: APPROVED")], HEAD, security_required=False)
        self.assertTrue(result["passed"])

    def test_security_required_when_relevant(self):
        result = review.gate([rv("QA: APPROVED"), rv("REVIEW: APPROVED")], HEAD, security_required=True)
        self.assertEqual(result["missing"], ["SECURITY: no verdict"])

    def test_approval_of_an_older_commit_does_not_count(self):
        result = review.gate([rv("QA: APPROVED", sha="old"), rv("REVIEW: APPROVED")], HEAD, False)
        self.assertEqual(result["missing"], ["QA: approved an older commit"])

    def test_latest_verdict_wins(self):
        reviews = [rv("REVIEW: APPROVED", at="2026-09-27T09:00:00Z"),
                   rv("REVIEW: CHANGES REQUESTED\n1. duplicate helper", at="2026-09-27T11:00:00Z"),
                   rv("QA: APPROVED")]
        self.assertEqual(review.gate(reviews, HEAD, False)["missing"], ["REVIEW: changes requested"])

    def test_verdict_must_open_the_review(self):
        self.assertIsNone(review.verdict("Looks fine. QA: APPROVED"))
        self.assertEqual(review.verdict("qa: approved — all criteria met"), ("QA", "APPROVED"))


if __name__ == "__main__":
    unittest.main()
