# Product Team plugin — specification

Status: agreed 2026-09-26 (grill session). This is the source of truth for what the plugin does; the plugin
itself is built from it in this repository.

## Goal

The owner hands over a product idea or an existing repository. A team of agents — PM, Architect, Designer,
Fullstack Dev, QA, DevOps, Analyst — develops it on a schedule, in the cloud, without the owner's laptop. The
owner acts as the client: approves designs and scope, reviews a demo every two weeks, decides on releases.
Communication is from the phone.

## Decisions

| #   | Topic | Decision | Rejected / trade-off |
| --- | ----- | -------- | -------------------- |
| 1 | Branches & release | `feature/*` → PR → `dev` (merge after CI + independent QA review) → `stage` (release candidate, cut from `dev` 2 days before a demo; fixes only after that) → `main` (production, only after the owner approves the release at the demo). CI/CD is set up when the repository is created or adopted. | Every PR waiting for the owner (bottleneck on a phone); agent deploying to prod |
| 2 | Team shape | Hybrid. The orchestrator is PM + DevOps; Dev subagents run in parallel in separate worktrees; QA is an independent subagent that sees only the task, acceptance criteria and `stage`, never the dev transcript. Roles are woken only when a task needs them. | Full role-per-subagent every run (3–5× tokens, context lost at each hand-off); one agent playing all roles (QA grades itself) |
| 3 | Schedule | Weekdays, Europe/Kyiv, specialised slots: **~18:00 PM + design** (read the owner's answers, plan, prepare designs for approval) · **~23:00 development** (the one heavy run) · **~04:00 QA + fixes + morning summary**. Freeze days: QA regression on `stage` instead of dev. Mon–Thu conservative task caps; **Fri 23:00 until Sun ~19:00 are "burn" runs** with higher caps and stronger models, since the weekly quota resets on Sunday at 20:00 Kyiv. Times jittered off the hour. | Three full cycles a day (3× cost, needs a lock) |
| 4 | Backlog | GitHub Issues — milestone = sprint, labels = status (`proposed`, `approved`, `in-progress`, `qa`, `done`, `bug`, severity). Accessed only through a backlog adapter (`list / create / move status / link PR`) so another tracker can replace it later. | ROADMAP.md (merge conflicts between parallel agents); a separate task artifact (second source of truth) |
| 5 | Approvals between demos | Designs are approved asynchronously (link to the phone), blocking only their own task; no answer = stays blocked, never auto-approved. Scope (which features exist) is decided at the demo. P0/P1 bugs are fixed immediately without approval. | Everything only at the demo (UI features take 2–4 weeks) |
| 6 | Stack | Chosen per product at kickoff and recorded as a decision. The plugin defines a **stack-agnostic contract** instead of templates: lint/test/build in CI, the branch model, per-environment deploy, e2e tests for QA, the same document structure. | Fixed default stack |
| 7 | Deploy | Managed free tiers (e.g. Vercel/Cloudflare/Netlify, Render/Fly, Neon/Supabase), deploy only via GitHub Actions — cloud agent sessions cannot SSH or call provider CLIs. Containerised from day one so a later move to a VPS is cheap. | Own VPS (all ops on us) |
| 8 | Accounts, secrets, money | A one-time checklist for the owner: create accounts, generate tokens, store them in GitHub Secrets. Agents never see secret values. **Budget $0**: any paid plan, upgrade or domain needs explicit approval; hitting a free-tier limit stops that work and notifies the owner. | Agents operating the owner's logged-in browser |
| 9 | Demo | An interactive demo page (artifact): what shipped with `stage` links and screenshots, QA findings, proposed features and **wow proposals** each with approve / reject / comment, release go / no-go. The next run copies the decisions into Issues; Issues stay the source of truth. | Live chat session at a fixed time; slides |
| 10 | Packaging | A plugin. **Agents = roles** (pm, architect, designer, fullstack-dev, qa, devops, analyst) with their own instructions, tools and limits (e.g. QA cannot edit product code). **Skills = procedures**: `kickoff`, `adopt`, `foundation`, `slot-pm`, `slot-dev`, `slot-qa`, `qa-regression`, `demo-prep`, `demo-apply`. Development runs in code mode; builds on the installed `engineering-baseline`, `design`, `engineering`, `product-management` plugins rather than duplicating them. | One monolithic skill |
| 11 | Foundation | Architecture, patterns and a complete design-token system (colour, type, spacing, radii, shadows, motion, dark theme) before features. The UI kit covers atomic native elements (button, input, textarea, select, checkbox, radio, switch, link, label, icon…) on a headless library; Storybook from day one. **Kit-first for primitives**: a feature that needs a missing primitive adds it to the kit first. Composite domain components stay in their feature until a second use. | An exhaustive kit up front (weeks of work, half discarded) |
| 12 | Wow | Motion tokens and choreography rules in the foundation, `prefers-reduced-motion` respected. At kickoff the owner gives 3–5 references → the designer offers 2–3 visual directions → the owner picks one. 1–3 **signature moments** per product built as interactive prototypes for approval. QA runs a polish checklist (empty states, skeletons, micro-interactions, transitions, 60 fps). Wow proposals per shipped feature appear at every demo. | Rich animation everywhere (noise, perf, a11y) |
| 13 | Models | Pinned IDs, never aliases. **Fable 5.1** (`claude-fable-5-1`): architecture, kickoff, foundation, ADRs. **Opus 5.5** (`claude-opus-5-5`): PM/orchestrator, designer, QA review, security review, dev tasks labelled `complexity:high`, dev in burn runs. **Sonnet 5** (`claude-sonnet-5`): dev, DevOps, regression, analyst. **Haiku 4.5** (`claude-haiku-4-5-20251001`): labels, changelog, summary formatting. Security reviews are not given to Fable (its extra cyber safeguards cause refusals). | Opus everywhere (quota gone mid-week); Fable for every lead role |
| 14 | Findings & release gates | Security Critical/High and UX blockers (core flow cannot be completed, data loss, a11y blocker): fixed in the current sprint without approval, **block the release**. Medium: next sprint, shown on the demo page. Low: backlog. A blocker found during the freeze makes the release no-go automatically; the owner may override with a written reason recorded as a decision. Checks: CI on every PR (SAST, dependency audit, secret scan, a11y in e2e); QA review against OWASP for PRs touching auth/data/payments/input; a designer UX walkthrough of key flows on `stage` each sprint; a deep audit each sprint in the Saturday burn slot. | Medium also blocking; everything decided by the owner |
| 15 | Existing projects | `adopt` instead of `kickoff`: audit (architecture map, conventions actually in force, tests, CI, debt), write `CLAUDE.md`, reuse the project's own decision records instead of adding a parallel one. **The project's conventions beat the plugin's defaults**; deviations are recorded, not "fixed". First sprint = audit + blockers, no features. Employer-owned code only with the employer's permission. | Forcing the plugin's structure onto existing code |
| 16 | Forge | GitHub for everything run in the cloud (gitlab.com is blocked by the cloud network allowlist; repository attachment is GitHub-only). GitLab projects work only in local mode with the laptop on. | Mirroring GitLab → GitHub (two sources of truth) |
| 17 | Communication | **One chat per project** for the owner ↔ team conversation (summaries, design links, questions). Code work happens in code-mode sessions on that project's repository. | One shared chat for all projects |

## Known constraints

- **No branch protection** on private repositories on GitHub's free plan. "Every change arrives as a PR; no
  direct pushes to `main` or `stage`" is enforced by agent instructions only. Owner's decision: live with it.
- **GitHub Actions minutes** (2,000/month on private repos, free plan) are a budget. Cancel superseded PR runs;
  run expensive suites only when their paths changed.
- **Quota**: a run that hits the usage limit fails; all state is in the repository, so the next run resumes.

## Deferred

- A task-manager integration (Linear, Jira…) — cheap later thanks to the backlog adapter.
- Reading the remaining usage quota dynamically — the schedule is static until this is verified.

## Decided while building

- **Stop conditions**: every run writes a `started`/`finished`/`failed` entry on a run-log issue; a dangling
  `started` older than 3 h counts as failed. Three failed runs in a row pause the team until the owner comments
  `/resume`. A run of the same slot still in progress makes the next one exit (overlap guard).
- **Model pinning** (decision 13): plugin agents accept full model IDs in `model:`. Where one role needs two
  models, a variant agent pins the second one (`qa-runner`, `scribe`).
- **Team extended** (amends decisions 2 and 10, 2026-09-27): `designer` is split into `ux-designer` (flow, states,
  copy, wireframe — first) and `ui-designer` (visuals on top; the owner still approves one final design);
  `reviewer` reviews code quality on every PR; `security` takes OWASP reviews, threat models and the deep audit
  from `qa`. Merge requires a review gate enforced in code: CI plus current-head approvals from `qa` and
  `reviewer`, and from `security` when the changed paths or labels call for it. Products add stack specialists
  as their own agents, routed with `agent:<name>`.
- **Model by tier** (amends decision 13, 2026-09-27): the architect sizes each issue; light → Sonnet 5, standard →
  Opus 5.5, heavy → Fable 5.1 (never for security work), with the guard rails in `scripts/ptlib/tiers.py`.
- **Development on Opus** (amends decision 13, 2026-09-27): `fullstack-dev` runs on Opus 5.5 in every mode; the
  separate senior variant is gone. Weekday caps stay conservative to keep the weekly quota.
- **Demo write-back** (decision 9): decisions are stored in the demo artifact's database (only the artifact's
  owner may write) and read by `demo-apply` via ArtifactData. Fallbacks that work without the Artifact tool:
  a `/demo-decisions` block pasted on the demo issue, or `/approve`, `/reject`, `/go`, `/no-go` comments on the
  issues themselves. Issues stay the source of truth.
