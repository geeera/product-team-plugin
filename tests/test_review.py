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


class MorePathsTest(unittest.TestCase):
    def test_paths_the_review_found_missing(self):
        for path in (".github/actions/setup/action.yml", ".gitleaksignore", ".env.example", "apps/api/drizzle/0007_x.sql",
                     "apps/api/src/sheets/internal-sheet-images.controller.ts", "apps/reader/ios/Podfile.lock",
                     "apps/reader/android/app/build.gradle.kts", "firestore.rules", "bun.lockb"):
            with self.subTest(path=path):
                self.assertTrue(review.security_reasons([path]))

    def test_false_positives_the_review_found(self):
        for path in (".cspell.json", "docs/authors.md", "apps/backoffice/src/favicon.ico", "apps/backoffice/src/styles.scss"):
            with self.subTest(path=path):
                self.assertEqual(review.security_reasons([path]), [])


class CiOnlyTest(unittest.TestCase):
    def test_release_flow_operations_are_allowed(self):
        for head, base in (("dev", "stage"), ("stage", "main"), ("stage", "dev"), ("main", "stage"), ("main", "dev")):
            with self.subTest(pair=(head, base)):
                self.assertEqual(review.ci_only_allowed(head, base, ["x"]), "")

    def test_skipping_stage_or_arbitrary_branches_is_refused(self):
        self.assertTrue(review.ci_only_allowed("dev", "main", []))
        self.assertTrue(review.ci_only_allowed("feature/7-x", "dev", []))

    def test_self_update_only_into_dev_and_only_under_claude(self):
        self.assertEqual(review.ci_only_allowed("chore/product-team-0.4.0", "dev", [".claude/agents/qa.md"]), "")
        self.assertTrue(review.ci_only_allowed("chore/product-team-0.4.0", "main", [".claude/agents/qa.md"]))
        self.assertTrue(review.ci_only_allowed("chore/product-team-x", "dev", [".claude/a.md", "apps/api/src/app.ts"]))


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

    def test_verdict_must_be_the_whole_first_line(self):
        self.assertIsNone(review.verdict("Looks fine. QA: APPROVED"))
        self.assertIsNone(review.verdict("QA: APPROVED (conditional on CI): do not merge until"))
        self.assertEqual(review.verdict("qa: approved"), ("QA", "APPROVED"))

    def test_conditional_approval_does_not_pass_the_gate(self):
        reviews = [rv("QA: APPROVED (conditional on CI)\nwait for e2e"), rv("REVIEW: APPROVED")]
        self.assertEqual(review.gate(reviews, HEAD, False)["missing"], ["QA: no verdict"])

    def test_pending_draft_reviews_do_not_count(self):
        reviews = [dict(rv("QA: APPROVED"), state="PENDING"), rv("REVIEW: APPROVED")]
        self.assertEqual(review.gate(reviews, HEAD, False)["missing"], ["QA: no verdict"])

    def test_only_configured_reviewer_logins_count(self):
        reviews = [dict(rv("QA: APPROVED"), user={"login": "owner"}), dict(rv("REVIEW: APPROVED"), user={"login": "team-bot"})]
        self.assertEqual(review.gate(reviews, HEAD, False, ["team-bot"])["missing"], ["QA: no verdict"])


if __name__ == "__main__":
    unittest.main()
