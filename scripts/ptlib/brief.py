"""What the owner hears when they open the team chat: what changed since last time, and what needs them."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import inbox, owner

BRIEFED = re.compile(r"<!-- pt-briefed at=(\S+) -->")
ANSWERABLE = ("approve", "reject", "go", "no-go", "override", "resume")


def briefed_at(comments: List[dict]) -> Optional[str]:
    stamps = [m.group(1) for c in comments for m in [BRIEFED.search(c.get("body") or "")] if m]
    return max(stamps) if stamps else None


def briefed_marker(at: str) -> str:
    return f"<!-- pt-briefed at={at} -->\nThe owner was last briefed in the team chat at {at}."


def needs(open_issues: List[dict]) -> List[dict]:
    """Everything waiting for the owner, in the inbox's order, with the answer line when there is one."""
    order = {key: i for i, key in enumerate(inbox.ORDER)}
    found = []
    for issue in open_issues:
        key = inbox.classify(issue)
        if key:
            found.append({"section": key, "number": issue["number"], "title": issue["title"], "url": issue["url"],
                          "ask": issue.get("ask") or owner.ask_of(issue.get("body") or "")})
    return sorted(found, key=lambda n: (order.get(n["section"], 99), n["number"]))


def answer_comment(command: str, text: str) -> str:
    """The owner's answer, given in the team chat, written as the command the team already understands."""
    if command not in ANSWERABLE:
        raise ValueError(f"unknown answer {command!r}; one of {', '.join(ANSWERABLE)}")
    if command in ("reject", "no-go", "override") and not text.strip():
        raise ValueError(f"/{command} needs the owner's reason")
    line = f"/{command} {text.strip()}".rstrip()
    return f"{line}\n\n_Answered by the owner in the team chat._\n"


def summary(since: Optional[str], closed: List[dict], merged: List[dict], decided: List[dict],
            runs: List[dict], open_issues: List[dict], sprint: Optional[dict], paused: bool) -> Dict[str, object]:
    def after(ts: Optional[str]) -> bool:
        return bool(ts) and (since is None or ts > since)

    done = [i for i in closed if after(i.get("closed_at")) and "status:done" in i.get("labels", [])]
    recent_runs = [r for r in runs if after(r.get("at"))]
    return {
        "since": since,
        "paused": paused,
        "sprint": sprint,
        "shipped": [{"number": i["number"], "title": i["title"], "url": i["url"]} for i in done],
        "merged_prs": [{"number": p["number"], "title": p["title"], "url": p["url"]} for p in merged if after(p.get("merged_at"))],
        "team_decisions": [{"number": d["number"], "title": d["title"], "url": d["url"]} for d in decided if after(d.get("updated_at"))],
        "runs": {"total": len(recent_runs), "failed": sum(1 for r in recent_runs if r.get("state") == "failed")},
        "needs_you": needs(open_issues),
    }
