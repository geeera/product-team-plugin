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

## 1. Plan
`B next` returns the plan for this run — deterministic, from the backlog and `slot-context`:
- `dispatch`: ordered issues with `agent`, `base` branch and `branch_prefix`. Order: production defects,
  P0/P1 bugs and release blockers (outside the cap), then QA rework (`qa:changes-requested`), then approved
  sprint work up to the cap.
- `skipped`: with the reason (design not approved, over the cap, `needs:local`, `needs:owner`, freeze).
- `needs_architect_note`: `complexity:high` issues without a note — ask `architect` for the notes first
  (it adds the `architect-note` label); they are picked up by the next run.
Do not re-order or add to the plan by judgement. If the plan looks wrong, say why in the run summary and fix
the backlog labels instead.

## 2. Dispatch
- Entries with `branch_prefix: hotfix` go through the `hotfix` skill, one at a time, before anything else.
- The rest: `B move N in-progress`, then start the named agent — **in parallel up to `parallel`** (several
  Agent calls in one message). Prompt: `PLUGIN_ROOT=<path>` line, repo, issue number, base branch, branch
  prefix, and the instruction to read the issue itself. Nothing else — especially no other issue's context.
- Rework entries (`base: null`): read the branch and base from the linked PR (`${CLAUDE_PLUGIN_ROOT}/scripts/pr view <pr>`), pass them to the
  agent; it continues on the PR's existing branch and removes `qa:changes-requested` only
  through the orchestrator (`B label N -qa:changes-requested`) once the new commits are pushed.

Each agent returns a PR URL or a blocked reason:
- PR → `B link-pr N <pr>`, `B move N qa`.
- Blocked → `B move N blocked --reason "<reason>"`; if the owner must act, add `needs:owner` (or
  `needs:local` for work that needs a local machine) so it lands in the owner's inbox and later runs skip it.

Kit-first: if two dispatched tasks need the same missing UI primitive, run the primitive first as its own
`kind:chore` issue and hold the dependants for the next run.

## 3. Quick QA when time allows
If CI of a new PR finishes during this run, you may run the `qa` agent on it (see `slot-qa` step 2) — the 04:00
slot catches the rest. Never merge without a `QA: APPROVED` verdict and green CI.

## 4. Close
Summary: PRs opened (links), issues blocked and why, what `slot-qa` will review. Metrics for the run log:
`--metric prs=<opened> --metric blocked=<n> --metric skipped=<n>`.
