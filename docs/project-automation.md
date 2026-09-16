# Project helper

Project automation connects Discord feedback to a GitHub repository. The selected character helps people explain a problem through conversation. The reporter writes the final report and decides when to post it.

## Channel behavior

| Channel | Type | Behavior |
| --- | --- | --- |
| `submit-feedback` | Forum recommended; text supported | Each report gets a conversation with the helper. The reporter writes and approves the public text after the checks pass. |
| `issue-tracker` | Forum | One post per GitHub issue. Edits and current state are synchronized; closed issues archive and lock their posts; reopening unlocks them. |
| `review-please` | Forum | One post per pull request, with distinct draft, open, closed, and merged states. |
| `commit-log` | Text | Grouped push summaries, including a link to the comparison. |
| `github` | Text | Release and workflow-run notifications. |
| `support` | Text, optional | A user explicitly runs `/feedback report:…` to start a feedback thread. Ordinary support conversations are not copied automatically. |

The configured project channels are reserved for the helper. Other character bots yield those channels. The existing global pause and channel response policy still apply.

## Feedback and personality

1. Post a problem or feature idea in `submit-feedback`, or run `/feedback report:…` in support. A rough description is enough to start.
2. Reply in the thread. The helper asks a useful question directly, in character, and helps establish whether the report belongs on GitHub. For a bug, this may include the steps, expected result, actual result, and version. Each reply builds on the conversation.
3. When ready, choose **Write my report**. Enter the title and report in your own words, then type **YES** to confirm you wrote them yourself. The helper can suggest missing details in conversation; it does not write the public report for you.
4. The helper checks the submitted report for actionable details and searches for duplicates. Tests, support requests, non-issues, incomplete reports, and unsuccessful duplicate checks cannot proceed directly to publication. Keep talking or ask a maintainer when help is needed.
5. Review the exact GitHub preview in Discord. Choose **Edit my report** to change your text or **Approve and post** to publish it. Only the original reporter can approve publication.
6. Follow the GitHub link for progress. Issue status and maintainer questions return to the feedback thread.

A maintainer can comment `/ask-reporter Which version are you using?` on a linked GitHub issue to relay a question. Continue the conversation in Discord, then write and approve your own follow-up report to post a comment on that issue. Conversation replies are never copied to GitHub automatically.

If the helper finds a matching issue, review it and choose **Add to existing issue** to prepare a comment instead of opening another issue. The comment still uses your own text and needs your approval.

The GitHub issue uses the reporter’s title. Its visible body contains only:

```text
Forwarded from Discord
@discord_username

The reporter’s own contents.
```

The username identifies the Discord reporter; it does not claim a matching GitHub account. Mention-safe formatting is applied, and an invisible recovery marker lets the helper find an uncertain delivery. No generated summary, transcript, checklist, or source appendix is added to the public body. Follow-up comments use the same format.

Approval never happens automatically. Editing a report or adding new information invalidates the earlier approval controls. Maintainers can help resolve a case and recover deliveries; they cannot substitute their approval for the reporter’s. The configurable **Clarification limit** ends repeated automated questioning with a request for human help, without approving an incomplete report.

Select an existing character under **Project → Personality**. Its persona and example dialogue shape free-form replies, follow-up questions, and status messages from the current context. Buttons and form labels remain consistent controls. Normal character-chat memories, user profiles, and unrelated conversation history are not imported into feedback cases. Attachments are recorded as links and are not read by the model in this version.

Conversation and report text go to the configured text provider for assessment. Human authorship is established by separate reporter input and explicit confirmation; it is not an AI-authorship detector. The helper does not implement fixes, merge pull requests, or promise release dates. A closed issue is reported as closed; a merged PR is reported as merged.

In **Project → Feedback cases**, inspect the reporter’s text, authorship confirmation, exact preview, next actor, and decision history. Older cases remain inspectable; a generated draft from the earlier workflow is not a confirmed human report.

## Setup

1. Add a dedicated bot identity through **Config**, using the existing bot configuration flow. Select its existing character and configure a text provider.
2. Open **Project**. Set the bot, personality, server, repository, channels, and maintainer roles/users. Save with automation paused.
3. Configure the GitHub App credential environment variables described in [OCI deployment](project-automation-oci.md). The dashboard contains variable names and presence checks, never private keys or webhook secrets.
4. Enable automation after credentials, bot permissions, channel types, and webhook delivery have been checked.

The initial repository default is `SillyBunnyTeam/SillyBunny`. One configured server/repository/helper combination is supported per Discord Pals process.

## Recovery and operations

**Project → Delivery activity** shows job IDs and failure/recovery states. Normal reads retry up to three attempts. An uncertain public write is held: a timeout or process crash does not cause another issue to be created automatically.

- `/project-recover job_id:123` checks the destination for the existing delivery. GitHub matches must bear the configured App’s attribution and the recovery marker.
- `/project-retry job_id:123` resumes failed work or checks a held delivery first.
- If a delivery remains absent, inspect the destination before using `confirmed_not_delivered:true`. A GitHub publication then returns to the reporter for fresh approval; the old job is cancelled. A Discord delivery can be retried after that explicit confirmation.

These commands require a configured maintainer or operator and the configured project server/helper. Their default Discord visibility requires Manage Server; administrators can grant command access to the maintainer role. Job and case revisions prevent a stale recovery action from replacing a newer decision.

GitHub issues and PRs reconcile on startup and every 15 minutes, including previously tracked items that closed while the webhook was unavailable. Push, release, workflow, and maintainer-question events depend on webhooks; use GitHub’s delivery history to redeliver missed events. Repository and channel changes hold older jobs for their original configuration. New mirror destinations get separate mappings.

The SQLite file is `bot_data/project_automation.sqlite3`. Keep it on persistent local disk. Both built-in update backup paths take a consistent SQLite snapshot, including committed WAL data, instead of copying live journal files. Other backups should use SQLite’s backup API or stop the service before copying its data directory. Retain backups outside the VM for recovery from host loss.

## Implementation boundaries

- `project_automation.py`: decisions, durable jobs, recovery, and lifecycle synchronization.
- `project_automation_ai.py`: character-aware conversation, assessment, and validated model responses; no public report authorship.
- `project_automation_discord.py`: persistent buttons, thread intake, commands, and mirrors.
- `project_automation_github.py`: repository-scoped GitHub App access, event parsing, and marker reconciliation.
- `project_automation_store.py`: SQLite cases, decision audit, mappings, and jobs.
- `project_automation_webhook.py`: signed webhook ingress.
- `project_automation_config.py` and `project_automation_dashboard.py`: normalized settings and operator UI.

One worker processes jobs serially and shares the existing LLM concurrency coordinator. Waiting for people consumes no model calls. The initial active-report cap is three per reporter/server. GitHub list/recovery scans have a bounded 2,000-item ceiling; exceeding it produces a visible failure requiring inspection. Discord recovery searches recent messages/posts and never treats an absent result as permission to republish.

## Verification

```bash
python tools/quality_check.py
python -m pytest -q
python tools/dashboard_smoke.py
ruff check . --exclude venv,bot_data --select E9,F821,F811,F822,F823,F632
```

On Windows, use `python -X utf8` for scripts that print status symbols. Native Discord tests run in a separate test interpreter because the legacy suite installs global SDK stubs. External writes are faked in tests; a configured test server and GitHub App are required for live acceptance.