- **QA verdicts**: all agents act as one GitHub user and GitHub rejects approving your own PR, so QA posts a
  `QA: APPROVED` / `QA: CHANGES REQUESTED` review comment and the orchestrator merges.
- **Owner commands** are honoured only from the repository owner's account.
- **The development plan is code** (`backlog next`): order and caps are tested functions, not re-decided by a
  model each run. Work only a person can do carries `needs:local` / `needs:owner` and leaves the plan.
- **Hotfix path**: P0/P1 live in production → `hotfix/*` from `main`, independent QA, released without waiting
  for the demo, back-merged to `stage` and `dev`; rollback is a PR or a manual deploy of the previous ref.
- **Branch guard** instead of branch protection: a workflow opens a critical issue for the owner when `dev`,
  `stage` or `main` moves without a merged PR. Rulesets need GitHub Pro or a public repository (owner's call).
- **Identity limits** (2026-09-27): every role inside one cloud session shares its tokens, and subagent tool lists
  cannot restrict Bash per command — so "a reviewer cannot write" is only as strong as the environment split.
  The hardening path: a reviewing machine account (`team.reviewer_logins`), its token (`PT_REVIEW_TOKEN`) only in
  the environment that runs `slot-qa`, and releases taken from the demo page rather than comments while the agents
  act as the owner's account.
- **Agent identities** (2026-09-29, amends the above): two GitHub Apps — a team app every script and push acts as,
  and a review app only verdicts are posted as (`reference/identities.md`). Only the review bot's verdicts pass the
  gate, and only comments by the owner's own login, never edited by anyone else, count as owner commands; the team chat records the owner's
  answers with a dedicated `PT_OWNER_TOKEN`, and any session holding a credential of the owner counts as
  same-account. `pr push` goes straight to GitHub as the app and refuses URL rewrites. Keys stay in environment settings; roles inside one session still share
  them, so the environment split remains the boundary. Same-account mode stays supported as the default.
- **Self-update is verified, not trusted**: `pr merge --ci-only` on a `chore/product-team-*` PR re-generates the
  tagged plugin release named in the manifest and requires a byte-identical result; settings files are never part
  of an update.
- **One inbox**: a pinned "Needs you" issue rewritten by every run is the owner's only to-do list.
- **Decision policy** (2026-09-28): the owner decides money, scope, release, access, legal and design approvals —
  nothing else. `backlog` refuses any other question; the team decides the rest and records it (`backlog decide`,
  or a decision record), listed as FYI in the digest and reversible with `/reject`. Every question opens with the
  answer line.
- **Team chat** (implements decision 17, 2026-09-29): the owner never works in GitHub issues. A pinned Claude Code
  session per product is the conversation: "what's new" gives a briefing (`scripts/brief`) and walks through the
  owner's decisions one at a time; plain answers become the commands the team reads (`backlog answer`, marked as
  given in the chat); requests become issues. Scheduled runs cannot write into the chat, so the digest points to it.
- **Daily digest**: GitHub does not notify the owner about comments written with the owner's own account, so the
  inbox goes to the phone once a day through Telegram or ntfy from GitHub Actions (`owner-digest.yml`), and at once
  for what cannot wait.
- **Measured runs**: every run records its duration and counts; `runlog stats` and `sprint-metrics` (plan vs
  shipped, cycle time, QA first-pass rate) feed the demo.
- **How the team reaches the cloud**: cloud sessions never install plugins a repository enables, but they load
  project agents and skills from the clone. `vendor` installs the plugin into the product's `.claude/` (generated
  files with a manifest), and a daily self-update PR follows the `stable` channel or a pinned tag — products update
  themselves, through the same PR flow as everything else. Routines use one repository each (project agents and
  skills load only in single-repository sessions).
- **No `gh` dependency**: cloud images may lack the CLI; scripts use the REST API with the session token.

## To verify before building on it

- How scheduled tasks attach a repository in code mode, and whether a scheduled task can fire into a
  project's persistent chat (decision 17) rather than a fresh session.
- Whether the Artifact / ArtifactData tools exist in cloud runs (`demo-prep` falls back to the demo issue).
- Whether subagents started from a routine honour their pinned `model` (checked by the first smoke run).
