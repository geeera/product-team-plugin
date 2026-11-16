"""Demo page data: filling the template and reading back the owner's decisions."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .console import instant

START, END = "/*PT-DEMO-DATA-START*/", "/*PT-DEMO-DATA-END*/"
REQUIRED = ("product", "repo", "sprint", "demo_date", "demo_issue", "release", "shipped", "proposals", "findings")
RELEASE_COMMANDS = ("go", "no-go", "override")
PROPOSAL_COMMANDS = ("approve", "reject")


def fill(template: str, data: dict) -> str:
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ValueError(f"demo data is missing keys: {', '.join(missing)}")
    if START not in template or END not in template:
        raise ValueError("template has no demo data markers")
    # `</` inside a <script> would end it early; JSON allows the escaped form.
    payload = json.dumps(dict(data, example=False), ensure_ascii=False, indent=2).replace("</", "<\\/")
    head, rest = template.split(START, 1)
    _, tail = rest.split(END, 1)
    return head + START + payload + END + tail


def _newest(rows: List[dict], commands: tuple) -> Optional[dict]:
    found = [r for r in rows if r.get("command") in commands]
    return max(found, key=lambda r: instant(r.get("decidedAt"))) if found else None


def _text(row: dict, name: str) -> str:
    value = row.get(name)
    return value if isinstance(value, str) else ""


def decisions_from_rows(demo_number: int, rows: List[Dict[str, Any]], source: Optional[str]) -> Optional[dict]:
    """The owner's demo decisions from the console's decision feed since the demo item was opened (rows only the
    owner's identity writes): the newest go/no-go/override on the demo item as `release`, the newest approve/reject
    on each other item under `issue-<n>`, in the shape the page's /demo-decisions block had. None without any."""
    by_item: Dict[int, List[dict]] = {}
    for row in rows:
        item = row.get("item")
        number = item.get("number") if isinstance(item, dict) else None
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            by_item.setdefault(number, []).append(row)
    decisions: Dict[str, dict] = {}
    release = _newest(by_item.pop(demo_number, []), RELEASE_COMMANDS)
    if release:
        decisions["release"] = {"decision": release["command"], "comment": _text(release, "text")}
    for number in sorted(by_item):
        found = _newest(by_item[number], PROPOSAL_COMMANDS)
        if found:
            decisions[f"issue-{number}"] = {"decision": found["command"], "comment": _text(found, "text"),
                                            "key": _text(found["item"], "key") or None}
    if not decisions:
        return None
    return {"release": {"command": release["command"], "text": _text(release, "text"),
                        "at": _text(release, "decidedAt") or None} if release else None,
            "decisions": decisions, "source": source}


def normalise_db_rows(rows: List[dict]) -> dict:
    """ArtifactData `list` rows of the `decisions` collection → the same shape as the comment block."""
    out = {}
    for row in rows:
        body = row.get("data") if isinstance(row.get("data"), dict) else row
        item = body.get("item") or row.get("id")
        if item:
            out[item] = {"decision": body.get("decision"), "comment": body.get("comment") or ""}
    return {"decisions": out, "source": "artifact-db"}
