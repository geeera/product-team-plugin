---
name: demo-apply
description: Copy the owner's demo decisions into Issues and act on them — approve or reject proposals, record comments, release stage to main on go (or record no-go/override), close the sprint and open the next. Used by slot-pm after a demo; Issues stay the source of truth.
model: claude-opus-5-5
---

# Demo apply

`B` = `${CLAUDE_PLUGIN_ROOT}/scripts/backlog`, `D` = `${CLAUDE_PLUGIN_ROOT}/scripts/demo-page`.
Skip entirely if the demo issue already has a `<!-- pt-demo-applied -->` comment.

## 1. Read decisions (first source that has any wins; merge per item, newest wins)
1. Artifact database — when the demo issue records an artifact URL and `ArtifactData` is available: `list` the
   `decisions` collection, save the rows to a file, `D from-db rows.json`.
2. `D decisions <demo item>` — the owner's decisions in the console since the demo item was opened: `release`
   (the newest go / no-go / override on the demo item) and the newest approve / reject per item under `issue-<n>`.
   Apply those only to the proposals the demo page listed; anything else there was decided outside the demo.
3. `B answers <n>` on an item for the decision's text and `id`.
Decisions are console rows only the owner's identity writes, so there is nothing to screen. No decision on an item =
leave it as it is. Every decision you apply goes into the run's `runlog finish` as `--acted N:<decision id>`.

## 2. Apply
- Proposal `approve` → the console already approved it; `B milestone N "<next sprint>"`. `reject` → `B close N --reason "rejected at
  demo: <comment>"` (a non-zero exit that still carries `partial` on stdout means the close is stored and the
  reason note is not: run `partial.next`, never rerun `close`). Comment only → add it to the issue.
- Feedback on a shipped item → comment on that issue; if it describes a defect, `B create --kind bug` with the
  severity `pm` assigns.
- **Release**:
  - `go` with no open `release-blocker` → `devops` opens PR `stage` → `main` (`Release <sprint>`), merge on green
    CI with `scripts/pr merge N --method merge --ci-only`; `scribe` writes the changelog/release notes; tag `v<date>` or the project's scheme.
  - `go` while a blocker is open **with** an override reason → record a decision (in `decisions_dir`: blocker,
    reason, owner, date), then release as above.
  - `go` while a blocker is open **without** a reason → do not release; ask on the demo issue for the reason.
  - `no-go` or no answer → no release; carry the reason into the next sprint plan.
- After a release or a no-go: back-merge `stage` → `dev` if they diverged (`scripts/pr merge N --method merge
  --ci-only`; if the back-merge has conflicts, resolve them on a `backmerge/*` branch — that PR goes through the
  full review gate, since conflict resolutions are code).

## 3. Roll the sprint
Close the finished milestone; `B sprint create "Sprint NN+1" <demo day + 14>` if missing; unfinished issues move
to it. `B comment` `<!-- pt-demo-applied -->` with a short list of what was applied on the demo item (the console
closed it on the release decision).
