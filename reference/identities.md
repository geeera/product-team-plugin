# Identities: the agents on their own GitHub Apps

Out of the box the agents act as the owner's GitHub account (**same-account mode**). That works, but two things
only hold by convention:

- a verdict in the merge gate (`QA:`, `REVIEW:`, `SECURITY:`) is a comment by the same account that wrote the code,
  so the gate cannot tell a reviewer's approval from a developer approving itself;
- an owner command (`/approve`, `/go`, …) is a comment by the owner's account, so an agent could write one.

Two GitHub Apps give the agents identities of their own. GitHub then records who wrote what, and the scripts check it.

## The two apps

| App | Used by | Writes as | What it unlocks |
| --- | ------- | --------- | --------------- |
| **Team app** | every script and every push (`gh.token()`, `pr push`) | `<team-slug>[bot]` | owner commands count only from the owner's login; commits, issues and PRs are visibly the team's; GitHub notifies the owner about the team's comments |
| **Review app** | `pr review` (verdicts) | `<review-slug>[bot]` | `pr gate` / `pr merge` count only verdicts by this bot |

Permissions (repository permissions; everything else "No access"; Metadata read-only is added by GitHub):

| Permission | Team app | Review app |
| ---------- | -------- | ---------- |
| Contents | Read and write | Read |
| Issues | Read and write | Read and write |
| Pull requests | Read and write | Read and write |
| Workflows | Read and write | — |
| Actions | Read and write | Read |
| Checks | Read | Read |
| Commit statuses | Read | Read |

Workflows write lets the team change `.github/workflows/` by PR; Actions write lets it pause and trigger workflows
(`scripts/workflows`). The scripts use the review app only for review comments today; its Issues write keeps a
reviewer able to comment its findings on the issue.

## Create them (owner, once per product, ~10 minutes)

For each app: GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.

1. Name: e.g. `<product>-team` and `<product>-review` (the slug becomes the bot login `<slug>[bot]`). Homepage URL:
   the repository URL.
2. Webhook: untick **Active** — nothing listens for events.
3. Repository permissions: the table above. Where can it be installed: **Only on this account**.
4. Create, note the **App ID** (About section), then **Generate a private key** — a `.pem` file downloads.
5. **Install App** → your account → **Only select repositories** → the product repository. Nothing else: a token
   is scoped to the one repository anyway, but an installation on other repositories is a standing grant.
6. Put the review bot's login in `.product-team/project.yml`, so sessions without the review key still know which
   verdicts count:

   ```yaml
   team:
     reviewer_logins: ['<product>-review[bot]']   # quote it: [bot] is YAML flow syntax
   ```

## Environment contract

| Variable | Meaning |
| -------- | ------- |
| `PT_TEAM_APP_ID` | team app id (numeric) |
| `PT_TEAM_APP_KEY_FILE` **or** `PT_TEAM_APP_KEY` | path to its `.pem`, **or** the PEM itself — base64 (one line, for cloud env vars) or raw |
| `PT_REVIEW_APP_ID` | review app id; must differ from the team app |
| `PT_REVIEW_APP_KEY_FILE` **or** `PT_REVIEW_APP_KEY` | as above, for the review app |

Precedence: `gh.token()` uses the team app when `PT_TEAM_APP_ID` is set, otherwise `GH_TOKEN`, `GITHUB_TOKEN`,
`gh auth token` as before. `gh.review_token()` uses the review app, otherwise `PT_REVIEW_TOKEN` (a reviewing machine
account), otherwise none (verdicts go out with the team token). A half-configured app (id without key, bad key, app
not installed) is an error, never a silent fallback to the owner's token.

The app's JWT is signed with the `openssl` CLI (present in macOS and the cloud images). An inline key is written
to a `0600` temp file for the one signature and deleted. Installation tokens live an hour; a script mints one on
first use and reuses it until five minutes before it expires. Scripts never print key material or tokens.

## Local setup

```bash
mkdir -p ~/.config/product-team && chmod 700 ~/.config/product-team
mv ~/Downloads/<product>-team.*.pem ~/.config/product-team/<product>-team.pem
chmod 600 ~/.config/product-team/*.pem
export PT_TEAM_APP_ID=123456
export PT_TEAM_APP_KEY_FILE=~/.config/product-team/<product>-team.pem
```

