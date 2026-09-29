"""Owner commands in issue comments: /approve, /reject <why>, /go, /no-go <why>, /resume, /override <why>.

A command counts only as the first token of a line of the owner's own prose: not inside a fenced code block, inline
code, a blockquote or an HTML comment, and not in a comment that starts with a team note header (`**Architect
note**`, `**PM grooming**`, …) or a script marker (`<!-- pt-… -->`). In same-account mode the agents write as the
owner's login, so text they quote must never read as the owner's decision.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

from . import provenance

COMMANDS = ("approve", "reject", "go", "no-go", "resume", "override")
# Up to three spaces of indentation: four make an indented code block in Markdown.
_LINE = re.compile(r"^ {0,3}/(approve|reject|go|no-go|resume|override)(?![\w-])[ \t:]*(.*)$", re.IGNORECASE)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_QUOTE = re.compile(r"^ {0,3}>")
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
# A code span: a run of backticks up to the same run, within one paragraph (never across a blank line).
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\1(?!`)", re.DOTALL)
# Headers the team's agents start their issue comments with (reference/workflow.md → Team comments). Keep in
# sync with AGENT_NOTE_ROLES there.
AGENT_NOTE_ROLES = ("Architect", "PM", "Product manager", "UX", "UI", "Designer", "Design", "Developer", "Dev",
                    "QA", "Reviewer", "Review", "Security", "DevOps", "Analyst", "Scribe", "Team")
AGENT_NOTE = re.compile(r"\A\s*(?:#{1,6}[ \t]+)?\*\*(?:%s)\b[^\n]*?\*\*" % "|".join(re.escape(r) for r in AGENT_NOTE_ROLES))
SCRIPT_MARKER = re.compile(r"\A\s*<!-- pt-")
_MASK = "\x00"


def _mask(text: str) -> str:
    return re.sub(r"[^\n]", _MASK, text)


def is_team_note(body: str) -> bool:
    """A comment the team wrote: it starts with an agent note header or a script marker."""
    return bool(AGENT_NOTE.match(body or "") or SCRIPT_MARKER.match(body or ""))


def command_lines(body: str) -> List[tuple]:
    """(command, text) for every line of `body` that is an owner command, in order.

    Everything that is not the owner's own prose is masked with a non-space character first (so a masked
    stretch at the start of a line also stops a command after it), keeping offsets so the text of a command comes
    from the original line.
    """
    body = (body or "").replace("\r\n", "\n")
    if is_team_note(body):
        return []
    lines = body.split("\n")
    masked_lines, fence = [], None
    for line in lines:
        m = _FENCE.match(line)
        if fence is None and m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = m.group(1)
            masked_lines.append(_mask(line))
        elif fence is not None:
            closing = m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip()
            if closing:
                fence = None
            masked_lines.append(_mask(line))
        else:
            masked_lines.append(line)
    masked = "\n".join(masked_lines)
    masked = _HTML_COMMENT.sub(lambda m: _mask(m.group(0)), masked)
    masked = _CODE_SPAN.sub(lambda m: _mask(m.group(0)), masked)
    found = []
    for original, line in zip(lines, masked.split("\n")):
        if _QUOTE.match(line):
            continue
        m = _LINE.match(line)
        if m:
            found.append((m.group(1).lower(), original[m.start(2):].strip()))
    return found


def parse(comments: Iterable[dict], owner: str, history: Optional[dict]) -> List[dict]:
    """Commands from the owner only, oldest first. Anyone else's commands are ignored by design, and so is an owner
    comment someone else edited (history: provenance.fetch of the issue; None or an error = unverifiable edits).
    """
    found = []
    trusted, _ = provenance.screen(comments, [owner], history)
    for c in trusted:
        for command, text in command_lines(c.get("body") or ""):
            found.append(
                {
                    "command": command,
                    "text": text,
                    "at": c.get("created_at") or c.get("createdAt"),
                    "comment_id": c.get("id"),
                    "url": c.get("html_url") or c.get("url"),
                }
            )
    found.sort(key=lambda f: f["at"] or "")
    return found


def rejected(comments: Iterable[dict], owner: str, history: Optional[dict]) -> List[dict]:
    """Owner comments with a command that do not count because someone else edited them (or that is unknown)."""
    with_commands = [c for c in comments if command_lines(c.get("body") or "")]
    return provenance.screen(with_commands, [owner], history)[1]


def latest(commands: List[dict], names: Iterable[str], since: str = "") -> dict:
    """Most recent command among `names` created after `since` (ISO timestamp), or {}."""
    wanted = set(names)
    hits = [c for c in commands if c["command"] in wanted and (c["at"] or "") > since]
    return hits[-1] if hits else {}
