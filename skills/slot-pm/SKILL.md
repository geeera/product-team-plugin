---
name: slot-pm
description: Scheduled ~18:00 Kyiv PM + design slot — read the owner's answers, unblock or revise designs, apply demo decisions, groom and plan the sprint, cut stage on cut day, prepare designs for approval. Invoked by a scheduled routine on the product repository.
model: claude-opus-5-5
disable-model-invocation: true
---

# Slot: PM + design

Follow `${CLAUDE_PLUGIN_ROOT}/reference/run-protocol.md` with slot name `slot-pm`. You are the orchestrator
(PM + DevOps). `B` = `${CLAUDE_PLUGIN_ROOT}/scripts/backlog`.

## 0. Keep the team current (products with a vendored team only)
Skip if `.claude/product-team/MANIFEST.json` does not exist. `PR` = `${CLAUDE_PLUGIN_ROOT}/scripts/pr`.
1. `PR list --base dev` → an open PR whose head starts with `chore/product-team-`?
   - `PR checks N` is `pass` → `PR merge N --method squash --delete-branch --ci-only`. It changes only generated `.claude/`
     files, so it merges on green CI **without a QA review** (the one exception). Done for today.
   - `fail` → add it to the owner's inbox (comment on the PR, `needs:owner` is not needed: say it in the run
     summary) and stop; `pending` → leave it for the next run. Never open a second update PR.
2. No such PR → `python3 .claude/product-team/scripts/vendor self-update` (follows `team.plugin_ref` in
   `project.yml`: the `stable` channel or a pinned tag). On `updated: true`: branch
   `chore/product-team-<version>` from `dev`, commit `.claude/`, `PR create --base dev` titled
   `chore: product team <version>` with the CHANGELOG entries between the two versions as the body. Do the
   migration step of every **Breaking** entry in the same PR. List `overwrote_local_edits` in the body — those
   fixes belong in the plugin, not here. The PR merges on a later run (step 1); the new team takes effect after.
   Exit code 4 with `conflicts` means the product has its own files with the plugin's names (e.g. its own
   `reviewer.md`): nothing was changed. Open one `kind:question` + `needs:owner` issue titled
   `Product team update blocked` (only if none is open) listing the files, and stop step 0.

## 1. Owner answers
- **Designs**: for each issue labelled `design:awaiting-approval`, `B answers N` (only commands newer than the
  design-link comment count):
  - `/approve` → `B label N +design:approved -design:awaiting-approval`, `B move N approved`.
  - `/reject why` → hand to `ui-designer` (visual) or `ux-designer` (flow) with the reason for a revision (same run if within caps), keep
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
- For approved `needs-design` issues without an approved design (at most 2 per run, 4 in burn):
  1. no `ux-spec` label → `ux-designer` writes the UX spec and wireframe on the issue and adds `ux-spec`;
  2. then `ui-designer` builds the design on that spec and sends it for approval — link comment on the issue,
     `+design:awaiting-approval`, `B move N blocked --reason "waiting for design approval"`.
  The owner approves once, the final design; the UX spec is the team's input, not a second approval.
- Security-relevant `complexity:high` issues: `security` adds a threat model next to the architect's note.
- Issues labelled `agent:<name>` go to that project-specific agent; check `.claude/agents/<name>.md` exists —
  if not, remove the label and say so in the summary.
- **Sizing**: one `architect` call for all approved sprint issues without a `tier:*` label: it reads each issue
  and the code it touches and sets `tier:light`, `tier:standard` or `tier:heavy` with a one-line reason as a
  comment (criteria in `reference/schedule-and-models.md` → Tiers), and adds `security` to issues that touch
  auth, sessions, access control, payments, uploads, personal data, secrets or dependencies. The tier picks the developer's model; it is
  not a priority and not an estimate for the owner.
- For `complexity:high` issues without an architect note (`B next` lists them): `architect` writes the note on
  the issue and adds the `architect-note` label.
- Label work that cloud runs cannot do: `needs:local` (needs a local machine, e.g. a Mac build or device test),
  `needs:owner` (payment, account, legal or product decision). They leave the dev plan and enter the inbox.
- `analyst`: success metric and tracking tasks for newly approved features.

## 4. Stage cut (only when `slot-context` says `is_cut_day`)
`devops` opens PR `dev` → `stage` titled `Stage cut: <sprint>`, listing merged issues. Merge it with
`${CLAUDE_PLUGIN_ROOT}/scripts/pr merge <pr> --method merge --ci-only` when CI is green
(this is the release candidate; the owner approves the release at the demo, not the cut). Everything still
`in-progress` moves to the next sprint milestone. Comment on the `team:demo` issue (create it with
`B create --kind question --label team:demo --title "<sprint> demo" …` if missing) with the stage URL.

## 5. Close
Summary: answers applied, designs waiting for the owner (with links), plan for tonight's dev run, anything
blocked on the owner.
