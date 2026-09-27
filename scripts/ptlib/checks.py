"""Summarise a commit's CI: check runs and legacy commit statuses → pass / fail / pending."""
from __future__ import annotations

from typing import List

FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}


def summarise(check_runs: List[dict], statuses: List[dict]) -> dict:
    failing, pending, passing = [], [], []
    latest = {}
    for run in check_runs:  # a re-run creates a new check run with the same name; keep the newest
        name = run.get("name", "?")
        if name not in latest or (run.get("started_at") or "") > (latest[name].get("started_at") or ""):
            latest[name] = run
    for name, run in sorted(latest.items()):
        if run.get("status") != "completed":
            pending.append(name)
        elif run.get("conclusion") in FAILED:
            failing.append(name)
        else:
            passing.append(name)  # success, neutral, skipped
    seen = set()
    for st in statuses:  # newest first from the API; the first per context wins
        ctx = st.get("context", "?")
        if ctx in seen:
            continue
        seen.add(ctx)
        {"success": passing, "pending": pending}.get(st.get("state"), failing).append(ctx)
    state = "fail" if failing else "pending" if pending or not (passing or failing) else "pass"
    return {"state": state, "failing": failing, "pending": pending, "passing": passing}
