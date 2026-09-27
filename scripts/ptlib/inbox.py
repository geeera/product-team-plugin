"""The owner's single to-do list: one pinned issue whose body every run rewrites from the backlog."""
from __future__ import annotations

from typing import List, Optional

MARKER = "<!-- pt-inbox -->"

# (label or predicate key, section title, the one action the owner takes)
SECTIONS = (
    ("paused", "Team is paused", "Check the failed runs, then comment `/resume` on the run log"),
    ("release", "Release decision", "Answer `/go` or `/no-go <why>` on the demo issue"),
    ("design", "Designs to approve", "Open the link, then `/approve` or `/reject <why>` on the issue"),
    ("question", "Questions", "Answer on the issue; `/approve` accepts the recommended option"),
    ("owner", "Only you can do", "Payment, account or decision described on the issue"),
    ("local", "Needs your machine", "Run locally (e.g. on a Mac); the issue says what"),
)


def classify(issue: dict) -> Optional[str]:
    labels = set(issue.get("labels") or [])
    if "team:demo" in labels:
        return "release"
    if "design:awaiting-approval" in labels:
        return "design"
    if "needs:owner" in labels:
        return "owner"
    if issue.get("kind") == "question":
        return "question"
    if "needs:local" in labels:
        return "local"
    return None


def render(issues: List[dict], paused_url: str = "", updated: str = "") -> str:
    groups = {key: [] for key, _, _ in SECTIONS}
    if paused_url:
        groups["paused"].append({"title": "Run log", "url": paused_url})
    for issue in sorted(issues, key=lambda i: i["number"]):
        key = classify(issue)
        if key:
            groups[key].append(issue)

    total = sum(len(v) for v in groups.values())
    lines = [MARKER, f"**{total} thing{'s' if total != 1 else ''} need{'' if total != 1 else 's'} you.** "
             f"Updated {updated} by the product team; do not edit — it is rewritten every run.", ""]
    if not total:
        lines.append("Nothing needs you right now.")
    for key, title, action in SECTIONS:
        items = groups[key]
        if not items:
            continue
        lines += [f"### {title} ({len(items)})", f"_{action}_", ""]
        for item in items:
            number = f"#{item['number']} " if "number" in item else ""
            lines.append(f"- {number}[{item['title']}]({item['url']})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
