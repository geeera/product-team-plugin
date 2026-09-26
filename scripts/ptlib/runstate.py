"""Run-log state machine: overlap guard and the 3-failures-in-a-row pause."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import List, Optional

MARKER = re.compile(r"<!-- pt-run id=(\S+) slot=(\S+) state=(\S+) -->")
PAUSE_MARKER = "<!-- pt-paused -->"
FAILURE_LIMIT = 3
OVERLAP_WINDOW = timedelta(hours=3)


def parse_runs(comments: List[dict]) -> List[dict]:
    runs = []
    for c in comments:
        m = MARKER.search(c.get("body") or "")
        if m:
            runs.append(
                {
                    "id": m.group(1),
                    "slot": m.group(2),
                    "state": m.group(3),
                    "at": c.get("created_at"),
                    "comment_id": c.get("id"),
                }
            )
    runs.sort(key=lambda r: r["at"] or "")
    return runs


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def effective_state(run: dict, now: datetime) -> str:
    """A `started` run older than the overlap window died (usually on the usage limit): count it as failed."""
    if run["state"] == "started" and now - _ts(run["at"]) >= OVERLAP_WINDOW:
        return "failed"
    return run["state"]


def decide(runs: List[dict], slot: str, now: datetime, paused: bool, reset_at: str = "") -> dict:
    """reset_at: the owner's last /resume; failures before it no longer count."""
    if paused:
        return {"decision": "paused", "reason": "run log is paused; owner must comment /resume"}
    for r in reversed(runs):
        if r["slot"] == slot and effective_state(r, now) == "started":
            return {"decision": "overlap", "reason": f"run {r['id']} of slot {slot} is still in progress"}
    finished = [effective_state(r, now) for r in runs if (r["at"] or "") > reset_at]
    tail = [s for s in finished if s != "started"][-FAILURE_LIMIT:]
    if len(tail) == FAILURE_LIMIT and all(s == "failed" for s in tail):
        return {"decision": "pause", "reason": f"last {FAILURE_LIMIT} runs failed"}
    return {"decision": "proceed", "reason": ""}


def resumed_after_pause(pause_at: Optional[str], owner_commands: List[dict]) -> bool:
    if not pause_at:
        return False
    return any(c["command"] == "resume" and (c["at"] or "") > pause_at for c in owner_commands)
