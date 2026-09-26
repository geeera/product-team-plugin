"""The label set of reference/workflow.md. `backlog init` makes a repository match it."""
from __future__ import annotations

STATUSES = ("proposed", "approved", "in-progress", "qa", "done", "blocked")
KINDS = ("feature", "bug", "chore", "finding", "wow", "question")
SEVERITIES = ("critical", "high", "medium", "low")

LABELS = {
    "status:proposed": ("c5def5", "Proposed; waits for the owner's scope decision"),
    "status:approved": ("0e8a16", "Approved; ready for development"),
    "status:in-progress": ("fbca04", "A developer is on it"),
    "status:qa": ("5319e7", "PR open, waiting for independent QA"),
    "status:done": ("0e8a16", "Merged to dev after QA"),
    "status:blocked": ("b60205", "Waiting for the owner or an external dependency; see last comment"),
    "kind:feature": ("1d76db", "User-facing capability"),
    "kind:bug": ("d73a4a", "Defect"),
    "kind:chore": ("cfd3d7", "Maintenance, infra, tooling"),
    "kind:finding": ("e99695", "QA / security / UX finding"),
    "kind:wow": ("f9a8d4", "Delight proposal for the demo"),
    "kind:question": ("d876e3", "Question or approval request for the owner"),
    "sev:critical": ("b60205", "P0: exploitable, data loss, core flow broken for all"),
    "sev:high": ("d93f0b", "P1: serious; blocks release if security or UX blocker"),
    "sev:medium": ("fbca04", "Next sprint; shown at the demo"),
    "sev:low": ("c2e0c6", "Backlog"),
    "complexity:high": ("5319e7", "Architect note first; senior dev model"),
    "needs-design": ("bfdadc", "Needs an approved design before development"),
    "design:awaiting-approval": ("fef2c0", "Design link sent to the owner"),
    "design:approved": ("0e8a16", "Owner approved the design"),
    "security": ("b60205", "Security-relevant"),
    "ux-blocker": ("b60205", "Core flow cannot be completed / data loss / a11y blocker"),
    "release-blocker": ("000000", "Release is no-go while open"),
    "foundation": ("0b1f33", "Foundation work (architecture, tokens, UI kit, CI) before features"),
    "signature-moment": ("f9a8d4", "One of the product's 1-3 signature moments"),
    "team:run-log": ("ededed", "Team run log (one issue per repository)"),
    "team:paused": ("b60205", "Team runs paused; owner comments /resume to continue"),
    "team:demo": ("0052cc", "Sprint demo and release decision"),
}
