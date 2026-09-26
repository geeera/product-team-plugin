---
name: backlog
description: Backlog adapter for the product team — list, create, move status, label, comment, link PRs, read owner answers and manage sprint milestones on GitHub Issues. Use whenever a product-team skill or role needs to read or change the backlog; never call gh issue directly.
---

# Backlog adapter

All backlog access goes through `${CLAUDE_PLUGIN_ROOT}/scripts/backlog`. The subcommands and their JSON output
are the contract; swapping GitHub Issues for another tracker means replacing that script, not the skills.
Conventions (statuses, kinds, severities, flags, milestones) are in
`${CLAUDE_PLUGIN_ROOT}/reference/workflow.md`.

| Need | Command |
| ---- | ------- |
| Set up labels in a repository (idempotent) | `backlog init` |
| Current sprint | `backlog sprint current` |
| Create a sprint | `backlog sprint create "Sprint 03" 2026-10-23` (due = demo day) |
| What is ready to build | `backlog list --status approved --milestone current` |
| Blocked on the owner | `backlog list --status blocked` |
| One issue with comments | `backlog show 42` |
| New issue | `backlog create --title "…" --kind feature --body-file /tmp/body.md [--status proposed] [--severity high] [--label needs-design] [--milestone current]` |
| Change status | `backlog move 42 in-progress [--reason "…"]` (`done` closes the issue) |
| Flags | `backlog label 42 +design:approved -design:awaiting-approval` |
| Move to a sprint | `backlog milestone 42 "Sprint 04"` (`current`, or `none` to remove) |
| Close without doing it | `backlog close 42 --reason "owner rejected at demo"` (not planned) |
| Comment | `backlog comment 42 --body-file /tmp/c.md` |
| Record a PR | `backlog link-pr 42 57` |
| Owner's commands | `backlog answers 42` → `/approve`, `/reject why`, `/go`, `/no-go why`, `/resume`, `/override why` |

Rules:
- Exactly one `status:*` label per issue — only `move` changes it.
- Only the repository owner's commands count; `answers` already filters out everyone else.
- Write bodies to a temp file and use `--body-file` for anything longer than a line (no shell-quoting bugs).
- Requires `gh` authenticated for the repository (`PT_REPO=owner/repo` overrides the checkout's repo).
