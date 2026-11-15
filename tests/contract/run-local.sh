#!/usr/bin/env bash
# Exercises the plugin's console-backed scripts against a real, local team-console (team-console#436-P5):
# every job role the console knows (`apps/api/src/auth/job-roles.ts`) reading the tracker, plus a full write
# flow as the roles the route matrix allows. Each call mints its own local-job stand-in token (`Authorization:
# Bearer local-job.<base64url(JSON)>`, team-console `apps/api/src/auth/local-job.ts`) — test/local-only, honoured
# by the console only under its own local bypass (`ENVIRONMENT=local AUTH_MODE=local`).
#
# Not part of CI: the console is a sibling checkout, not something this repository's pipeline can build. Run it
# by hand before a 0.12.0-pre tag; paste its output into the PR body (team-console#436-P5 acceptance criterion).
#
# Bring up the console first (team-console checkout at tests/contract/console.lock, ports packed from
# E2E_PORT_BASE so they stay clear of the console's own e2e range):
#   git worktree add --detach <dir> "$(cat tests/contract/console.lock)"
#   cd <dir> && npm ci
#   npx nx build console
#   npx nx run api:migrate   # fresh local D1 under apps/api/.wrangler/state (wrangler d1 migrations apply --local)
#   npx wrangler dev --config apps/api/wrangler.jsonc --env dev --port "$((E2E_PORT_BASE))" \
#     --persist-to apps/api/.wrangler/state --var ENVIRONMENT:local --var AUTH_MODE:local --var GITHUB_MOCK:true \
#     --var OWNER_GITHUB_LOGIN:geeera
# then seed one tracker-cut-over project for TC_PROJECT (slug, key prefix, the plugin's builtin labels) — the
# console has no CLI for this yet (architect note on #436 §1, C3 is fixtures only), so seed it with the same
# `TrackerProjectsRepo.switchTracker` statements the console's own test kit runs (`apps/api/src/testing/
# tracker-kit.ts`), through `wrangler d1 execute --local`.
set -euo pipefail

: "${TC_API_URL:?set to the running console API base, e.g. http://127.0.0.1:26700/api/v1}"
: "${TC_PROJECT:?set to a tracker-cut-over project slug, e.g. tc}"
export TC_API_URL TC_PROJECT
export TC_CONSOLE_URL="${TC_CONSOLE_URL:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPTS="$ROOT/scripts"

# team-console apps/api/src/auth/job-roles.ts JOB_ROLES, verbatim.
ROLES=(pm architect orchestrator dev dev-light dev-heavy qa reviewer security devops analyst scribe ux-designer ui-designer)

token() {
  python3 -c '
import base64, json, os, sys
role, items = sys.argv[1], sys.argv[2]
claims = {"role": role, "project_slug": os.environ["TC_PROJECT"], "job_id": f"run-local-{role}",
          "items": [int(i) for i in items.split(",") if i]}
print("local-job." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("="))
' "$1" "${2:-}"
}

run_as() {
  local role="$1" items="$2"; shift 2
  echo "--- ${role} (items=${items:-none}): $* ---"
  TC_JOB_TOKEN="$(token "$role" "$items")" "$@"
  echo
}

echo "=== reads: every job role lists the tracker (route matrix: GET …/items admits any role) ==="
for role in "${ROLES[@]}"; do
  run_as "$role" "" python3 "$SCRIPTS/backlog" list
done

echo "=== reads: every job role reads the inbox (GET …/inbox admits a job, #444 §5.6) ==="
for role in "${ROLES[@]}"; do
  run_as "$role" "" python3 "$SCRIPTS/inbox" show
done

echo "=== writes: pm creates a proposed item (#452: a job never creates past proposed) ==="
CREATED="$(TC_JOB_TOKEN="$(token pm "")" python3 "$SCRIPTS/backlog" create --title "run-local smoke item" \
  --kind chore --body "Created by tests/contract/run-local.sh; safe to delete.")"
echo "$CREATED"
NUMBER="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["number"])' <<<"$CREATED")"

echo "=== writes: qa (a non-dev role) asks the owner a question on it ==="
run_as qa "$NUMBER" python3 "$SCRIPTS/backlog" ask "$NUMBER" --owner-category scope \
  --ask "Keep this smoke item or close it?" --commands approve,reject

echo "=== writes: pm records a team decision (a decision comment; refused for dev-light only) ==="
run_as pm "$NUMBER" python3 "$SCRIPTS/backlog" decide "$NUMBER" --body "Approved as a one-off smoke check."

echo "=== writes: dev-light adds a note (comment kind=note, allowed for every role) ==="
run_as dev-light "$NUMBER" python3 "$SCRIPTS/backlog" comment "$NUMBER" --body "Ran the console contract check."

echo "=== writes: pm adds a label ==="
run_as pm "$NUMBER" python3 "$SCRIPTS/backlog" label "$NUMBER" +tier:light

echo "=== writes: pm moves the item in the queue (sprints/queue: owner + pm only) ==="
run_as pm "$NUMBER" python3 "$SCRIPTS/backlog" queue "$NUMBER" up

echo "=== writes: pm closes the smoke item as not planned ==="
run_as pm "$NUMBER" python3 "$SCRIPTS/backlog" close "$NUMBER" --reason "run-local.sh smoke check, cleaning up"

echo "=== reads: pm's next pick no longer offers the closed item ==="
run_as pm "" python3 "$SCRIPTS/backlog" next

echo "=== done ==="
