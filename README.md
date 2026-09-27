# product-team-plugin

A Claude Code plugin that runs an autonomous product team — PM, Architect, Designer, Fullstack Dev, QA, DevOps,
Analyst — on scheduled cloud sessions, with the repository owner as the client.

What it does and why each choice was made: [SPEC.md](SPEC.md).

## Install

```bash
claude plugin marketplace add geeera/product-team-plugin
```

```bash
claude plugin install product-team@product-team
```

For scheduled cloud runs the product repository itself must enable the plugin: copy
[templates/claude-settings.json](templates/claude-settings.json) into the product repo's `.claude/settings.json`
(`kickoff` and `adopt` do this). It pins a release tag, so a change here reaches a product only when that
product's `devops` bumps the tag through a PR — see [CHANGELOG.md](CHANGELOG.md).

## Use

| When | Command |
| ---- | ------- |
| New product from an idea (interactive) | `/product-team:kickoff <idea>` |
| Existing GitHub repository (interactive) | `/product-team:adopt` |
| Routine, weekdays ~18:07 Kyiv | `/product-team:slot-pm` |
| Routine, Mon–Thu ~23:13 Kyiv + burn runs | `/product-team:slot-dev` |
| Routine, Tue–Fri ~04:21 Kyiv + burn runs | `/product-team:slot-qa` |

Full schedule, caps and model pinning: [reference/schedule-and-models.md](reference/schedule-and-models.md).

## Layout

| Path | What |
| ---- | ---- |
| [agents/](agents/) | Roles, each pinned to a model: `pm`, `architect`, `designer`, `fullstack-dev`, `qa`, `devops`, `analyst`, plus model variants `fullstack-dev-senior` (Opus), `qa-runner` (Sonnet), `scribe` (Haiku) |
| [skills/](skills/) | Procedures: `kickoff`, `adopt`, `foundation`, `slot-pm`, `slot-dev`, `slot-qa`, `qa-regression`, `hotfix`, `demo-prep`, `demo-apply`, `backlog` |
| [reference/](reference/) | Rules the roles share: workflow (branches, hotfix, labels, approvals, gates, budget), schedule and models, run protocol, stack contract, deploy recipes, design system, QA checklists |
| [scripts/](scripts/) | `backlog` (tracker adapter; `backlog next` is the development plan), `runlog` (run log, stop conditions, durations, `stats`), `slot-context` (normal/burn/freeze mode), `inbox` (the owner's pinned "Needs you" issue), `sprint-metrics` (plan vs shipped, cycle time, QA first-pass rate), `demo-page` (build the demo page, read decisions). Python 3.9+ stdlib + `gh` |
| [templates/](templates/) | `project.yml` contract, owner checklist, CI, security, branch-guard and deploy workflows, demo page, `.claude/settings.json` |
| [tests/](tests/) | Unit tests for the script logic |

```bash
python3 -m unittest discover -s tests -t .
```

## Releasing

1. Move the "Unreleased" notes in [CHANGELOG.md](CHANGELOG.md) under the new version; mark **Breaking** changes
   with their migration step.
2. Bump `version` in `.claude-plugin/plugin.json` and the `ref` in `templates/claude-settings.json`.
3. Merge to `main` (CI green), then tag it: `git tag vX.Y.Z && git push origin vX.Y.Z`.

## License

[MIT](LICENSE)
