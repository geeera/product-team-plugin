---
name: slot-pm
description: Scheduled ~18:00 Kyiv PM + design slot — read the owner's answers, unblock or revise designs, apply demo decisions, groom and plan the sprint, cut stage on cut day, prepare designs for approval. Invoked by a scheduled routine on the product repository.
model: claude-opus-5-5
disable-model-invocation: true
---

# Slot: PM + design

Follow `${CLAUDE_PLUGIN_ROOT}/reference/run-protocol.md` with slot name `slot-pm`. You are the orchestrator
(PM + DevOps). `B` = `${CLAUDE_PLUGIN_ROOT}/scripts/backlog`.

## 1. Owner answers
- **Designs**: for each issue labelled `design:awaiting-approval`, `B answers N` (only commands newer than the
  design-link comment count):
  - `/approve` → `B label N +design:approved -design:awaiting-approval`, `B move N approved`.
  - `/reject why` → hand to `designer` with the reason for a revised design (same run if within caps), keep
    `status:blocked`.
  - nothing → leave it blocked. Never approve on the owner's behalf.
- **Questions**: for each open `kind:question`, read answers and act (e.g. a cost approved → unblock the
  dependent issue; `/reject` → record and close with `B close`).
- **Demo**: if the current or previous sprint's `team:demo` issue has decisions that were not applied yet, run
  the `demo-apply` skill.

## 2. Groom
Delegate to `pm` with the sprint state (`B list --milestone current`, `B list --status proposed`):
- every `approved` issue has acceptance criteria, correct flags (`needs-design`, `complexity:high`), size that
  fits; split what does not fit;
- new bugs/findings triaged by severity; P0/P1 go to the top of the current sprint;
- the sprint still fits the remaining dev runs at the caps in `reference/schedule-and-models.md`; overflow moves
  to the next sprint (tell the owner in the summary).

## 3. Prepare
- For approved `needs-design` issues without an approved design (at most 2 per run, 4 in burn): `designer`
  produces the design and sends it for approval — link comment on the issue, `+design:awaiting-approval`,
  `B move N blocked --reason "waiting for design approval"`.
- For `complexity:high` issues without an architect note: `architect` writes the note on the issue.
- `analyst`: success metric and tracking tasks for newly approved features.

## 4. Stage cut (only when `slot-context` says `is_cut_day`)
`devops` opens PR `dev` → `stage` titled `Stage cut: <sprint>`, listing merged issues. Merge it when CI is green
(this is the release candidate; the owner approves the release at the demo, not the cut). Everything still
`in-progress` moves to the next sprint milestone. Comment on the `team:demo` issue (create it with
`B create --kind question --label team:demo --title "<sprint> demo" …` if missing) with the stage URL.

## 5. Close
Summary: answers applied, designs waiting for the owner (with links), plan for tonight's dev run, anything
blocked on the owner.
