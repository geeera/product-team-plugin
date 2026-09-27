# Schedule, caps and models

Source: SPEC decisions 2, 3, 13. Timezone: **Europe/Kyiv**. `scripts/slot-context` computes the current mode;
skills read it instead of re-deriving the calendar.

## Slots

| Local time (jittered) | Days | Skill | Mode |
| --------------------- | ---- | ----- | ---- |
| 18:07 | Mon–Fri | `slot-pm` | read owner answers, plan, prepare designs |
| 23:13 | Mon–Thu | `slot-dev` | development (the one heavy run) |
| 04:21 | Tue–Fri | `slot-qa` | QA, fixes, morning summary |
| 23:13 Fri → 19:00 Sun | burn | `slot-dev` at Fri 23:13, Sat 10:37, Sat 23:13, Sun 10:37 · `slot-qa` at Sat 04:21, Sun 04:21, Sun 17:07 · deep audit inside `slot-qa` at Sat 16:43 | burn |

On **freeze days** (the two days before the sprint's due date, after `stage` was cut) `slot-dev` runs
`qa-regression` on `stage` instead of feature development.

The weekly quota resets on **Sunday 20:00 Kyiv**; the burn window uses what is left of it. Runs are jittered
off the hour to avoid the top-of-hour load spike. Cron in routines is usually UTC — Kyiv is UTC+3 in summer
and UTC+2 in winter, so re-check routine times at DST changes; `slot-context` always reports the true local
mode, so a run that fires an hour off still behaves correctly.

## Caps

| Mode | Dev tasks per run | Parallel dev subagents | Dev model |
| ---- | ----------------- | ---------------------- | --------- |
| normal (Mon–Thu) | 2 | 2 | `fullstack-dev` (Opus 5.5) |
| burn (Fri 23:00 – Sun 19:00) | 5 | 3 | `fullstack-dev` (Opus 5.5) |
| freeze | 0 features, fixes only | 1 | `fullstack-dev` (Opus 5.5) |

Development runs on Opus in every mode (owner's decision, 2026-09-27). The weekday caps stay conservative so the
weekly quota still lasts until the burn window; watch `runlog stats` and lower `caps` in `project.yml` if runs start
failing on the usage limit mid-week.

P0/P1 bugs and release blockers do not count against the cap. The plan itself comes from `scripts/backlog next`
(`scripts/ptlib/picker.py`): the order and the caps are code with tests, not a judgement call per run.

## Models (pinned IDs, never aliases)

| Model | ID | Used by |
| ----- | -- | ------- |
| Fable 5.1 | `claude-fable-5-1` | `architect`; `kickoff`, `foundation`, ADRs |
| Opus 5.5 | `claude-opus-5-5` | orchestrator (`slot-*`, `adopt`, `demo-*`), `pm`, `ux-designer`, `ui-designer`, `qa`, `security`, `fullstack-dev` |
| Sonnet 5 | `claude-sonnet-5` | `reviewer`, `devops`, `analyst`, `qa-runner` (regression) |
| Haiku 4.5 | `claude-haiku-4-5-20251001` | `scribe`: labels, changelog, summary formatting |

Security reviews are never delegated to Fable (its extra cyber safeguards cause refusals) — they go to
`security` on Opus. Project-specific agents (`agent:<name>` issues) use whatever model their own file pins.

Role variants exist only to pin a second model to the same role: `qa-runner` is `qa` on Sonnet for mechanical
regression runs, `scribe` is the Haiku formatter.

## Stop conditions

Every run writes a `started` entry to the run log and closes it with `finished`/`failed`
(`scripts/runlog`). A run that dies on the usage limit leaves a dangling `started`, which counts as failed.

- **3 failed runs in a row** → the next run sets the run log to `paused`, notifies the owner and does no work.
  Work resumes when the owner comments `/resume` on the run-log issue.
- A run that finds the previous run of the same slot still `started` less than 3 hours ago exits
  immediately (overlap guard).
- All state is in the repository and in Issues, so the next run resumes where the failed one stopped.
