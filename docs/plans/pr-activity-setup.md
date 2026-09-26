# PR activity and project setup

## Goal

Extend each PR's Discord forum post with one-way GitHub discussion and code updates. Add generic channel purpose labels, helper-specific server/channel pickers, read-only setup checks, and persistent operational status.

## Decisions

- Include humans and bot authors, comments/replies, published reviews and edits/dismissals, resolved/unresolved review threads, and synchronize updates including fork PRs.
- Preserve source authors/text/time/links. No LLM rewriting. Clearly label excerpts, edits, deletions, and bot authors; never send mentions.
- Keep one forum thread per PR. Recover/create it before activity delivery; restore archive/lock according to current PR state.
- New activity from activation onward. Persist mapping-specific baseline/checkpoints across restart and pause. Reconcile accessible missed activity every 15 minutes using bounded resumable jobs.
- Durable source identities and delivery receipts deduplicate events and reconciliation. Ambiguous sends require destination recovery; old configuration jobs keep their original binding.
- Keep existing configuration keys, six channel purposes, distinct destinations, single repository/server/helper, reporter authorship and approval, and /ask-reporter behavior.
- Generic channel labels: Feedback intake, Issue tracking, Pull requests, Commit updates, Repository updates, Support handoff (optional).
- GET /api/project-automation/discord-options: authenticated helper-specific choices, including forums, all IDs strings. Preserve unavailable saved values; retain manual ID input.
- POST /api/project-automation/check: authenticated and CSRF protected; inspect draft config without saving or public sends. Passed/Needs attention/Not verified with actionable details. Check bot/server/channels/permissions and GitHub repository/App permissions/subscriptions. Use bot event loop and bounded timeouts, secret-free outputs.
- Add accepted-webhook/completed-sync times, pending/failed/held counts and destination links to existing status. Refresh lightweight status every 30 seconds only while visible; quiet activity is not an error.

## Baseline and boundaries

Targets `staging`. Existing configuration keys, cases, reporter decisions, and destination bindings remain compatible. Shared/optional feeds, multi-project support, bidirectional replies, and reporter-flow redesign are outside this change.

## Work

- [x] GitHub event normalization, source reads, paginated reconciliation, App diagnostics.
- [x] Discord activity rendering and receipt/recovery behavior.
- [x] Service activation, reconciliation jobs, persistence, webhook/status integration.
- [x] Channel pickers, setup checks, generic labels, status UI.
- [x] Regression tests, full suite, quality/lint, dashboard auth smoke, desktop/mobile browser verification.
- [x] Operator documentation and rollout/backout instructions.

## Validation and rollout

Cover all event types, duplicate/out-of-order deliveries, edits/deletions, bot authors, long bodies, fork/force pushes, archived threads, crash/restart/pause, baseline behavior and destination changes. Test helper/guild isolation, channel types/renames/deletions, string IDs, offline/error states, permission/subscription checks, auth and CSRF. Keep native Discord tests in their separate interpreter.

Run focused tests, python tools/quality_check.py, full pytest, correctness Ruff and python tools/dashboard_smoke.py. Use synthetic data/public-write fakes for automated and browser checks. Live acceptance requires a designated test mapping. Before deployment: consistent SQLite/config backup, App subscriptions, acceptance; rollback pauses automation and restores prior code while retaining data.

## Implementation and compatibility

Reconciliation now includes updated PRs across open/closed/merged states, 100-record pages and missing-comment scans, indexed source scopes, and atomic continuations. Activation is recorded when enabled settings are saved, including offline helpers. Commit delivery uses durable head occurrences, preserves source provenance across force-pushes and delayed webhooks, and can enrich an existing reconciled update without posting another message.

Validation covers all activity types, archive restoration, exact-version recovery, reporter approval behavior, setup/auth/CSRF, source baselines, large PRs, and changed destinations. The full suite passes 832 tests and 10 subtests, with 16 existing datetime.utcnow deprecation warnings. Repository quality checks, correctness Ruff, Bandit high-severity/high-confidence scan, and the real Waitress authentication smoke test pass. Native Discord activity contracts run in their own interpreter. Browser checks cover 1280px and 390px layouts in both themes using real routes/templates with synthetic data; live acceptance remains necessary.

Before production activation, back up configuration and SQLite, add the three review-event subscriptions, and perform live acceptance in a designated test GitHub repository and Discord mapping. Rollback pauses automation and restores prior code while preserving SQLite and user configuration. See [the OCI deployment guide](../project-automation-oci.md).
