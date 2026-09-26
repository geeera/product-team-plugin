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
- Stop conditions (e.g. three failed runs in a row → pause and notify) — decided while writing the slot skills.

## To verify before building on it

- How scheduled tasks attach a repository in code mode, and whether a scheduled task can fire into a
  project's persistent chat (decision 17) rather than a fresh session.
- Whether each role subagent can be pinned to its own model inside a plugin (decision 13).
- Whether an interactive demo page can write approvals somewhere the next run can read (decision 9).

## Projects

| Project | Repository | Status |
| ------- | ---------- | ------ |
| storify | `geeera/storify` (moved from GitLab 2026-09-26) | CI on GitHub Actions (PR #1 merged), `dev`/`stage` cut from `main`, `RENOVATE_TOKEN` set; `main` is red only on stale generated contract schemas (fixed in `wip/craft-i18n`); GitLab project still to archive; adopt not yet run |
| sheltrix | GitLab | Moves after storify is settled |
