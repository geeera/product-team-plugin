---
name: kickoff
description: Start a new product from an idea — interview the owner, choose the stack, create the GitHub repository contract, owner checklist, visual direction and first sprint. Use when the owner hands over a new product idea (not an existing repository; for that use adopt).
model: claude-fable-5-1
argument-hint: "<product idea or path to a brief>"
disable-model-invocation: true
---

# Kickoff — new product

Goal: from an idea to a repository where the scheduled team can start `foundation`. Interactive: the owner is
present for this one. Read `${CLAUDE_PLUGIN_ROOT}/reference/workflow.md`, `stack-contract.md`,
`design-system.md` and `schedule-and-models.md` first.

## 1. Understand the product (PM)
Interview the owner **one question at a time, each with your recommended answer** (use the `grill-me` style).
Cover: problem and audience, the core flow, what "v1 is useful" means, platforms (web / mobile web / PWA),
data and privacy, integrations, non-goals. Stop when you can write a one-page brief. Save it as
`docs/product-brief.md`.

## 2. Choose the stack (architect)
Delegate to the `architect` agent with the brief. It proposes 2–3 stacks that satisfy
`reference/stack-contract.md` on **free tiers** (hosting, DB, auth, analytics), with free-tier limits listed.
The owner picks. Record it as the first decision record (`docs/decisions/0001-stack.md`), including rejected
options and the limits to watch.

## 3. Visual direction (designer)
Ask the owner for 3–5 references (links or screenshots). Delegate to `designer`: 2–3 visual directions, each as
a small interactive HTML page (palette, type, a sample screen, one motion sample) published as an artifact when
the Artifact tool is available, otherwise committed under `docs/design/directions/`. The owner picks one →
decision record. Ask the owner which 1–3 moments should be **signature moments**; create them as
`kind:feature` + `signature-moment` + `needs-design` issues.

## 4. Repository and contract (devops)
The owner creates the GitHub repository (agents cannot create accounts). Then, via PRs where the repo already
has commits:
- Branches `main`, `stage`, `dev`; ask the owner to set `dev` as the default branch.
- `.product-team/project.yml` from `${CLAUDE_PLUGIN_ROOT}/templates/project.yml` (`mode: new`).
- `.product-team/owner-checklist.md` from the template, filled with the accounts and secrets the stack needs.
- `.claude/settings.json` from `${CLAUDE_PLUGIN_ROOT}/templates/claude-settings.json` so cloud sessions load
  this plugin.
- `CLAUDE.md`: how to work in this repo — commands, structure, conventions, links to decisions.
- `backlog init`; `backlog sprint create "Sprint 01" <demo day, two weeks out, a weekday>`.

## 5. First sprint = foundation
Create `kind:chore` issues, `status:approved` (the owner approved the kickoff), milestone Sprint 01:
architecture skeleton, CI contract + security workflow, deploy dev/stage/prod, tokens, UI-kit primitives,
Storybook, e2e harness. `foundation` executes them.

## 6. Schedule
Show the owner the routines to create (from `reference/schedule-and-models.md`): prompts
`/product-team:slot-pm`, `/product-team:slot-dev`, `/product-team:slot-qa` on this repository, with local times
converted to the routine's timezone. Creating routines is the owner's action (or `/schedule` on their
confirmation).

## Output
A short summary for the owner: brief, stack decision, chosen direction, checklist items waiting for them, the
sprint and its demo date.
