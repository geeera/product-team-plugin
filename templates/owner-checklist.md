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

## Claude
- [ ] Scheduled routines created for `slot-pm`, `slot-dev`, `slot-qa` (see the plugin README)
- [ ] Project chat created for owner ↔ team conversation
- [ ] GitHub mobile notifications on for issues labelled `kind:question`, `design:awaiting-approval`, `team:demo`

## Budget
Budget is **$0**. Any paid plan, upgrade or domain arrives as a `kind:question` issue; nothing is bought without
your `/approve` there.
