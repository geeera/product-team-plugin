# Owner checklist

One-time setup only the owner can do. Agents never see secret values; they reference `secrets.NAME` in
workflows. Tick an item by editing this file in a PR, or comment `/approve` on the linked question issue.

## Accounts (free tiers only)
- [ ] GitHub repository `OWNER/REPO` exists, default branch `dev`; `main`, `stage`, `dev` branches exist
- [ ] Hosting account(s): <!-- e.g. Vercel / Cloudflare Pages / Render / Fly.io — filled at kickoff -->
- [ ] Database account: <!-- e.g. Neon / Supabase -->

## Secrets (Settings → Secrets and variables → Actions)
| Secret | What for | Where to create it | Done |
| ------ | -------- | ------------------ | ---- |
<!-- rows added by devops; one row per secret -->

## Security hardening (recommended before the first release)

Out of the box every agent acts as your GitHub account: the merge gate and your `/approve`, `/go` comments are
conventions an agent could imitate (the inbox shows a standing "Security setup" item until this is done).

1. **Reviewing account.** Create a GitHub machine account (one free machine account per person is allowed).
   Give it write access to this repository, no admin. Create a fine-grained token for it: this repository only,
   Pull requests read/write, Contents read, Issues read. Put its login in `team.reviewer_logins` in
   `.product-team/project.yml` — from then on only its verdicts count in `scripts/pr gate`.
2. **Separate cloud environment for reviews.** In claude.ai/code create an environment `reviewers` with the
   variable `PT_REVIEW_TOKEN` = that token, and point the `slot-qa` routine at it. Keep `slot-pm` and `slot-dev`
   in the default environment, which has no reviewer token — so a developer agent cannot post a counted verdict.
   (Every role inside one session shares that session's tokens; separation only works between environments.)
3. **Your own commands.** While the agents' GitHub identity is your account, an agent can write a comment that
   looks like yours; the team therefore only takes a release **go** from the demo page or from a comment older
   than the current run. Full separation needs the agents on their own identity as well.
4. **Server-side enforcement (costs money or visibility).** Rulesets that require a review from the reviewing
   account are not available for private repositories on GitHub's free plan. Options: GitHub Pro (paid — needs
   your `/approve` on the budget question) or making the repository public. Until then `branch-guard.yml`
   reports, after the fact, any change that reached `dev`, `stage` or `main` without a merged PR.

## Claude
- [ ] Scheduled routines created for `slot-pm`, `slot-dev`, `slot-qa` (see the plugin README)
- [ ] Project chat created for owner ↔ team conversation
- [ ] GitHub mobile notifications on for issues labelled `kind:question`, `design:awaiting-approval`, `team:demo`

## Budget
Budget is **$0**. Any paid plan, upgrade or domain arrives as a `kind:question` issue; nothing is bought without
your `/approve` there.
