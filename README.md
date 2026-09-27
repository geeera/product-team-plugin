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
(`kickoff` and `adopt` do this).

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
| [skills/](skills/) | Procedures: `kickoff`, `adopt`, `foundation`, `slot-pm`, `slot-dev`, `slot-qa`, `qa-regression`, `demo-prep`, `demo-apply`, `backlog` |
| [reference/](reference/) | Rules the roles share: workflow (branches, labels, approvals, gates, budget), schedule and models, run protocol, stack contract, design system, QA checklists |
| [scripts/](scripts/) | `backlog` (tracker adapter over GitHub Issues), `runlog` (run log and stop conditions), `slot-context` (normal/burn/freeze mode), `demo-page` (build the demo page, read decisions). Python 3.9+ stdlib + `gh` |
| [templates/](templates/) | `project.yml` contract, owner checklist, CI and security workflows, demo page, `.claude/settings.json` |
| [tests/](tests/) | Unit tests for the script logic |

```bash
python3 -m unittest discover -s tests -t .
```

## License

[MIT](LICENSE)
