# product-team-plugin

A Claude Code plugin that runs an autonomous product team — PM, Architect, Designer, Fullstack Dev, QA, DevOps,
Analyst — on scheduled cloud sessions, with the repository owner as the client.

What it does and why each choice was made: [SPEC.md](SPEC.md).

## Install

**In a product repository** (what scheduled cloud runs use): `kickoff` or `adopt` runs

```bash
python3 scripts/vendor install /path/to/product
```

It copies the agents to `.claude/agents/`, the skills to `.claude/skills/` and the scripts and references to
`.claude/product-team/`. Cloud sessions load project agents and skills from the clone, but never install plugins
that a repository lists in `.claude/settings.json` — hence the copy. The files are generated: fix things here,
not in the product.

**Updates reach products by themselves.** The first `slot-pm` of the day runs `vendor self-update`: when the
`stable` branch of this repository has moved, it opens a `chore: product team <version>` PR in the product with
the changelog, which merges on green CI. A product can pin a tag instead (`team.plugin_ref` in
`.product-team/project.yml`).

Routines point at the product repository only (one repository per routine) and run `/slot-pm`, `/slot-dev`,
`/slot-qa`. No setup script and no `gh` CLI are needed: the scripts call the GitHub REST API with the session's
token.

**On your machine**, for `kickoff`/`adopt` or trying things out:

```bash
claude plugin marketplace add geeera/product-team-plugin#stable
```

```bash
claude plugin install product-team@product-team
```

Turn on auto-update for the `product-team` marketplace in `/plugin` → Marketplaces (third-party marketplaces
are off by default). Inside a product repository that already has the vendored copy you do not need the
installed plugin; with both, the skills appear twice (`/slot-pm` and `/product-team:slot-pm`).

## Use

| When | Command |
| ---- | ------- |
| New product from an idea (interactive) | `/product-team:kickoff <idea>` |
| Existing GitHub repository (interactive) | `/product-team:adopt` |
| Routine, weekdays ~18:07 Kyiv | `/slot-pm` |
| Routine, Mon–Thu ~23:13 Kyiv + burn runs | `/slot-dev` |
| Routine, Tue–Fri ~04:21 Kyiv + burn runs | `/slot-qa` |

Full schedule, caps and model pinning: [reference/schedule-and-models.md](reference/schedule-and-models.md).

## Layout

| Path | What |
| ---- | ---- |
| [agents/](agents/) | Roles, each pinned to a model: `pm`, `architect`, `designer`, `fullstack-dev`, `qa`, `devops`, `analyst`, plus model variants `qa-runner` (Sonnet) and `scribe` (Haiku) |
| [skills/](skills/) | Procedures: `kickoff`, `adopt`, `foundation`, `slot-pm`, `slot-dev`, `slot-qa`, `qa-regression`, `hotfix`, `demo-prep`, `demo-apply`, `backlog` |
| [reference/](reference/) | Rules the roles share: workflow (branches, hotfix, labels, approvals, gates, budget), schedule and models, run protocol, stack contract, deploy recipes, design system, QA checklists |
| [scripts/](scripts/) | `vendor` (install into a product, self-update), `pr` (pull requests over REST), `backlog` (tracker adapter; `backlog next` is the development plan), `runlog` (run log, stop conditions, durations, `stats`), `slot-context` (normal/burn/freeze mode), `inbox` (the owner's pinned "Needs you" issue), `sprint-metrics` (plan vs shipped, cycle time, QA first-pass rate), `demo-page` (build the demo page, read decisions). Python 3.9+ stdlib only; GitHub via REST with `GH_TOKEN` |
| [templates/](templates/) | `project.yml` contract, owner checklist, CI, security, branch-guard and deploy workflows, demo page |
| [tests/](tests/) | Unit tests for the script logic |

```bash
python3 -m unittest discover -s tests -t .
```

## Releasing

1. Move the "Unreleased" notes in [CHANGELOG.md](CHANGELOG.md) under the new version; mark **Breaking** changes
   with their migration step.
2. Bump `version` in `.claude-plugin/plugin.json`.
3. Merge to `main` (CI green), tag it and move the `stable` channel — products pick it up on their next
   `slot-pm`:

```bash
git tag -a vX.Y.Z -m "product-team X.Y.Z" && git push origin vX.Y.Z && git push origin vX.Y.Z^{}:refs/heads/stable
```

## License

[MIT](LICENSE)
