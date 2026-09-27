import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import tiers  # noqa: E402


def eff(labels, burn=False, hotfix=False):
    return tiers.effective(labels, burn=burn, hotfix=hotfix)[0]


class TierTest(unittest.TestCase):
    def test_architect_label_decides(self):
        self.assertEqual(eff(["tier:light"]), "light")
        self.assertEqual(eff(["tier:heavy"]), "heavy")

    def test_missing_or_conflicting_label_is_standard(self):
        self.assertEqual(eff([]), "standard")
        self.assertEqual(eff(["tier:light", "tier:heavy"]), "standard")

    def test_two_failed_reviews_raise_one_step(self):
        self.assertEqual(eff(["tier:light", "tier-up"]), "standard")
        self.assertEqual(eff(["tier:standard", "tier-up"]), "heavy")
        self.assertEqual(eff(["tier:heavy", "tier-up"]), "heavy")

    def test_burn_and_hotfix_have_no_light_work(self):
        self.assertEqual(eff(["tier:light"], burn=True), "standard")
        self.assertEqual(eff(["tier:light"], hotfix=True), "standard")

    def test_security_work_never_runs_on_fable(self):
        self.assertEqual(eff(["tier:heavy", "security"]), "standard")
        self.assertEqual(eff(["tier:standard", "tier-up", "security"]), "standard")

    def test_every_tier_has_an_agent_file_with_its_model(self):
        root = Path(__file__).resolve().parents[1] / "agents"
        for tier, agent in tiers.AGENTS.items():
            with self.subTest(tier=tier):
                text = (root / f"{agent}.md").read_text()
                self.assertIn(f"model: {tiers.MODELS[tier]}", text)

    def test_variants_share_the_developer_procedure(self):
        root = Path(__file__).resolve().parents[1] / "agents"
        body = lambda name: (root / f"{name}.md").read_text().split("\n---\n", 1)[1]  # noqa: E731
        for agent in ("fullstack-dev-light", "fullstack-dev-heavy"):
            with self.subTest(agent=agent):
                self.assertEqual(body(agent), body("fullstack-dev"))


if __name__ == "__main__":
    unittest.main()
