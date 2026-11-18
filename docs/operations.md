# Operations

This page covers local operations, deployment, update recovery, file layout, and troubleshooting.

## Dashboard Security

The dashboard starts automatically with `python main.py` and listens on `0.0.0.0:5000` by default. That can be reachable from other devices if the firewall or host allows it.

Set the bind address and port with environment variables rather than editing `main.py`:

```env
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=5000
```

The dashboard can read and write Discord tokens and provider API keys, so bind it to `127.0.0.1` or a private/VPN address unless the host firewalls the port. An invalid or out-of-range `DASHBOARD_PORT` logs a warning and falls back to 5000.

Set a password before exposing the dashboard outside a trusted machine or private network:

```env
DASHBOARD_USER=admin
DASHBOARD_PASS=your_secure_password
```

When `DASHBOARD_PASS` is set, dashboard pages require login and sessions persist until logout or browser close. When it is unset, authentication is disabled.

`GET /healthz` returns `{"status": "ok"}` without a login so host monitoring keeps working after you set a password. It reports nothing else. Point uptime checks at it rather than `/api/version`, which redirects to the login page once authentication is on.

Dashboard write routes use CSRF tokens. If a custom script calls dashboard APIs, send the token with the form field `csrf_token` or header `X-CSRF-Token`.

## Updates And Recovery

The dashboard updater is the normal update path. It checks GitHub releases and remote tags, backs up local state, installs dependencies, and checks that the new code imports. It restarts the bot by itself only after a clean update: one that applied, produced no warning, and installed its dependencies. Otherwise the panel under the controls lists the warnings, and you restart when ready. Its branch selector defaults to **Current**, which preserves the historical current checkout/upstream and release-tag behavior for existing installs. Choosing **Main** or **Staging** explicitly updates from that remote branch and skips the release-tag override for that run. Such an update is complete when the checkout reaches that branch's tip; a newer release tag on another branch is not an update there.

Version checks run in the background when a page needs one, at most once every 30 minutes, or every 5 minutes after a failed check, so a page load never waits for GitHub. `/api/version` reports `running_commit`, the commit the process started from, and `disk_commit`, the commit checked out now; when they differ, the bot needs a restart. The check also fetches the branch that updates follow, and `commits_behind` counts the commits on it that the checkout lacks. With **Main** or **Staging** selected, the dashboard shows those commits as an update.

If the dashboard updater is too old or reports that it is current while a newer badge is visible, run:

```bash
python update.py
```

If `update.py` is missing in an old install, download the latest `update.py` from the repository into the Discord Pals folder and run it with Python.

When an update moves the checkout to another commit, the updater first backs up local state under `bot_data/update_backups/pre-update-<timestamp>/`. An update that leaves the checkout at its commit, or is refused, takes no snapshot, and the dashboard updater deletes the snapshot of an update that it rolls back, and a snapshot whose copy failed part-way, so repeated **Update** clicks keep the last real one. When a rollback cannot restore the checkout, its snapshot stays. Backups include bot/provider config, runtime data, characters, and local prompt files. Rotated logs under `bot_data/logs/` are deliberately excluded: their own rotation already limits them by size and age, and copying them made each retained snapshot as large as the whole log budget. Three snapshots are kept. Update outcomes are recorded in `bot_data/update_log.json` without tokens or message contents. Entries from the dashboard updater record `before_head` and `after_head`, the commit the update started from and the commit it left, also for refused, rolled-back, and failed updates.

Both built-in update paths snapshot `bot_data/project_automation.sqlite3` through SQLite's backup API in one step, including committed WAL data, so writes from the running helper cannot restart the copy. A database larger than 256 MB is left out of the snapshot, and the update log records a warning; back it up separately, for example with the command in [Renaming the helper bot](project-automation.md#renaming-the-helper-bot). Keep this database with `bot_data/runtime_config.json`: it holds feedback cases, approvals, delivery receipts, and reconciliation checkpoints. For other backup tools, use SQLite's backup API or stop the service before copying the data directory. Keep an off-host copy for host-loss recovery.

The dashboard updater refuses to start, and changes nothing, when the checkout holds unresolved conflicts or an unfinished merge, rebase, cherry-pick, or revert. The message names the files or the Git command that ends the operation. Resolve or abort it, then update again. It also refuses when the update adds a file at a path where the checkout already holds a file that Git does not track, such as your own `characters/<name>.md`, which the repository ignores; Git would overwrite such a file without asking. Move or rename the named file, then update again.

Local changes to tracked files, such as an edited prompt or a deleted example character, are stashed while the checkout moves and then reapplied. When they conflict with the new commit, the update rolls back: the checkout returns to the commit it started from with the changes in place, and the page names the conflicting files. To take the update, discard those changes, for example with `git checkout -- prompts/other_prompts.md`, or move them out of tracked files, then update again. When Git cannot reapply them for another reason, such as a file that another program holds open, the update rolls back the same way and the page shows Git's message; try the update again.

