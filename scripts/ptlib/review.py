"""The review gate: which independent verdicts a PR needs before it may merge, and whether it has them."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

ROLES = ("QA", "REVIEW", "SECURITY")
_VERDICT = re.compile(r"^\s*(QA|REVIEW|SECURITY):\s*(APPROVED|CHANGES REQUESTED)\b", re.IGNORECASE)

# Paths whose change makes a security review mandatory. Deliberately broad: a false positive costs one review,
# a false negative ships an unreviewed auth or payment change.
SENSITIVE = re.compile(
    r"(^|[/_.-])(auth|oauth|sso|login|session|token(?!s[./])|jwt|password|passwd|credential|secret|crypto|encrypt|"
    r"permission|rbac|acl|role|guard|policy|middleware|cors|csp|security|"
    r"payment|purchase|billing|checkout|refund|invoice|subscription|wallet|"
    r"upload|webhook|admin|backoffice|account|user[-_]?data|pii|gdpr|deletion)",
    re.IGNORECASE,
)
DEPENDENCY_FILES = re.compile(
    r"(^|/)(package\.json|pnpm-lock\.yaml|package-lock\.json|yarn\.lock|requirements[^/]*\.txt|pyproject\.toml|"
    r"uv\.lock|poetry\.lock|Pipfile(\.lock)?|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock|Gemfile(\.lock)?|"
    r"pubspec\.yaml|pubspec\.lock|Dockerfile[^/]*|docker-compose[^/]*\.ya?ml)$|(^|/)\.github/workflows/",
    re.IGNORECASE,
)


def security_reasons(paths: Iterable[str], labels: Iterable[str] = ()) -> List[str]:
    reasons = []
    if "security" in set(labels):
        reasons.append("issue labelled security")
    for path in paths:
        if SENSITIVE.search(path):
            reasons.append(f"sensitive path: {path}")
        elif DEPENDENCY_FILES.search(path):
            reasons.append(f"dependencies or CI: {path}")
    return reasons


def verdict(body: str) -> Optional[tuple]:
    m = _VERDICT.match(body or "")
    return (m.group(1).upper(), m.group(2).upper()) if m else None


def latest_verdicts(reviews: List[dict], head_sha: str) -> Dict[str, dict]:
    """Newest verdict per role; `current` is False when it was given on an older commit than the head."""
    found: Dict[str, dict] = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        v = verdict(r.get("body") or "")
        if v:
            found[v[0]] = {"verdict": v[1], "current": r.get("commit_id") == head_sha, "at": r.get("submitted_at")}
    return found


def gate(reviews: List[dict], head_sha: str, security_required: bool) -> dict:
    required = ["QA", "REVIEW"] + (["SECURITY"] if security_required else [])
    verdicts = latest_verdicts(reviews, head_sha)
    missing = []
    for role in required:
        v = verdicts.get(role)
        if not v:
            missing.append(f"{role}: no verdict")
        elif v["verdict"] != "APPROVED":
            missing.append(f"{role}: changes requested")
        elif not v["current"]:
            missing.append(f"{role}: approved an older commit")
    return {"required": required, "verdicts": verdicts, "missing": missing, "passed": not missing}
