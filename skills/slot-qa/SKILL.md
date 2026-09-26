---
name: slot-qa
description: Scheduled ~04:00 Kyiv QA slot — independent QA review of open PRs, merge approved ones to dev, quick fixes, finding triage, the Saturday deep audit, demo-page preparation on demo day, and the owner's morning summary. Invoked by a scheduled routine.
model: claude-opus-5-5
disable-model-invocation: true
---

# Slot: QA + fixes + morning summary

Follow `${CLAUDE_PLUGIN_ROOT}/reference/run-protocol.md` with slot name `slot-qa`. `B` =
`${CLAUDE_PLUGIN_ROOT}/scripts/backlog`.

## 1. Freeze
If `mode == freeze`, run the `qa-regression` skill first (it covers `fix/*` PRs to `stage`), then continue with
step 2 for any PRs to `dev` that are still open.

## 2. Review PRs (`status:qa` issues)
For each linked open PR, oldest first:
1. `gh pr checks <pr>` — pending: skip (next slot); red: comment the failing job on the PR,
   `B move N in-progress --reason "CI red: <job>"`.
2. Green → start the **`qa` agent** with only: `PLUGIN_ROOT=<path>`, repo, PR number, issue number. Never pass
   the developer's summary or transcript. Independent reviews may run in parallel (up to 3).
3. Verdict:
   - `QA: APPROVED` → `gh pr merge <pr> --squash --delete-branch`, `B move N done`.
   - `QA: CHANGES REQUESTED` → `B move N in-progress`. If the requested changes are small and the issue is P0/P1,
     dispatch `fullstack-dev` now on the same branch, then one more `qa` pass. Otherwise `slot-dev` picks it up.
4. Findings the reviewer filed outside the PR's scope stay in the backlog with their severity.

## 3. Triage
Review new `kind:finding` / `kind:bug` issues since the last run: severity set, `release-blocker` on security
Critical/High and UX blockers, blockers placed in the current sprint (`B move N approved` — no owner approval
needed), Medium in the next sprint, Low in the backlog.

## 4. Deep audit — Saturday burn slot only
When `slot-context` is `burn` on a Saturday and no deep audit ran this sprint (look for a run-log summary with
"deep audit" in the current sprint), run in parallel:
- `qa`: security audit of the whole `dev` branch (OWASP, dependency audit, secrets in history, authz paths).
- `designer`: UX walkthrough of the key flows on `stage` (or `dev` before the cut) per
  `reference/qa-checklists.md`.
- `analyst`: sprint metrics for the demo.
- `architect` is **not** used for the security part (Fable refusals); it may review debt and structure.
All output becomes findings through `B create`.

## 5. Demo day
If today is the sprint's demo date (`slot-context.demo_date`), run the `demo-prep` skill after the reviews.

## 6. Morning summary
Hand the facts to the `scribe` agent to format; post it as the run summary (run-log comment) and print it. It
contains, in this order:
1. **Needs you** — designs waiting, questions, cost approvals, release decision (links, one line each).
2. Shipped to `dev` overnight (issue → PR).
3. Blockers and P0/P1 status.
4. What the team does next.
Keep it readable on a phone: no tables wider than three columns, no internal jargon.
