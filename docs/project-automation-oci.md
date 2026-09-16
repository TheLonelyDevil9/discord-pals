# Deploying the project helper on OCI

This feature extends the existing Discord Pals service. A separate Discord bot token gives the project helper its own identity; its process can share the current runtime and provider configuration.

## Infrastructure

| Component | Location |
| --- | --- |
| Discord Gateway client | Existing Python process on the OCI VM |
| Feedback worker | Same process, one job at a time |
| Durable cases/jobs | SQLite on persistent local disk |
| LLM inference | Existing configured provider, reached over HTTPS |
| GitHub ingress | HTTPS reverse proxy to the existing Flask/Waitress webhook route |
| Operator dashboard | Existing private access path |

The E2.1.Micro has 1 GB RAM and a fractional CPU allocation. This design adds no local model, Redis, PostgreSQL, or container stack. Pilot with the current provider and observe memory, disk space, queue delay, and provider spend before increasing traffic. The design targets a small community; it has not been load-tested on the live VM.

## Discord application

Create a dedicated application/bot, such as **SillyBunny Helper**, and add its token using Discord Pals’ Config page. Select the existing Firefly character entry. Do not overwrite existing bot identities or character files.

Invite it with `bot` and `applications.commands` scopes. In the intended channels, grant:

- View Channels and Read Message History.
- Send Messages, Send Messages in Threads, and Embed Links.
- Create Public Threads and Manage Threads, for creating, reopening, archiving, and locking project posts.
- Attach Files, for complete long reports submitted through `/feedback`.

For forum channels, **Send Messages** permits creating posts; **Create Public Threads** alone does not. Check channel overrides as well as the bot role, especially when a forum is read-only for `@everyone`.

Enable Message Content Intent for ordinary feedback posts. The existing Discord Pals client also requests Server Members Intent, so enable that for this application. Administrator permission is not required. Ensure the configured maintainer role/users can see feedback threads and the project commands.

Use forum channels for `issue-tracker` and `review-please`; forum or text for feedback; text channels for commit/general feeds. The channel IDs come from Discord Developer Mode → Copy Channel ID. Keep the IDs as strings.

## GitHub App

Create an App and install it for **SillyBunnyTeam/SillyBunny**. Use repository permissions:

| Permission | Access | Purpose |
| --- | --- | --- |
| Metadata | Read | Repository identity |
| Issues | Read and write | Search/read issues, create approved issues/comments |
| Pull requests | Read | PR state and merge information |
| Contents | Read | Push notifications |
| Actions | Read | Workflow-run notifications |

Subscribe to **Issues**, **Issue comment**, **Pull request**, **Push**, **Release**, and **Workflow run** events. Configure the App’s webhook URL as `https://YOUR_WEBHOOK_HOST/webhooks/github`. Set a strong random webhook secret and retain its value in the service environment. Enter the App ID and installation ID in Project setup.

Place the generated private key in a file readable only by the service account. Configure one of:

- `PROJECT_GITHUB_PRIVATE_KEY_FILE`: absolute path to that PEM file, recommended.
- `PROJECT_GITHUB_PRIVATE_KEY`: PEM text, if the hosting secret mechanism supports it.

Also configure `PROJECT_GITHUB_WEBHOOK_SECRET`. Use the service’s environment/secret management path; do not put these values in the repository, issues, logs, or screenshots. The dashboard supports different variable names if needed.

## Public webhook endpoint

Use a DNS hostname pointing to the VM and an HTTPS proxy. If Caddy is used, the relevant site block is:

```caddyfile
YOUR_WEBHOOK_HOST {
    @github_hook path /webhooks/github
    handle @github_hook {
        reverse_proxy 127.0.0.1:YOUR_DASHBOARD_PORT
    }
    handle {
        respond "Not found" 404
    }
}
```

Replace the host and local port with verified values. Keep the existing dashboard private; this public hostname forwards only the exact webhook path. Standard Caddy certificate issuance needs the DNS record and appropriate inbound HTTP/HTTPS access. Update the OCI security list/NSG and host firewall for the chosen proxy, while retaining the existing SSH/private administration path. A public webhook route still requires a valid GitHub HMAC signature.

The bot needs outbound HTTPS to Discord, GitHub, and its text provider. No new public database or Discord listener port is needed. A tunnel with a stable HTTPS hostname can replace the reverse proxy if already operated on this host.

## Installation and activation

Install a reviewed Discord Pals revision containing the project helper. Schedule a maintenance window after preparing the bot, App, channel mapping, and hostname:

1. Inspect the deployment’s current Git status and record its commit. Preserve existing local changes, including personal character files.
2. Back up `.env`, `bots.json`, `providers.json`, `characters`, `prompts`, and `bot_data` using the existing backup flow. Save a copy outside the VM.
3. Stop the existing service and update the checkout through the normal deployment process, preserving local changes. For a systemd deployment this is commonly `discord-pals.service`; verify the service name on your host.
4. Install requirements using the service's actual virtual environment:

   ```bash
   /path/to/venv/bin/python -m pip install -r requirements.txt
   ```

5. Configure the GitHub secret environment and private-key file. Start the service. Keep **Enable project automation** off during configuration.
6. Open **Project**, select the dedicated helper and Firefly entry, and save all channel/maintainer IDs. Verify the bot is online and can use the intended channels.
7. Enable automation and send/redeliver GitHub’s test delivery. Valid events should receive `202`; a valid ping receives `200`. Paused or incomplete setup returns `503`; GitHub delivery history is the recovery path for those events.
8. Run the acceptance checks below before using normal community traffic.

The implementation adds `PyJWT[crypto]` for GitHub App signing. Other components reuse the existing dependencies.

## Live acceptance

- Submit a vague report. Verify the character asks a useful question directly and the reporter can answer in the same thread.
- Submit an explicit test or a support question. Verify the helper explains the appropriate next step and offers no direct publication approval.
- Write a complete report through **Write my report** and confirm authorship. Verify its title and body stay human-written through assessment and preview.
- Restart with a report awaiting approval. Verify its current controls still work and do not approve themselves.
- Try another user, a maintainer acting as approver, and an old button. Verify only the original reporter can approve the current report.
- Approve once. Verify exactly one GitHub issue, the minimal forwarded body, a link back in Discord, and the Discord issue mirror.
- Suggest an existing issue or interrupt the duplicate search. Verify a new issue cannot bypass the duplicate check.
- Close/reopen an issue and close/merge separate PRs. Verify accurate labels, archived/locked posts, and reopened access.
- Redeliver the same webhook. Verify no extra forum post or commit message.
- Run `/ask-reporter` as a maintainer on a linked issue. Verify the question is relayed in character and any public response still requires the reporter’s own text and approval.
- Pause automation. Verify queued work waits and pending human choices remain saved.

Use a test server and an App installation on an appropriate test repository before enabling the production mapping if the team requires a staging pass. Automated tests use fakes and do not prove live credentials or Discord permissions.

## Rollback

Pause automation first. Stop the service and restore the recorded deployment revision through the normal deployment process, retaining local user changes. Restore the dependencies required by that revision and restart the service. Preserve the SQLite database and backups; removing the feature's code does not require deleting feedback history.

Primary references: [Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding), [Discord threads](https://docs.discord.com/developers/topics/threads), [GitHub webhook practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks), [GitHub App permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app), [SQLite backup API](https://www.sqlite.org/backup.html), [Caddy HTTPS](https://caddyserver.com/docs/automatic-https), and [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
