# Project automation with a character voice

## Goal

Implement Discord-native, LLM-assisted feedback intake with durable human choices and GitHub synchronization. Cover submit-feedback, issue-tracker, review-please, commit-log, github, and an explicit support-to-feedback handoff. Personality is required: use a selectable existing character for conversational replies, while structured facts, publication drafts, permissions, and actions remain workflow-owned.

## Architecture

- Existing provider gateway, character loader, dashboard, Discord clients, and concurrency coordinator are reusable.
- Existing request queue is transient; project cases, pending gates, webhooks, and deliveries require durable storage.
- One configured repository, server, and dedicated helper identity per process. The character is selectable, including an existing Firefly entry. The feature defaults to disabled.

## Decisions

- Python, existing runtime, SQLite on local persistent disk, bounded background worker.
- New `project_automation_*` modules own the feature; small hooks in existing entrypoints.
- `runtime_config.project_automation` is one validated configuration object, managed through a new dashboard section.
- Human gate: model proposes; reporter/authorized maintainer chooses; only then does the selected branch execute. Pending gates never auto-approve.
- Personality shapes conversational text; issue/comment drafts are technical and previewed exactly before submission.
- Use GitHub App credentials from environment variables / a private key file. Secrets never appear in dashboard responses or generated reports.
- Persist case revisions and atomically consume approvals with their outbound job. A stale button or recovery action cannot approve or replace a newer decision.
- Hold uncertain public writes for recovery. Reconcile against the destination before permitting another attempt; GitHub publication returns to a fresh human approval when a maintainer confirms the delivery is absent.
- Reconcile issue and PR state on startup and periodically. Keep event-only notifications recoverable through GitHub webhook redelivery.

## Steps

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