After the checkout moves and the dependencies install, the updater imports `main` and `dashboard` from the new code in a separate process. When that check fails, or any other step fails after the checkout moved, the checkout returns to the previous commit, the page shows the error, and the bot keeps running. When `requirements.txt` differs between the two commits, the rollback installs the previous requirements again. That install restores only versions pinned with `==`; when it fails, the page says that the installed packages may be newer than the code expects. A rolled-back update leaves no stash entry, backup branch, or snapshot behind. The rollback waits a few seconds for a `.git/index.lock` that another Git process holds, such as `git commit` with its editor open, and never deletes such a lock. It removes a lock only when the update killed the Git command that left it, or when the lock is older than 240 seconds. If the rollback cannot restore the checkout, the page shows the commit the update started from, the stash entry that holds your changes when there is one, and the Git commands that restore them, and the bot is not restarted.

The **Restart** button runs the same import check on the code on disk and refuses with the error when it fails. It also refuses while an update runs, and while the checkout holds unresolved conflicts or an unfinished Git operation.

The dashboard's configuration ZIP export contains only `providers.json`, `bots.json`, and `autonomous.json`. It is not a full-state backup and does not include Project helper configuration or its database. Back up service-managed secrets and GitHub App private-key files separately; files outside the checkout are not captured by update snapshots.

### Release Verification

For a release, commit `version.py` and the matching dated `CHANGELOG.md` entry together, update any README version references, and verify the annotated `vX.Y.Z` tag resolves to that release commit on `main`. Check CI on the exact commit, including the dashboard smoke test and supported Python versions. A later documentation-only commit can follow the release without moving its tag or changing the version.

`bump_version.py --tag` changes the version and changelog, commits them, pushes `HEAD` to `origin/main`, and creates and pushes the tag. `--commit` also pushes to `main`; it is not a local-only commit option. With either option the helper first checks that the branch is `main` or `release/*`, unless `--allow-non-main-release` is set, and that no file other than `version.py` and `CHANGELOG.md` has changes, and it stops before writing anything when a check fails. For review before publication, use the default `--no-tag` mode, replace the generic generated changelog with release-specific categorized notes, and commit and publish manually after checks pass. Running the helper again would perform another bump. It does not create a GitHub Release; do not rerun it to check an existing release.

After deployment, sign in and check `/api/version`: `running_version` and `file_version` should match the intended release, and `running_commit` should equal `disk_commit`. For the latest release, `github_version` and `latest_version` should match too, with `update_available` and `restart_required` false. `/healthz` proves liveness only. Check `/api/status` for connected bots, and the **Health** card and **Delivery activity** on the Project page for a stopped worker, a growing queue, and failed or held work; an online helper alone does not prove synchronization is completing.