Keep the review key out of your everyday shell; set it only where reviews run. Keep `gh auth login` as yourself:
the team chat uses your own token to record your answers (below).

## Cloud setup (claude.ai/code environments)

Environment variables are single-line, so give the key as base64:

```bash
base64 < <product>-team.pem | tr -d '\n'     # paste the output as PT_TEAM_APP_KEY
```

- **Default environment** (routines `slot-pm`, `slot-dev`): `PT_TEAM_APP_ID` + `PT_TEAM_APP_KEY`. No review app, no
  personal token.
- **`reviewers` environment** (routine `slot-qa`): the team app **and** `PT_REVIEW_APP_ID` + `PT_REVIEW_APP_KEY`.
  Only this environment can post a verdict that counts.
- **Team-chat environment** (if the team chat runs in the cloud): the team app plus `GH_TOKEN` = a fine-grained
  token of **your** account for this repository (Issues read/write) — `backlog answer` posts your answers with it.
  Never put your own token into the scheduled environments: an agent there could write as you again.

Delete the downloaded `.pem` files once they are stored; rotate a key by generating a new one in the app's
settings and deleting the old one there.

## What changes for the team

- **Owner commands** count only when your login wrote them (`commands.parse`, the demo decisions block, reversals
  of team decisions). A bot comment that quotes you is never read as your answer. `backlog answers` reports
  `same_account: false` and the agents' login; the same-run caution in `slot-pm` and `demo-apply` applies only
  while `same_account` is true.
- **`backlog answer`** (team chat) posts with your own token (`gh auth token` / `GH_TOKEN`) and refuses when that
  token is not yours — otherwise the answer would be the bot's and would not count.
- **Verdicts**: with the review app configured, `pr gate` counts only `<review-slug>[bot]`; the login list in
  `project.yml` cannot widen that. Without the key in a session it falls back to `team.reviewer_logins`.
- **Pushing**: `scripts/pr push [--branch B]` pushes as the team app. The token reaches git through `GIT_CONFIG_*`
  environment variables — not argv, not `.git/config`, not the output — and the session's credential helper is
  switched off for that call, so a rejected app token fails instead of pushing as you. It never forces and never
  pushes `dev`, `stage` or `main`. Without a team app it is a plain `git push -u origin B`.
- **Commit authorship**: `eval "$(scripts/pr git-identity)" && git commit …` authors the commit as
  `<slug>[bot] <id+slug[bot]@users.noreply.github.com>`, linked to the bot on GitHub. It must be in the same shell
  command as the commit (each agent command starts a fresh shell). Without a team app it prints nothing.
- Pushes by an app token trigger workflows (unlike Actions' `GITHUB_TOKEN`), so CI runs on the team's PRs as before.
- The run log accepts entries from the log's author and from the team bot, so a log opened before the switch keeps
  its history. The inbox's standing "Security setup" item disappears once the agents no longer act as you.

## What this does and does not isolate

It does:
- make the author of every comment, review, commit and push visible and checkable — self-approval and imitated owner
  commands stop counting;
- keep your personal token out of the scheduled environments entirely;
- confine a leaked installation token to one repository and one hour.

It does not:
- separate roles **inside one session**. Every agent in a session sees the same environment variables, so in the
  `reviewers` environment a developer agent started by `slot-qa` could read the review key and post a verdict.
  Separation is between environments, not between subagents (SPEC: identity limits);
- protect the app keys from anyone who can edit the cloud environment or read your machine;
- enforce anything server-side. Rulesets that require the review bot's approval need GitHub Pro or a public
  repository; `branch-guard.yml` still reports changes that reached `dev`, `stage` or `main` without a merged PR.

## Troubleshooting

| Error says | Fix |
| ---------- | --- |
| `openssl` command is not installed | install OpenSSL/LibreSSL, or unset `PT_*_APP_ID` |
| neither a PEM private key nor base64 of one | re-encode with `base64 < key.pem \| tr -d '\n'` |
| openssl could not sign | the file is not the app's RSA private key |
| GitHub rejected the JWT | wrong app id for this key, or the clock is off by more than a minute (`date -u`) |
| is not installed on owner/repo | app settings → Install App → add the repository |
| may not get a token | the installation does not include the repository, or a permission is missing |
| an owner answer must be posted with …'s own token | `gh auth login` as yourself, or answer on GitHub |
