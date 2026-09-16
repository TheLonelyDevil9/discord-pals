# Project automation with a character voice

## Goal

Implement Discord-native, LLM-assisted feedback intake with durable human choices and GitHub synchronization. Cover submit-feedback, issue-tracker, review-please, commit-log, github, and an explicit support-to-feedback handoff. Personality is required: use a selectable existing character for conversational replies, while structured facts, publication drafts, permissions, and actions remain workflow-owned.

## Architecture

- Existing provider gateway, character loader, dashboard, Discord clients, and concurrency coordinator are reusable.
- Existing request queue is transient; project cases, pending gates, webhooks, and deliveries require durable storage.
- One configured repository, server, and dedicated helper identity per process. The character is selectable, including an existing Firefly entry. The feature defaults to disabled.

## Initial implementation decisions

- Python, existing runtime, SQLite on local persistent disk, bounded background worker.
- New `project_automation_*` modules own the feature; small hooks in existing entrypoints.
- `runtime_config.project_automation` is one validated configuration object, managed through a new dashboard section.
- Human gate: model proposes; reporter/authorized maintainer chooses; only then does the selected branch execute. Pending gates never auto-approve.
- Personality shapes conversational text; issue/comment drafts are technical and previewed exactly before submission.
- Use GitHub App credentials from environment variables / a private key file. Secrets never appear in dashboard responses or generated reports.
- Persist case revisions and atomically consume approvals with their outbound job. A stale button or recovery action cannot approve or replace a newer decision.
- Hold uncertain public writes for recovery. Reconcile against the destination before permitting another attempt; GitHub publication returns to a fresh human approval when a maintainer confirms the delivery is absent.
- Reconcile issue and PR state on startup and periodically. Keep event-only notifications recoverable through GitHub webhook redelivery.

## Initial implementation steps

- [x] Configuration parsing and dashboard controls.
- [x] Durable SQLite cases, decisions, webhook inbox, and outbound jobs.
- [x] GitHub App client and deterministic event rendering.
- [x] Character-aware assessment and draft generation.
- [x] Persistent Discord choices, feedback ingestion, and support handoff.
- [x] Issue/PR forum lifecycle, commit feed, general feed, and reporter updates.
- [x] Authenticated dashboard management and signed webhook ingress.
- [x] Focused restart, isolation, duplicate-delivery, persona, and publication-gate tests.
- [x] Configuration and recovery dashboard, including mobile layouts.
- [x] Operator documentation and OCI deployment instructions.

## Validation

Tests cover restart persistence, stale approvals, unauthorized actors, intake limits, duplicate webhooks, uncertain deliveries, recovery transactions, persona isolation, configuration validation, and consistent SQLite backups. Native Discord tests run in a separate interpreter to avoid the legacy suite's global SDK stubs.

Use synthetic reports and isolated data for automated and dashboard checks. Public writes use fakes in tests. Live acceptance requires a configured Discord server and GitHub App; follow [OCI deployment](../project-automation-oci.md) before enabling community traffic. See [Project helper](../project-automation.md) for commands and recovery behavior.

## Conversational feedback redesign

### Goals and decisions

- A new Discord report starts an ordinary thread conversation. The helper asks the next useful question directly instead of making the reporter choose a branch before each question.
- Every conversational reply uses the configured character and current context. Structured workflow events stay internal; generated dialogue explains the next action.
- The reporter enters their own title and report in a Discord form and explicitly confirms authorship. Assessment output contains no generated issue title or body.
- Publication requires an actionable bug or feature report, enough detail, a successful duplicate check, an exact preview, and the original reporter’s approval. Explicit tests, support requests, and non-issues do not enter direct publication.
- The visible GitHub body is `Forwarded from Discord`, the Discord username, and the reporter’s contents. Preserve mention handling and the invisible recovery marker.
- Reporters control their public text and publication. Maintainers help with unresolved questions and recovery. The clarification limit requests human help; it never grants approval.
- Preserve saved cases, stale-button rejection, restart recovery, authentication, CSRF protection, and the other channel automations.

### Work and validation

- [x] Replace branch-first assessment with conversational clarification and eligibility checks.
- [x] Collect and validate reporter-written reports and reporter-only publication approval.
- [x] Generate character replies for workflow messages and preserve exact human public text.
- [x] Update dashboard report inspection, authorship, next-step ownership, and labels.
- [x] Update usage, setup, and live acceptance documentation; remove the outdated dashboard preview from the guide.
- [x] Verify focused workflow, native Discord, AI, and recovery regressions.
- [x] Verify dashboard behavior, authentication, CSRF, and rendered desktop/mobile states.
- [x] Complete integration checks and independent review before publication.

The checkboxes above track this redesign separately from the initial implementation. Dashboard verification passed 31 focused tests, the quality checks, and a synthetic browser pass at desktop and mobile widths. That pass covered reporter/preview title priority, human authorship, legacy cases, literal untrusted text, and exclusion of internal workflow notices. Automated checks use synthetic cases and fake public writes; live Discord/GitHub acceptance remains a deployment step.
