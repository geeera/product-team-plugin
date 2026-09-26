# product-team-plugin

A Claude plugin that runs an autonomous product team — PM, Architect, Designer, Fullstack Dev, QA, DevOps,
Analyst — on scheduled cloud sessions, with the repository owner as the client.

What it does and why each choice was made: [SPEC.md](SPEC.md). Build order for the first code-mode session:

1. Plugin skeleton and the role agents (decisions 2, 10, 13).
2. Backlog adapter over GitHub Issues (decision 4).
3. `adopt`, then `slot-pm` / `slot-dev` / `slot-qa` (decisions 3, 5, 14, 15).
4. `demo-prep` / `demo-apply` and the demo page (decisions 9, 12).
5. `kickoff` and `foundation` for new products (decisions 6, 7, 8, 11).

Pilot: `geeera/storify`.
