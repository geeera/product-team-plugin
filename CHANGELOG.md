# Changelog

Products follow the `stable` channel (or a pinned tag, `team.plugin_ref` in `.product-team/project.yml`): the
first `slot-pm` of the day runs `vendor self-update` and opens a PR with the entries in between. Breaking changes (a renamed label, a changed script contract, a new required
`project.yml` key) are marked **Breaking** with the migration step.

## 0.3.0

- **Cloud runs load the team from the product repository.** `scripts/vendor install` copies agents, skills,
  scripts and references into the product's `.claude/`; cloud sessions load project agents and skills from the
  clone but never install plugins listed in `.claude/settings.json`. `vendor self-update` (first `slot-pm` of the
  day) opens a PR when the `stable` channel moves, so products pick up releases by themselves.
- **No `gh` CLI needed.** Scripts talk to the GitHub REST API with `GH_TOKEN`; new `scripts/pr` covers create,
  view, checks, diff, review, update-branch and merge (merge refuses unless CI passes).
- **Breaking** — in products the skills are `/slot-pm`, `/slot-dev`, `/slot-qa` (project skills), not
  `/product-team:…`. Migration: run `vendor install .` in the product, delete the `product-team` entries from its
  `.claude/settings.json`, point each routine at the product repository only with the new command.
- **Breaking** — `templates/claude-settings.json` is gone (it never worked for cloud runs).

## 0.2.0

- `backlog next`: the development plan is code with tests (`scripts/ptlib/picker.py`) — production defects,
  P0/P1 and release blockers first, then QA rework, then sprint work up to the cap; `needs:*`, unapproved
  designs and missing architect notes are skipped with a reason.
- `hotfix` skill: P0/P1 in production goes `hotfix/*` → `main`, independent QA, back-merge to `stage` and
  `dev`; rollback is a PR or a manual deploy of the previous ref.
- `inbox`: one pinned "Needs you" issue, rewritten by every run.
- Run log records duration and per-run metrics; `runlog stats` and `sprint-metrics` feed the demo.
- Templates: `branch-guard.yml` (an issue for the owner when a protected branch moves without a PR),
  `deploy.yml` (per-environment deploy, rollback trigger, smoke check); `reference/deploy-recipes.md`.
- PRs are brought up to date with their base before QA; conflicts in generated files are regenerated.
- New labels: `architect-note`, `qa:changes-requested`, `needs:local`, `needs:owner`, `in-production`,
  `team:inbox`, `team:guard`. Run `backlog init` once in each product to create them.
- The plugin repository has CI: unit tests and static checks of agents, skills and manifests.

## 0.1.0

- First release: role agents, slot skills, backlog adapter, run log, slot context, demo page.
