# Project helper

Project automation connects Discord feedback to a GitHub repository. The selected character helps people explain a problem or desired improvement through conversation. The reporter writes the final report and decides when to post it.

## Channel behavior

| Purpose | Type | Behavior |
| --- | --- | --- |
| Feedback intake | Forum recommended; text supported | Each report gets a conversation with the helper. The reporter writes and approves the public text after a minimal completeness check. |
| Issue tracking | Forum | One post per GitHub issue. Edits and current state are synchronized; closed issues archive and lock their posts; reopening unlocks them. |
| Pull requests | Forum | One post per PR, with draft, open, closed, and merged states, plus PR discussion and code updates. |
| Commit updates | Text | Grouped push summaries, including a link to the comparison. |
| Repository updates | Text | Release and workflow-run notifications. |
| Support handoff (optional) | Text | A user explicitly runs `/feedback report:…` to start a feedback thread. Ordinary support conversations are not copied automatically. |

These are purpose labels; existing channel names can stay unchanged. Each purpose needs a distinct destination. The configured project channels are reserved for the helper. Other character bots yield those channels. The existing global pause and channel response policy still apply.

## Pull request activity

The helper adds GitHub activity to the PR's existing forum post:

- PR comments, code review comments and replies, including edits and deletions.
- Published reviews, edited or dismissed reviews, and resolved or reopened review threads.
- Code updates from PR synchronization, including fork branches and force pushes, with source commit/comparison links when available.

Human and bot authors are included and attributed. Messages retain source text, timestamps, and GitHub links; long text is marked as an excerpt with a link to the complete source. Edits, deletions, and bot authors are labelled. Discord mentions are disabled. No language model rewrites this activity, and Discord replies are not sent back to GitHub.

The helper creates or recovers the PR post before delivering its activity. A closed or merged PR's post returns to its archived/locked state after delivery. Source identities and delivery receipts prevent webhook redelivery and reconciliation from producing duplicate copies.

Activity starts at the mapping's first activation. Earlier discussion is not imported as a backlog, though later changes to it can appear. The activation time and reconciliation checkpoints survive restarts and pauses. Changing the repository, server, helper, or PR forum creates a separate mapping; returning to an earlier mapping retains its saved baseline.

## Feedback and personality

1. Post a problem or feature idea in the Feedback intake channel, or run `/feedback report:…` in the Support handoff channel. A rough description is enough to start.
2. Reply in the thread for help explaining the problem or idea. The helper can ask a useful question directly, in character. Add details such as steps, expected results, or a version when you have them.
3. Choose **Write my report** whenever you want to submit. Enter the title and report in your own words, then type **YES** to confirm you wrote them yourself. The helper can suggest missing details in conversation; it does not write the public report for you.
4. The helper checks that your title and body describe an understandable problem or desired improvement. Reproduction steps, logs, versions, technical experience, contributor status, and a feasibility judgment are not prerequisites. If the basic problem or improvement is unclear, use **Edit my report** and **Check my report** to try again, or cancel.
5. Review the exact GitHub preview in Discord. Choose **Edit my report** to change your text or **Approve and post** to publish it. Only the original reporter can approve publication.
6. Follow the GitHub link for progress. Issue status and maintainer questions return to the feedback thread.

A maintainer can comment `/ask-reporter Which version are you using?` on a linked GitHub issue to relay a question. Continue the conversation in Discord, then write and approve your own follow-up report to post a comment on that issue. Conversation replies are never copied to GitHub automatically.

If the helper finds a matching issue, review it and optionally choose **Add to existing issue** to prepare a comment. You can also approve the new issue preview. The comment still uses your own text and needs your approval. Duplicate suggestions, an unavailable duplicate search, and the helper's advice about report type or readiness do not block a complete report.

If the report check fails because the text provider is unavailable or returns an invalid response, your report stays saved. Use **Check my report** to retry or **Edit my report** to change it. A failed check does not approve publication.

The GitHub issue uses the reporter’s title. Its visible body contains only:

