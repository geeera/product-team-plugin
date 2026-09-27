"""Static checks on the plugin's own files: what `claude plugin validate` would reject, plus the plugin's rules."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"}
PLAIN_SCALAR_TRAPS = re.compile(r": |\s#|^[\[\]{}>|*&!%@`'\"]")


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path}: no frontmatter")
    block = text[4:text.index("\n---", 4)]
    fields = {}
    for line in block.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class ManifestTest(unittest.TestCase):
    def test_manifests_are_valid_json_with_matching_names(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(plugin["name"], market["plugins"][0]["name"])
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$")

    def test_changelog_has_the_current_version(self):
        version = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]
        self.assertIn(f"## {version}", (ROOT / "CHANGELOG.md").read_text())


class FrontmatterTest(unittest.TestCase):
    def files(self):
        return sorted((ROOT / "agents").glob("*.md")) + sorted((ROOT / "skills").glob("*/SKILL.md"))

    def test_every_file_has_name_and_description(self):
        for path in self.files():
            with self.subTest(path=path.name if path.parent.name == "agents" else path.parent.name):
                fields = frontmatter(path)
                self.assertTrue(fields.get("name"))
                self.assertTrue(fields.get("description"))

    def test_plain_descriptions_do_not_break_yaml(self):
        # An unquoted ": " or " #" silently drops all frontmatter at load time.
        for path in self.files():
            desc = frontmatter(path)["description"]
            if desc.startswith('"'):
                json.loads(desc)
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIsNone(PLAIN_SCALAR_TRAPS.search(desc), desc[:80])

    def test_models_are_pinned_ids(self):
        for path in self.files():
            model = frontmatter(path).get("model")
            if model is None:
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIn(model, PINNED_MODELS)

    def test_agent_names_match_files(self):
        for path in sorted((ROOT / "agents").glob("*.md")):
            self.assertEqual(frontmatter(path)["name"], path.stem)

    def test_skill_names_match_folders(self):
        for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
            self.assertEqual(frontmatter(path)["name"], path.parent.name)

    def test_reviewing_agents_cannot_edit(self):
        for name in ("qa", "qa-runner", "reviewer", "security"):
            disallowed = frontmatter(ROOT / "agents" / f"{name}.md").get("disallowedTools", "")
            for tool in ("Edit", "Write"):
                self.assertIn(tool, disallowed, name)

    def test_security_never_runs_on_fable(self):
        self.assertNotIn("fable", frontmatter(ROOT / "agents" / "security.md")["model"])

    def test_referenced_plugin_files_exist(self):
        # ${CLAUDE_PLUGIN_ROOT}/path references in docs must point at real files.
        pattern = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+[\w/])")
        for path in list(ROOT.glob("agents/*.md")) + list(ROOT.glob("skills/*/SKILL.md")) + list(ROOT.glob("reference/*.md")):
            for ref in pattern.findall(path.read_text(encoding="utf-8")):
                with self.subTest(file=str(path.relative_to(ROOT)), ref=ref):
                    self.assertTrue((ROOT / ref).exists())


if __name__ == "__main__":
    unittest.main()
