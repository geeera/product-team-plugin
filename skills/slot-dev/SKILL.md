---
name: slot-dev
description: Scheduled ~23:00 Kyiv development slot (and burn runs Fri 23:00–Sun 19:00) — address QA change requests, then build approved issues with parallel dev subagents in isolated worktrees, one PR each. On freeze days runs qa-regression on stage instead. Invoked by a scheduled routine.
model: claude-opus-5-5
disable-model-invocation: true
---

# Slot: development

Follow `${CLAUDE_PLUGIN_ROOT}/reference/run-protocol.md` with slot name `slot-dev`. `B` =
`${CLAUDE_PLUGIN_ROOT}/scripts/backlog`.

## 0. Route by mode
- `mode == freeze` → run the `qa-regression` skill (within this run protocol) and close. No feature work.
- Open `foundation` issues in the current sprint → run the `foundation` skill for them and close.
- Otherwise continue.

## 1. Pick work (in this order, respecting caps from `slot-context`)
1. P0/P1 bugs and `release-blocker` findings (`sev:critical` / `sev:high`) — outside the cap.
2. `status:in-progress` issues whose PR has a `QA: CHANGES REQUESTED` verdict — fix on the same branch.
3. `status:approved` issues of the current sprint, in milestone order, skipping:
   - `needs-design` without `design:approved`;
   - `complexity:high` without an architect note on the issue (ask `architect` for it first, then include it if
     the cap allows).
Stop at `caps.dev_tasks` (P0/P1 excluded). Do not start work that clearly cannot finish in this run.

## 2. Dispatch
For each picked issue: `B move N in-progress`, then start a dev subagent — **in parallel up to
`caps.parallel_devs`** (several Agent calls in one message):
- `fullstack-dev-senior` when mode is `burn` or the issue has `complexity:high`; otherwise `caps.dev_agent`.
- Prompt: `PLUGIN_ROOT=<path>` line, repo, issue number, target branch (`dev`), the instruction to read the
  issue itself. Nothing else — especially no other issue's context.

Each returns a PR URL or a blocked reason:
- PR → `B link-pr N <pr>`, `B move N qa`.
- Blocked → `B move N blocked --reason "<reason>"`; if the owner must act, open a `kind:question`.

Keep kit-first in mind: if two tasks both need the same missing UI primitive, dispatch the primitive first as its
own `kind:chore` issue and hold the dependants for the next run.

## 3. Quick QA when time allows
If CI of a new PR finishes during this run, you may run the `qa` agent on it (see `slot-qa` step 2) — the 04:00
slot catches the rest. Never merge without a `QA: APPROVED` verdict and green CI.

## 4. Close
Summary: PRs opened (links), issues blocked and why, what `slot-qa` will review.
