"""Which issues a development run takes, in which order, with which agent (reference/schedule-and-models.md).

Pure function over the backlog so the choice is deterministic and testable; `backlog next` feeds it.
"""
from __future__ import annotations

from typing import Dict, List, Optional

SKIP_ALWAYS = {"needs:local": "needs a local machine", "needs:owner": "waits for the owner"}
SEV_RANK = {"critical": 0, "high": 1}


def _labels(issue: dict) -> set:
    return set(issue.get("labels") or [])


def _severity(issue: dict) -> Optional[str]:
    for label in _labels(issue):
        if label.startswith("sev:"):
            return label[4:]
    return None


def is_urgent(issue: dict) -> bool:
    """P0/P1 bugs and release blockers bypass caps (SPEC decisions 5, 14); other findings wait their turn."""
    if "release-blocker" in _labels(issue):
        return True
    return issue.get("kind") == "bug" and _severity(issue) in SEV_RANK


def pick(issues: List[dict], sprint: Optional[str], ctx: dict) -> Dict[str, list]:
    """ctx: slot-context output (`mode`, `is_burn`, `caps`)."""
    caps = ctx["caps"]
    freeze = ctx["mode"] == "freeze"
    urgent, rework, planned, skipped, needs_note = [], [], [], [], []

    for issue in issues:
        labels = _labels(issue)
        status = issue.get("status")
        if issue.get("state", "open") != "open" or status not in ("approved", "in-progress"):
            continue
        if issue.get("kind") == "question":
            continue  # answered by the owner, never built
        blocker = next((why for label, why in SKIP_ALWAYS.items() if label in labels), None)
        if blocker:
            skipped.append({"number": issue["number"], "reason": blocker})
            continue
        if status == "in-progress":
            if "qa:changes-requested" in labels:
                rework.append(issue)
            continue  # in progress without a QA verdict: someone else's open PR
        if is_urgent(issue):
            urgent.append(issue)
            continue
        if freeze:
            skipped.append({"number": issue["number"], "reason": "freeze: fixes only"})
            continue
        if issue.get("milestone") != sprint:
            continue
        if "needs-design" in labels and "design:approved" not in labels:
            skipped.append({"number": issue["number"], "reason": "design not approved"})
            continue
        if "complexity:high" in labels and "architect-note" not in labels:
            needs_note.append(issue["number"])
            skipped.append({"number": issue["number"], "reason": "architect note missing"})
            continue
        if "foundation" in labels:
            skipped.append({"number": issue["number"], "reason": "foundation: handled by the foundation skill"})
            continue
        planned.append(issue)

    urgent.sort(key=lambda i: (0 if "in-production" in _labels(i) else 1,
                               SEV_RANK.get(_severity(i) or "", 2), i["number"]))
    rework.sort(key=lambda i: i["number"])
    planned.sort(key=lambda i: i["number"])

    budget = caps["dev_tasks"]
    dispatch = []
    for issue in urgent:
        dispatch.append(_entry(issue, ctx, freeze, "urgent (outside the cap)"))
    for reason, group in (("QA asked for changes", rework), ("planned", planned)):
        for issue in group:
            if budget <= 0:
                skipped.append({"number": issue["number"], "reason": "over the cap for this run"})
                continue
            budget -= 1
            dispatch.append(_entry(issue, ctx, freeze, reason))

    return {"dispatch": dispatch, "skipped": skipped, "needs_architect_note": needs_note,
            "parallel": caps["parallel_devs"]}


def _entry(issue: dict, ctx: dict, freeze: bool, reason: str) -> dict:
    labels = _labels(issue)
    senior = ctx.get("is_burn") or "complexity:high" in labels or "in-production" in labels
    if "in-production" in labels:
        base, prefix = "main", "hotfix"
    elif freeze:
        base, prefix = "stage", "fix"
    else:
        base, prefix = "dev", "feature"
    return {
        "number": issue["number"],
        "title": issue["title"],
        "agent": "fullstack-dev-senior" if senior else "fullstack-dev",
        "base": base,
        "branch_prefix": prefix,
        "reason": reason,
    }
