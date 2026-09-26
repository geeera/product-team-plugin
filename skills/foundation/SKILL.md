---
name: foundation
description: Build a new product's foundation before any feature — architecture skeleton, CI contract, per-environment deploy, complete design tokens, UI-kit primitives on a headless library, Storybook and the e2e harness. Use for issues labelled foundation (Sprint 01 after kickoff); slot-dev hands them here.
model: claude-fable-5-1
---

# Foundation

Runs inside the `slot-dev` run protocol (`${CLAUDE_PLUGIN_ROOT}/reference/run-protocol.md`) — it does not open
or close the run log itself. Read `reference/stack-contract.md` and `reference/design-system.md`.

Work the open `foundation` issues in this order; each is one PR to `dev`, reviewed by `qa` like any other.
Parallelise only where marked.

1. **Architecture skeleton** (`architect`): project structure per the stack decision and the owner's baseline
   (Feature-Sliced Design on the frontend, shared-first), error handling, config/env access, logging, API
   conventions, one vertical "hello" slice proving the layers. Decision records for each non-obvious choice.
2. **CI + security** (`devops`, parallel with 3): `ci.yml` and `security.yml` from
   `${CLAUDE_PLUGIN_ROOT}/templates/workflows/` filled for the stack; `Dockerfile`; commands written into
   `.product-team/project.yml`.
3. **Tokens** (`designer`, parallel with 2): the complete token set from `design-system.md` for the chosen visual
   direction — colour (light + dark), type, spacing, radii, shadows, z-index, breakpoints, motion with
   reduced-motion fallbacks. A lint rule against raw values where the stack allows.
4. **UI kit + Storybook** (`designer` for look, `fullstack-dev` for behaviour): primitives on the headless
   library, each with stories for variants, states, both themes and reduced motion. Storybook deploys with `dev`.
5. **Deploy** (`devops`): dev / stage / production from Actions on the chosen free tiers; URLs into
   `project.yml`. Missing accounts or secrets → owner checklist + `kind:question`, and the issue waits.
6. **E2E harness** (`fullstack-dev`): runs against `BASE_URL`, a11y assertions built in (axe or the stack's
   equivalent), one smoke test on the hello slice. Wired into CI with a paths filter.
7. **Signature-moment prototypes** (`designer`): interactive prototypes for the `signature-moment` issues, sent for
   approval (`design:awaiting-approval`, `status:blocked`).

Foundation is done when every contract item in `stack-contract.md` holds on `dev`. Features start in the next
run after that; until then `slot-dev` keeps handing `foundation` issues here.