For v2.9.0, follow the [upgrade notes](../CHANGELOG.md#upgrade-notes), install the updated requirements, and restart the process. Failure dismissal uses metadata in the existing database; no manual migration is required. Older code may show dismissed failures again after rollback. For Project helper installations, run **Check setup** after updating GitHub App subscriptions and allow the three review events through any filtering proxy. Verify public ingress separately; see [Project helper deployment](project-automation-oci.md#public-webhook-endpoint).

Before rollback, record the current revision and preserve local changes and state backups. Pause Project automation and stop the service before restoring a prior reviewed revision and its dependencies. Preserve the helper database for recovery, then restart and repeat the version, authentication, and bot-status checks. A code rollback does not undo GitHub or Discord posts; use [delivery recovery](project-automation.md#recovery-and-operations) rather than replaying uncertain writes.

## Deployment

### Windows Task Scheduler

1. Open Task Scheduler with `taskschd.msc`.
2. Create a basic task named `Discord Pals Bot`.
3. Trigger it when the computer starts.
4. Start `C:\path\to\discord-pals\run.bat`.
5. In task properties, enable running whether the user is logged on or not.

To hide the console, use a small VBS wrapper:

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\path\to\discord-pals && venv\Scripts\python.exe main.py", 0
Set WshShell = Nothing
```

### Linux systemd

Run the bot as a service user that owns the installation, for example one created with `sudo useradd --system --home-dir /opt/discord-pals --shell /usr/sbin/nologin discord-pals` and `sudo chown -R discord-pals: /opt/discord-pals`. Then create `/etc/systemd/system/discord-pals.service`:

```ini
[Unit]
Description=Discord Pals Bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=discord-pals
WorkingDirectory=/opt/discord-pals
ExecStart=/opt/discord-pals/venv/bin/python main.py
Restart=always
RestartSec=10
RestartForceExitStatus=75
SuccessExitStatus=75
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/opt/discord-pals
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
```

The dashboard's **Restart** button calls neither `systemctl` nor `sudo`. It closes the bots, saves history, memories, reminders, and statistics, and then starts the code on disk. When the bot is the main process of a system service (systemd 248 or later sets `SYSTEMD_EXEC_PID` to its PID) and `systemctl show` reports that the unit starts again after exit status 75, the process exits with 75 and systemd starts the service again. That holds for `Restart=always`, for `Restart=on-failure` unless `SuccessExitStatus=` lists 75, and for any unit with `RestartForceExitStatus=75`. In every other case, including a user unit, a shell inside a terminal, `./run.sh`, or Docker, the process re-executes itself in place and keeps its PID; on Windows it starts a new process and exits. The unit above lists 75 in `RestartForceExitStatus=`, and in `SuccessExitStatus=` so that a dashboard restart is not recorded as a failure and does not run `OnFailure=` units.

Because no restart path needs root, the unit can set `NoNewPrivileges=yes`, which blocks `sudo`. `ProtectSystem=strict` makes the file system read-only outside `ReadWritePaths`; the updater writes the checkout and `venv/`, so keep both inside the installation directory. For an installation under `/home`, replace `ProtectHome=yes` with `ProtectHome=read-only` and list the installation directory in `ReadWritePaths`.

With `RestartSec=10`, systemd's default start limit of five starts in ten seconds is never reached, so the service keeps retrying through a Discord or DNS outage. On systemd 254 or later, `RestartSteps=` and `RestartMaxDelaySec=` can lengthen the wait between failed starts.

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable discord-pals
sudo systemctl start discord-pals
sudo systemctl status discord-pals
sudo journalctl -u discord-pals -f
```

`systemctl stop` and `systemctl restart` send SIGTERM. The bot handles it as a graceful shutdown and flushes conversation history, memories, reminders, and statistics before exiting, so a restart does not discard buffered state.

### Docker

Minimal `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python startup.py --init-configs

CMD ["python", "main.py"]
```

Minimal `docker-compose.yml`:

```yaml
version: "3.8"
services:
  discord-pals:
    build: .
    restart: unless-stopped
    environment:
      DISCORD_TOKEN: ${DISCORD_TOKEN}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    volumes:
      - ./bot_data:/app/bot_data
      - ./characters:/app/characters
      - ./prompts:/app/prompts
    ports:
      - "5000:5000"
```

`python startup.py --init-configs` creates starter `.env` and `providers.json` files without prompts and without writing secrets. Runtime environment variables from Docker, Compose, systemd, or a host shell can satisfy startup validation, so mounting `.env` is optional. Mount `providers.json` or `bots.json` only when you want to manage those files outside the container image.

For multi-bot Docker runs, mount or bake a `bots.json` file and add one environment variable per bot `token_env`:

```yaml
environment:
  FIREFLY_DISCORD_TOKEN: ${FIREFLY_DISCORD_TOKEN}
  NAHIDA_DISCORD_TOKEN: ${NAHIDA_DISCORD_TOKEN}
volumes:
  - ./bots.json:/app/bots.json:ro
```

Run:

```bash
docker-compose up -d
docker-compose logs -f
```

Remove the dashboard port mapping if you do not need remote dashboard access.

### VPS Notes

Recommended baseline:

- 1 GB RAM minimum; 2 GB or more for multiple bots.
- 1 vCPU.
- 10 GB storage.

For private dashboard access, prefer firewall rules, SSH tunneling, or a reverse proxy with authentication.

SSH tunnel example:

```bash
ssh -L 5000:localhost:5000 user@your-server
```

Nginx reverse proxy sketch:

```nginx
server {
    listen 443 ssl;
    server_name dashboard.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    auth_basic "Discord Pals Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Create the password file with:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd admin
```

## File Layout

```text
discord-pals/
|-- main.py                  # Entry point and multi-bot launcher
|-- bot_instance.py          # Discord event orchestration and response lifecycle
|-- coordinator.py           # Multi-bot request coordination
|-- config.py                # Provider and startup config loading
|-- runtime_config.py        # Live-adjustable runtime settings
|-- providers.py             # AI provider abstraction and fallback chain
|-- character.py             # Character Markdown parsing
|-- memory.py                # Learned memory and manual lore stores
|-- reminders.py             # Reminder scheduling and delivery state
|-- time_utils.py            # Timezones and prompt time context
|-- scopes.py                # Shared scope identifiers
|-- discord_utils.py         # Discord helpers, history, topology, and JSON helpers
|-- response_sanitizer.py    # Output cleanup and identity guard helpers
|-- request_queue.py         # Request queue and rate limiting
|-- user_ignores.py          # User ignore system
|-- security.py              # Dashboard auth and CSRF
|-- logger.py                # Logging
|-- stats.py                 # Message statistics
|-- prometheus_metrics.py    # Metrics integration
|-- project_automation*.py   # Project helper, GitHub/Discord delivery, and SQLite state
|-- startup.py               # Startup validation
|-- version.py               # Version constant
|-- diagnose.py              # Provider diagnostics
|-- bump_version.py          # Release helper
|-- update.py                # Bootstrap updater
|-- commands/                # Slash command modules
|-- characters/              # Character definitions
|-- prompts/                 # Prompt text
|-- templates/               # Dashboard HTML
|-- images/                  # Dashboard/readme images
`-- bot_data/                # Runtime state
```

## Log Files

Local logs are JSON lines under `bot_data/logs/`: the active `discord-pals.log` and its rotations `discord-pals.log.1`, `discord-pals.log.2`, and so on, newest first. The active file rotates when it reaches **Log File Size**. While **Keep Log Files** (`log_retention_days`, default 14) is above 0, it also rotates on its first write of each UTC day, and rotated files whose oldest line is older than that many days are deleted at start and then about once a day. No line then stays on disk more than one day longer than the setting. Only files with these names are deleted, and the active file is rotated first instead of deleted.

The size limit still applies: past the five newest rotated files, older ones stay only while they all fit in the space of five full files, so a busy install can lose files sooner than the setting. 0 keeps rotation by size only. Both settings are under **Config → Advanced → Debug Settings**.

## Metrics

Response times, provider API latency, memory-generation and history-save durations are recorded as Prometheus histograms during normal operation. They are not exposed unless the exporter is turned on:

```env
METRICS_ENABLED=true
METRICS_HOST=127.0.0.1
METRICS_PORT=9090
```

Metrics are then scrapeable at `http://METRICS_HOST:METRICS_PORT/metrics`. The endpoint is unauthenticated and carries no message content, but it does reveal bot names and traffic volume — keep it on localhost or a private address.

## Troubleshooting

### Token errors

Single-bot mode needs `DISCORD_TOKEN` from `.env` or the process environment. The dashboard Config page Advanced tab can update `.env`; saved tokens are never displayed and require a bot restart before they take effect.

Multi-bot mode uses the token variable names listed in `bots.json`. The Config page Advanced tab shows one token field per declared `token_env`, writes the matching `.env` variable, and never stores literal tokens in `bots.json`. In container deployments where secrets come from Compose or the host environment, update those runtime variables outside the dashboard.

If Discord rejects one bot's token, that identity stays offline while other bots and the dashboard continue running. Replace its token in **Config → Advanced → Discord Tokens**, then restart the service. The dashboard remains available even if all configured tokens are rejected. A liveness check alone does not prove any bot is connected; check each bot's online status.

### No characters available

Add `.md` files to `characters/`. The filename is the switch name; the first heading is the display name.

### Bot online but not responding

Check:

- Message Content Intent in the Discord Developer Portal.
- `server_responses_enabled` or `dm_responses_enabled`.
- Channel whitelist/blacklist and DM user blacklist.
- Whether the user has ignored that bot.
- Whether global pause or bot interaction pause is active.

### Slash commands missing

Restart the bot so commands sync again. Confirm the invite URL included `applications.commands`. Grouped commands appear as nested commands, such as `/timezone set` and `/reminders list`.

### All providers failed

Run:

```bash
python diagnose.py
```

Then check API keys, provider URLs, model names, and network access. For slow local models, raise provider `timeout`.

### Character edits not active

Use `/reload` or the dashboard reload action. Character files are cached until reloaded.

### Dashboard not accessible

Open <http://localhost:5000> on the host running the bot. For remote access, check firewall rules, port forwarding, reverse-proxy config, and dashboard password settings.

### Memories not saving

Check that `bot_data/` exists and is writable. Use the dashboard or memory commands rather than editing JSON while the bot is running.

### Bot loops or talks too much

Use `/stop` for bot-to-bot conversations, disable bot triggers on the channel, disable autonomous mode, lower `name_trigger_chance`, or turn off nickname triggers.

### Rate limits

The bot retries 429s with backoff. Persistent rate limits usually mean the provider quota is too low for current traffic. Add a fallback provider or reduce usage.

### Context seems wrong

Use `/history clear` to reset a chat. Use `/recall` to pull recent Discord messages back into context. Review `history_limit`, `immediate_message_count`, identity guard, and bot reply reference settings.

### Startup crash

Common causes:

- Invalid JSON in `providers.json`, `bots.json`, or runtime config.
- Missing environment variables.
- Python older than 3.10.
- Missing dependencies.

Run `pip install -r requirements.txt`, then `python diagnose.py`.
