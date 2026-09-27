# Run protocol (every scheduled skill)

Scheduled runs start in a fresh cloud session on the product repository. Everything a run needs is in the
repository and in Issues; nothing is remembered between runs.

`PT` below means `${CLAUDE_PLUGIN_ROOT}` — the plugin's root directory (in a product repository that is
`.claude/product-team`, the copy `vendor` installs). Pass its absolute path to every subagent you start, as
`PLUGIN_ROOT=<path>` on the first line of the prompt.

GitHub access goes through `PT/scripts/backlog`, `PT/scripts/pr`, `PT/scripts/runlog` and `PT/scripts/inbox`: they
use the GitHub REST API with the session's token (`GH_TOKEN`), so the `gh` CLI is not needed and must not be
installed at run time. `git push` works through the session's own remote.

## Open
1. `git fetch --all --prune`. Read `CLAUDE.md` and `.product-team/project.yml`. If the project file is missing,
   stop: the product was never set up (run `kickoff` or `adopt`).
2. `PT/scripts/runlog start <slot>`:
   - `proceed` → keep the `run_id`.
   - `overlap` / `paused` → print the reason and end the run. No other action.
   - `pause` → the team just paused itself: tell the owner (see *Notify*) and end the run.
3. `PT/scripts/slot-context` → mode (`normal` / `burn` / `freeze`), `is_cut_day`, caps, sprint, demo date.

## Work
- Work only through the backlog adapter (`PT/scripts/backlog`), PRs and the role agents.
- Delegate: never do a role's work in the orchestrator when the role agent exists. Give each subagent only
  what it needs (issue number, criteria, branch, PLUGIN_ROOT) — QA especially must never receive dev
  transcripts.
- Respect caps from `slot-context`. P0/P1 bugs (`sev:critical`, `sev:high` with `kind:bug`) bypass caps.
- A free-tier limit, a missing secret or a paid requirement: mark the affected issue `status:blocked` with the
  reason, open a `kind:question` issue for the owner, move on.

## Close
0. `PT/scripts/inbox update` — rewrite the owner's pinned "Needs you" issue from the backlog.
1. Write the run summary to a temp file: what changed (issue/PR links), what is blocked on the owner, anything
   that failed. Owner-facing, short, in the owner's language from `project.yml`.
2. `PT/scripts/runlog finish <run_id> finished --summary-file <file> [--metric key=value]...` (or `failed` if
   the run could not do its job — a crash of one subtask that was handled is still `finished`). Duration is
   recorded automatically; add the counts the slot skill names (PRs opened, merged, blocked…).
3. Print the same summary as the session's final message.

## Notify
The owner reads GitHub notifications and the project chat on the phone. To reach them: comment on the
relevant issue (a question, a design link, the demo issue) — GitHub notifies the owner — and include the link
in the run summary. Never @-mention anyone except the repository owner.

## Never
- Push to `dev`, `stage` or `main` directly; merge without green CI and a `QA: APPROVED` verdict.
- Auto-approve a design, a scope change, a release or a cost.
- Read, print or ask for secret values.
- Force-push shared branches, rewrite history, delete branches other than merged `feature/*` / `fix/*`.
