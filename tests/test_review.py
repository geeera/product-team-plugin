try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import project, review  # noqa: E402

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


class AgentToolingTest(unittest.TestCase):
    def test_files_that_steer_agents_or_the_gate_need_security(self):
        for path in (".claude/settings.json", ".claude/agents/qa.md", ".claude/product-team/scripts/pr",
                     ".claude/product-team/scripts/ptlib/review.py", ".product-team/project.yml", ".mcp.json",
                     "CLAUDE.md", "AGENTS.md", ".github/CODEOWNERS", "apps/web/.claude/settings.json",
                     "apps/web/.claude/agents/x.md", "CLAUDE.local.md", "apps/api/CLAUDE.md"):
            with self.subTest(path=path):
                self.assertTrue(review.security_reasons([path]))


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


class ReviewAppTest(unittest.TestCase):
    BOT = "acme-review[bot]"

    def test_review_app_is_the_only_reviewer_even_if_project_yml_lists_more(self):
        self.assertEqual(review.allowed_reviewers(["someone", self.BOT], self.BOT), ([self.BOT], ""))

    def test_warns_when_project_yml_would_disagree_without_the_key(self):
        allowed, warning = review.allowed_reviewers(["old-machine-account"], self.BOT)
        self.assertEqual(allowed, [self.BOT])
        self.assertIn(self.BOT, warning)

    def test_without_the_review_app_project_yml_decides(self):
        self.assertEqual(review.allowed_reviewers([self.BOT], None), ([self.BOT], ""))
        self.assertEqual(review.allowed_reviewers([], None), ([], ""))

    def test_gate_counts_only_the_review_bots_verdicts(self):
        team, owner = {"login": "acme-team[bot]"}, {"login": "geeera"}
        reviews = [dict(rv("QA: APPROVED"), user=team), dict(rv("REVIEW: APPROVED"), user=owner),
                   dict(rv("QA: APPROVED"), user={"login": self.BOT})]
        allowed, _ = review.allowed_reviewers([], self.BOT)
        result = review.gate(reviews, HEAD, False, allowed)
        self.assertEqual(result["missing"], ["REVIEW: no verdict"])
        self.assertEqual(set(result["verdicts"]), {"QA"})

    def test_unguarded_warning_names_same_account_only_when_it_is(self):
        self.assertIn("same-account mode", review.unguarded_warning(True))
        self.assertNotIn("same-account", review.unguarded_warning(False))
        self.assertIn("review app", review.unguarded_warning(False))