```text
Forwarded from Discord
@discord_username

The reporter’s own contents.
```

The username identifies the Discord reporter; it does not claim a matching GitHub account. Mention-safe formatting is applied, and an invisible recovery marker lets the helper find an uncertain delivery. No generated summary, transcript, checklist, or source appendix is added to the public body. Follow-up comments use the same format.

Approval never happens automatically. Editing a report or adding new information invalidates the earlier approval controls. Only the original reporter can write, check, or approve their report; submission requires no maintainer role or handoff. Maintainers retain delivery recovery access. The configurable **Clarification limit** stops repeated automated questions while keeping the reporter's controls available. It never approves an incomplete report.

Select an existing character under **Project → Personality**. Its persona and example dialogue shape free-form replies, follow-up questions, and status messages from the current context. Buttons and form labels remain consistent controls. Normal character-chat memories, user profiles, and unrelated conversation history are not imported into feedback cases. Attachments are recorded as links and are not read by the model in this version.

Personality selects a character file; it does not create or switch Discord bot credentials. Keep the connected helper selected under **Dedicated helper bot** and choose any installed character under **Personality**. A separate bot identity is needed only when you want a different Discord account to own the workflow, not when changing its voice.

Conversation and report text go to the configured text provider for assessment. Human authorship is established by separate reporter input and explicit confirmation; it is not an AI-authorship detector. The helper does not implement fixes, merge pull requests, or promise release dates. A closed issue is reported as closed; a merged PR is reported as merged.

In **Project → Feedback cases**, inspect the reporter’s text, authorship confirmation, exact preview, next actor, and decision history. Older cases waiting for a maintainer return to reporter controls when the helper reconnects. A generated draft from the earlier workflow is not a confirmed human report.

## Setup

1. Add a dedicated bot identity through **Config**, using the existing bot configuration flow. Select its existing character and configure a text provider.
2. Open **Project**. Select the helper and a server it has joined, then choose the channels for each purpose, personality, repository, and maintainer roles/users for delivery recovery. Channel choices come only from that helper and server. Issue tracking and Pull requests require forums; the other selectors show their supported types.
3. Configure the GitHub App credential environment variables described in [OCI deployment](project-automation-oci.md). The dashboard contains variable names and presence checks, never private keys or webhook secrets.
4. Choose **Check setup**. It checks the current draft without saving it or posting to Discord/GitHub. Results identify required settings, helper/server access, channel types and effective permissions, GitHub repository access, App permissions, and event subscriptions. **Passed**, **Needs attention**, and **Not verified** are separate outcomes; unavailable data and timeouts are not a pass.
5. Resolve actionable results and save with automation paused. If the helper is offline or a channel is no longer available, its selected ID stays visible. **Advanced: enter Discord IDs manually** supports setup before the helper connects. Re-run checks after changing the draft.
6. Enable automation when ready, then verify a signed webhook delivery and the live acceptance cases. Setup checks cannot prove webhook reachability, Developer Portal intent grants, future permissions, or a successful public delivery.

The initial repository default is `SillyBunnyTeam/SillyBunny`. One configured server/repository/helper combination is supported per Discord Pals process.

## Recovery and operations

The Project page shows the last accepted webhook, completed sync, and delivered PR activity for the current mapping, plus pending, failed, and held job counts. Status refreshes every 30 seconds while the page is visible. A quiet repository can have old or missing timestamps without an error. **Refresh activity** also reloads feedback cases and delivery rows.

**Project → Delivery activity** shows job IDs, failure/recovery states, and recorded GitHub/Discord destinations. When a delivery receipt exists, PR activity links open its exact Discord message. Normal reads retry up to three attempts. An uncertain public write is held: a timeout or process crash does not cause another issue or activity message to be created automatically.

Use **Recent** for delivery history, **Needs attention** for undismissed failures and held work, and **Dismissed** for failures you have already reviewed. **Load older** retrieves another page without losing the loaded rows. **Dismiss** hides one failed job; **Dismiss visible failures** handles up to 100 loaded failures at a time and states the exact count. Dismissal preserves the error, attempts, payload, and audit history. It does not retry, cancel, delete, or mark a delivery successful.

