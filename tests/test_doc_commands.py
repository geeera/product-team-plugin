"""Every script command the agents are told to run must exist with the flags they are told to pass."""
try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import functools
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {"backlog", "pr", "runlog", "inbox", "vendor", "demo-page", "sprint-metrics", "slot-context"}
# Short aliases the skills define: `B` = backlog, `PR` = pr, `D` = demo-page.
ALIASES = {"B": "backlog", "PR": "pr", "D": "demo-page"}
SPAN = re.compile(r"`([^`]+)`")
CALL = re.compile(r"(?:scripts/([\w-]+)|^(B|PR|D))\s+([a-z][\w-]*)?((?:\s+\S+)*)")


@functools.lru_cache(maxsize=None)
def help_text(script: str, sub: str = "") -> str:
    args = [sys.executable, str(ROOT / "scripts" / script)] + ([sub] if sub else []) + ["--help"]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    return proc.stdout + proc.stderr


def calls():
    docs = list(ROOT.glob("agents/*.md")) + list(ROOT.glob("skills/*/SKILL.md")) + list(ROOT.glob("reference/*.md"))
    for doc in docs:
        for span in SPAN.findall(doc.read_text(encoding="utf-8")):
            m = CALL.search(span.strip())
            if not m:
                continue
            script = m.group(1) or ALIASES[m.group(2)]
            if script not in SCRIPTS:
                continue
            yield doc.relative_to(ROOT), span, script, m.group(3), re.findall(r"(--[\w-]+)", m.group(4) or "")


class DocCommandsTest(unittest.TestCase):
    def test_subcommands_and_flags_exist(self):
        checked = 0
        for doc, span, script, sub, flags in calls():
            top = help_text(script)
            with self.subTest(doc=str(doc), span=span):
                if sub and "{" in top:  # the script has subcommands
                    choices = re.search(r"\{([^}]*)\}", top).group(1).split(",")
                    self.assertIn(sub, choices, f"{script} has no subcommand {sub!r}")
                    text = help_text(script, sub)
                else:
                    text = top
                for flag in flags:
                    self.assertIn(flag, text, f"{script} {sub or ''} has no flag {flag}")
                checked += 1
        self.assertGreater(checked, 20)


if __name__ == "__main__":
    unittest.main()