class ReviewPolicyTest(unittest.TestCase):
    """The `review:` block of project.yml (read without a YAML parser)."""

    def test_absent_block_keeps_both_verdicts_on_every_pr(self):
        for text in ("", "name: x\nteam:\n  reviewer_logins: []\n", "# review:\n#   reviewer: never\n"):
            with self.subTest(text=text):
                policy = project.review_policy_from_text(text)
                self.assertEqual((policy["configured"], policy["qa"], policy["reviewer"]), (False, "always", "always"))

    def test_reads_the_template_block(self):
        text = ("name: x\nreview:\n  qa: always                 # QA on every PR\n  reviewer: code\n"
                "  code_paths: [\"apps/**\", 'libs/**']   # globs\n  max_rework_rounds: 2\nowner:\n  language: en\n")
        policy = project.review_policy_from_text(text)
        self.assertEqual(policy, {"configured": True, "qa": "always", "reviewer": "code",
                                  "code_paths": ["apps/**", "libs/**"], "max_rework_rounds": 2})

    def test_block_lists_comments_and_defaults(self):
        text = ("review:\n  # proportional\n  reviewer: code\n  code_paths:\n    - apps/**\n\n"
                "    - \"packages/*/src/**\"\nsprint:\n  x: 1\n")
        policy = project.review_policy_from_text(text)
        self.assertEqual(policy["code_paths"], ["apps/**", "packages/*/src/**"])
        self.assertEqual((policy["qa"], policy["max_rework_rounds"]), ("always", 1))

    def test_code_without_code_paths_uses_broad_defaults(self):
        policy = project.review_policy_from_text("review:\n  reviewer: code\n")
        self.assertIn("apps/**", policy["code_paths"])
        self.assertEqual(review.code_changes(["src/x.py", "tools/gen.ts", "docs/a.md"], policy["code_paths"]),
                         ["src/x.py", "tools/gen.ts"])

    def test_a_nested_review_key_elsewhere_is_not_the_block(self):
        policy = project.review_policy_from_text("team:\n  review:\n    reviewer: never\n")
        self.assertFalse(policy["configured"])

    def test_unreadable_values_raise(self):
        for text in ("review:\n  reviewer: sometimes\n", "review:\n  qa: yes\n", "review:\n  code_paths: apps/**\n",
                     "review:\n  code_paths: []\n", "review:\n  max_rework_rounds: many\n",
                     "review:\n  reviewers: code\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    project.review_policy_from_text(text)

    def test_a_second_review_block_or_key_is_an_error(self):
        for text in ("review:\n  reviewer: code\nowner:\n  x: 1\nreview:\n  reviewer: always\n",
                     "review:\n  reviewer: code\nreview: {}\n",
                     "review:\n  reviewer: code\n  reviewer: always\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    project.review_policy_from_text(text)

    def test_hash_inside_quotes_is_text_and_outside_is_a_comment(self):
        text = ("review:\n  reviewer: 'code'   # note\n"
                "  code_paths: [\"apps/#web/**\", 'libs/**', \"a,b/**\"]  # a comment, with a comma\n")
        policy = project.review_policy_from_text(text)
        self.assertEqual(policy["reviewer"], "code")
        self.assertEqual(policy["code_paths"], ["apps/#web/**", "libs/**", "a,b/**"])
        block = "review:\n  code_paths:\n    - \"apps/#1/**\"   # first\n    - libs/**#literal\n"
        self.assertEqual(project.review_policy_from_text(block)["code_paths"], ["apps/#1/**", "libs/**#literal"])

    def test_an_unclosed_quote_is_an_error(self):
        for text in ("review:\n  code_paths: [\"apps/**]\n", "review:\n  reviewer: 'code\n",
                     "review:\n  code_paths:\n    - \"apps/**\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    project.review_policy_from_text(text)

    def test_the_template_parses_to_reviewer_code(self):
        text = (Path(__file__).resolve().parents[1] / "templates" / "project.yml").read_text()
        policy = project.review_policy_from_text(text)
        self.assertEqual((policy["configured"], policy["qa"], policy["reviewer"]), (True, "always", "code"))
        self.assertIn("apps/**", policy["code_paths"])
        self.assertEqual(project.reviewer_logins_from_text(text), [])


class CodePathsTest(unittest.TestCase):
    def test_globs(self):
        cases = [("apps/**", "apps/web/src/page.tsx", True), ("apps/**", "apps.md", False),
                 ("apps/**", "docs/apps/x.md", False), ("apps/", "apps/x.ts", True),
                 ("*.ts", "tools/deep/gen.ts", True), ("*.ts", "x.tsx", False),
                 ("packages/*/src/**", "packages/ui/src/a/b.ts", True),
                 ("packages/*/src/**", "packages/ui/docs/a.md", False),
                 ("**/migrations/**", "apps/api/migrations/1.sql", True), ("/libs/**", "libs/a.ts", True),
                 ("src/?.ts", "src/a.ts", True), ("src/?.ts", "src/ab.ts", False)]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(review.glob_regex(pattern).match(path)), expected)

    def test_required_verdicts_explain_themselves(self):
        policy = project.review_policy_from_text("review:\n  reviewer: code\n  code_paths: [apps/**]\n")
        roles, why = review.required_verdicts(policy, [".github/workflows/ci.yml"], ["dependencies or CI: ci.yml"])
        self.assertEqual(roles, ["QA", "SECURITY"])
        self.assertIn("no changed path matches code_paths (apps/**)", why["REVIEW"]["why"])
        roles, why = review.required_verdicts(policy, [f"apps/{i}.ts" for i in range(5)], [])
        self.assertEqual(roles, ["QA", "REVIEW"])
        self.assertIn("(+2 more)", why["REVIEW"]["why"])

    def test_a_pr_beyond_githubs_file_listing_counts_as_code(self):
        policy = project.review_policy_from_text("review:\n  reviewer: code\n  code_paths: [apps/**]\n")
        roles, why = review.required_verdicts(policy, ["docs/a.md"] * review.PR_FILES_LIMIT, [])
        self.assertIn("REVIEW", roles)
        self.assertIn("3000", why["REVIEW"]["why"])

    def test_default_code_paths_cover_scripts_infra_markup_and_config(self):
        paths = ["deploy/run.sh", "Makefile", "infra/main.tf", "web/index.html", "web/a.css", "web/b.scss",
                 "x/mod.mts", "x/mod.cts", "site/page.astro", "ui/App.svelte", "ui/App.svelte.ts",
                 "vite.config.ts", "tailwind.config.js", "tsconfig.json", "tsconfig.base.json", "SRC/Main.PY",
                 "tools/Build.SH"]
        self.assertEqual(review.code_changes(paths, project.DEFAULT_CODE_PATHS), paths)
        self.assertEqual(review.code_changes(["docs/a.md", "README.md", ".github/CODEOWNERS", "notes.txt"],
                                             project.DEFAULT_CODE_PATHS), [])

    def test_matching_is_case_insensitive_and_dot_slash_is_ignored(self):
        self.assertTrue(review.glob_regex("apps/**").match("Apps/Web/Page.TSX"))
        self.assertTrue(review.glob_regex("./apps/**").match("apps/x.ts"))
        self.assertTrue(review.glob_regex("././libs/").match("libs/x.ts"))

    def test_unsupported_glob_syntax_is_an_error(self):
        for pattern in ("apps/{web,api}/**", "src/[ab].ts", "!docs/**", "", "./"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ValueError):
                    review.glob_regex(pattern)
        with self.assertRaises(ValueError):
            project.review_policy_from_text("review:\n  reviewer: code\n  code_paths: [\"apps/{web,api}/**\"]\n")
        with self.assertRaises(ValueError):
            project.review_policy_from_text("review:\n  reviewer: code\n  code_paths:\n    - '!docs/**'\n")

    def test_gate_takes_the_roles(self):
        self.assertTrue(review.gate([rv("QA: APPROVED")], HEAD, False, roles=["QA"])["passed"])
        self.assertEqual(review.gate([], HEAD, True, roles=[])["required"], ["SECURITY"])


if __name__ == "__main__":
    unittest.main()