Use **Restore** in Dismissed to return a failure to the normal views and failed count. A retried job automatically returns to normal visibility, so a new failure is not hidden by an earlier dismissal. Jobs held for recovery cannot be dismissed because a public write may already exist. If work changes while you are reviewing it, refresh before dismissing or restoring.

**Discard** deletes one held job and its saved delivery version. Check the destination first: the delivery is not retried, and a public write may already exist. A held report publication cannot be discarded, because its report stays in recovery until `/project-retry` returns it to the reporter.

- `/project-recover job_id:123` checks the destination for the existing delivery. GitHub matches must bear the configured App's attribution and recovery marker; Discord mirrors must match the saved destination, bot author, and delivery marker. An inaccessible destination stays held and is never treated as proof of absence.
- `/project-retry job_id:123` resumes failed work or checks a held delivery first.
- If a delivery remains absent, inspect the destination before using `confirmed_not_delivered:true`. A GitHub publication then returns to the reporter for fresh approval; the old job is cancelled. A Discord delivery can be retried after that explicit confirmation.

These commands require a configured maintainer or operator and the configured project server/helper. Their default Discord visibility requires Manage Server; administrators can grant command access to the maintainer role. Job and case revisions prevent a stale recovery action from replacing a newer decision.

GitHub issues, PRs, and accessible PR activity reconcile on startup and then at most every 15 minutes. A new cycle starts only when no job of the previous cycle is still queued, running, or sending; failed and held jobs do not delay it. An issue or PR that closed more than 7 days ago is no longer refreshed by reconciliation, and it returns to scope if it reopens. PR discovery includes recently updated open, closed, and merged PRs, so discussion can be recovered even when a PR opened and closed during an outage. PR activity scans use bounded, resumable jobs. They recover the source's available current state, not every intermediate edit; deletion checks can only recover comments the helper previously observed. Repository push feeds, release, workflow, and maintainer-question events still depend on webhooks; use GitHub’s delivery history to redeliver missed events. Repository and channel changes hold older jobs for their original configuration. New mirror destinations get separate mappings.

Unchanged issue status does not enqueue another reporter notification. Status checkpoints and notifications are saved together, including when older completed notification jobs use the previous payload format. A permission error can happen after a Discord post was created or edited; restore access and verify the destination before retrying those jobs.

The SQLite file is `bot_data/project_automation.sqlite3`. Keep it on persistent local disk. Both built-in update backup paths take a consistent SQLite snapshot, including committed WAL data, instead of copying live journal files. Other backups should use SQLite’s backup API or stop the service before copying its data directory. Retain backups outside the VM for recovery from host loss.

### Job history and Project data

Project jobs are working data. A sweep deletes them at most every 10 minutes, also while automation is paused, in short transactions of at most 500 rows. When more than 20,000 rows are due at once, it deletes them all in one transaction instead, which holds the database while it runs. **Helper setup → Job history** sets how many days finished jobs stay (0 to 365, default 0).

| Data | Kept |
| --- | --- |
| Finished jobs | Until the next sweep, or for the Job history days. A reconciliation cycle keeps its finished jobs while any of its jobs is still queued, running, or sending. |
| Failed jobs, dismissed or not, and cancelled publications | 7 days after their last change, or the Job history days when that is longer. A failed report decision or publication stays until `/project-retry` resumes it, because its report cannot continue without it. |
| Queued, running, sending, and held jobs | Until they finish or you remove them. The sweep never deletes them. |
| Duplicate-delivery markers | 4 days. A marker holds only a SHA-256 of the GitHub delivery ID. A delivery received again within that time is answered as accepted and queues nothing; GitHub offers redelivery for 3 days. |
| Release, workflow, and commit post records | 7 days, or the Job history days when that is longer. A later update to the same release or workflow run then posts a new message instead of editing the old one. |
| Issue and PR posts, PR activity receipts, feedback cases, and notification receipts | Until **Reset Project data**. |

**Project → Project data** has two controls:

- **Clear history and queue** deletes every job except jobs in progress and the feedback steps that open reports still need: queued, failed, and held report checks, decisions, publications, and notices stay, so every report can still continue or be retried. Other queued work never runs, and other held deliveries are not checked again. Posts, receipts, feedback cases, and duplicate-delivery markers stay. It works while automation runs or is paused.
- **Reset Project data** deletes every job, post record, receipt, and feedback case. It is available only while automation is paused and no job is in progress, and you confirm it by typing the repository name. Afterwards every open issue and PR is posted again as new, and the old Discord posts stay in their channels.

After a clear or reset, the database file is compacted when at least 25% of it, and at least 16 MB, is free. Compaction switches the file to incremental auto-vacuum, so later sweeps return freed space to the disk. If another process holds the database, a later sweep compacts it instead.

Operators without dashboard access can run the same functions from the checkout, with the service's Python environment:

```bash
python tools/project_data.py status
python tools/project_data.py clear-history
python tools/project_data.py reset --confirm OWNER/REPOSITORY
python tools/project_data.py compact
```

`reset` refuses while automation is enabled, while a job is in progress, and when `--confirm` does not match the configured repository. The tool prints counts only.

#### First start after upgrading

The first start of a version with job history runs a one-time cleanup before the worker takes any job. It deletes the queued reconciliation work left by overlapping cycles: sync cycle starts, PR discovery, page, missing-comment, and check jobs, and issue and PR refreshes. Queued webhook deliveries, detected PR activity deliveries, feedback jobs, and held jobs stay. It then applies the table above to every remaining job, including failed and cancelled jobs older than 7 days, and compacts the file when enough space is free. It logs one summary line with the counts.

On a large database the cleanup can hold the database for a minute or more. Webhooks that arrive meanwhile are refused with 503; redeliver them from GitHub's delivery history. A bot that connects during the cleanup finishes starting, including its slash-command sync, after the cleanup ends. Back up the database before updating, as described above.

## Implementation boundaries

- `project_automation.py`: decisions, durable jobs, recovery, and lifecycle synchronization.
- `project_automation_ai.py`: character-aware conversation, assessment, and validated model responses; no public report authorship.
- `project_automation_discord.py`: persistent buttons, thread intake, commands, and mirrors.
- `project_automation_activity.py` and `project_automation_discord_activity.py`: PR activity checkpoints, delivery orchestration, formatting, and receipts.
- `project_automation_github.py`: repository-scoped GitHub App access, event parsing, and marker reconciliation.
- `project_automation_github_activity.py`: PR activity normalization, source reads, and GitHub setup diagnostics.
- `project_automation_store.py`: SQLite cases, decision audit, mappings, and jobs.
- `project_automation_webhook.py`: signed webhook ingress.
- `project_automation_config.py`, `project_automation_setup.py`, and `project_automation_dashboard.py`: normalized settings, read-only setup checks, and operator UI.

One worker processes jobs serially and shares the existing LLM concurrency coordinator. Waiting for people consumes no model calls. The initial active-report cap is three per reporter/server. PR discovery, discussion, and missing-comment scans process at most 100 records per job and persist continuation work. Legacy issue listing and publication-recovery scans retain their 2,000-item ceiling; exceeding it produces a visible failure requiring inspection. Discord recovery searches recent messages/posts and never treats an absent result as permission to republish.

## Verification

```bash
python tools/quality_check.py
python -m pytest -q
python tools/dashboard_smoke.py
ruff check . --exclude venv,bot_data --select E9,F821,F811,F822,F823,F632
```

On Windows, use `python -X utf8` for scripts that print status symbols. Native Discord tests run in a separate test interpreter because the legacy suite installs global SDK stubs. External writes are faked in tests; a configured test server and GitHub App are required for live acceptance.
